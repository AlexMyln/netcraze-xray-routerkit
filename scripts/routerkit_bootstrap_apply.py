#!/usr/bin/env python3
"""Transactional standalone RouterKit bootstrap apply implementation."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import selectors
import signal
import stat
import subprocess
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from routerkit_artifact_network import ArtifactNetworkError, download_pinned_archive


REQUIRED_PACKAGES = (
    "ca-bundle",
    "curl",
    "unzip",
    "coreutils-sha256sum",
    "python3",
)
OPKG_RELATIVE_CANDIDATES = (Path("bin/opkg"), Path("sbin/opkg"))
STATE_RELATIVE_PATH = Path("var/lib/routerkit/bootstrap-state.json")
BACKUP_RELATIVE_DIR = Path("var/lib/routerkit/backups")
STAGING_RELATIVE_DIR = Path("var/tmp/routerkit")
TARGET_RELATIVE_PATH = Path("sbin/xray")
STATE_SCHEMA_VERSION = 1
MAX_PROCESS_OUTPUT = 64 * 1024
PROCESS_TIMEOUT = 30.0
VERSION_TIMEOUT = 10.0
MAX_BINARY_BYTES = 128 * 1024 * 1024
MAX_ZIP_ENTRIES = 128
MAX_XRAY_MEMBER_BYTES = 96 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_RECEIPT_BYTES = 64 * 1024


class BootstrapApplyError(Exception):
    """A generic apply failure safe for operator output."""

    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class BootstrapRollbackError(BootstrapApplyError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=3)


class BootstrapTermination(BaseException):
    def __init__(self, signum: int, prior_exit_code: Optional[int] = None) -> None:
        super().__init__(signum)
        self.signum = signum
        self.prior_exit_code = prior_exit_code


def termination_exit_code(exc: BootstrapTermination) -> int:
    if exc.prior_exit_code not in (None, 0):
        return int(exc.prior_exit_code)
    return 128 + exc.signum


@dataclass
class TransactionResult:
    mode: str = "apply"
    environment: str = "Linux arm64 /opt"
    packages_already_installed: List[str] = field(default_factory=list)
    packages_installed: List[str] = field(default_factory=list)
    artifact_release: Optional[str] = None
    archive_sha256_verified: bool = False
    candidate_version: Optional[str] = None
    existing_binary_present: bool = False
    existing_binary_hash: Optional[str] = None
    backup_created_or_reused: Optional[str] = None
    backup_path: Optional[str] = None
    replacement_performed: bool = False
    post_install_verified: bool = False
    rollback_attempted: bool = False
    rollback_verified: bool = False
    idempotent_noop: bool = False
    residual_risks: List[str] = field(
        default_factory=lambda: [
            "newly installed Entware packages are additive and are not automatically removed",
            "SIGKILL, power loss, kernel failure, or host crash can prevent in-process cleanup",
            "no service restart or autostart action is performed",
        ]
    )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "environment": self.environment,
            "packages_already_installed": list(self.packages_already_installed),
            "packages_installed": list(self.packages_installed),
            "artifact_release": self.artifact_release,
            "archive_sha256_verified": self.archive_sha256_verified,
            "candidate_version": self.candidate_version,
            "existing_binary_present": self.existing_binary_present,
            "existing_binary_hash": self.existing_binary_hash,
            "backup_created_or_reused": self.backup_created_or_reused,
            "backup_path": self.backup_path,
            "replacement_performed": self.replacement_performed,
            "post_install_verified": self.post_install_verified,
            "rollback_attempted": self.rollback_attempted,
            "rollback_verified": self.rollback_verified,
            "idempotent_noop": self.idempotent_noop,
            "residual_risks": list(self.residual_risks),
        }


@dataclass(frozen=True)
class OpkgHandle:
    path: Path
    resolved_path: Path
    identity: Tuple[int, int]


@dataclass(frozen=True)
class BinaryRecord:
    path: Path
    sha256: str
    version: Optional[str]
    identity: Tuple[int, int]
    size: int


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class BootstrapSignalLifecycle:
    """Scoped catchable-signal ownership for mutable bootstrap work."""

    graceful_timeout = 2.0
    forced_timeout = 2.0

    def __init__(self) -> None:
        self.active_child: Optional[subprocess.Popen] = None
        self.requested_signum: Optional[int] = None
        self._previous_handlers: Dict[int, object] = {}
        self._phase = "inactive"

    @staticmethod
    def handled_signals() -> Tuple[int, ...]:
        values = []
        for name in ("SIGTERM", "SIGHUP"):
            value = getattr(signal, name, None)
            if value is not None and value not in values:
                values.append(value)
        return tuple(values)

    def install(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            raise BootstrapApplyError("Signal-aware bootstrap must run in the main thread.")
        try:
            for signum in self.handled_signals():
                self._previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, self._handle_signal)
        except (OSError, RuntimeError, ValueError):
            self.restore()
            raise BootstrapApplyError("Bootstrap signal handlers could not be installed.") from None
        self._phase = "active"

    def restore(self) -> None:
        self._phase = "restoring"
        for signum, previous in self._previous_handlers.items():
            signal.signal(signum, previous)
        self._previous_handlers.clear()
        self._phase = "inactive"

    def _handle_signal(self, signum: int, _frame: Optional[FrameType]) -> None:
        if self.requested_signum is None:
            self.requested_signum = signum
        if self._phase == "active":
            self._phase = "terminating"
            raise BootstrapTermination(signum)

    def child_started(self, child: subprocess.Popen) -> None:
        self.active_child = child
        self._phase = "active"
        self.raise_if_requested()

    def child_reaped(self) -> None:
        self.active_child = None
        self._phase = "active"

    def begin_cleanup(self) -> None:
        self._phase = "cleanup"

    def raise_if_requested(self, prior_exit_code: Optional[int] = None) -> None:
        if self.requested_signum is not None:
            self._phase = "terminating"
            raise BootstrapTermination(self.requested_signum, prior_exit_code)

    def shutdown_active_child(self) -> None:
        child = self.active_child
        if child is None:
            return
        try:
            _signal_child(child, force=False)
            try:
                child.wait(timeout=self.graceful_timeout)
            except subprocess.TimeoutExpired:
                _signal_child(child, force=True)
                child.wait(timeout=self.forced_timeout)
        finally:
            if child.poll() is not None:
                self.active_child = None


def _signal_child(child: subprocess.Popen, *, force: bool) -> None:
    if child.poll() is not None:
        return
    signum = signal.SIGKILL if force else signal.SIGTERM
    if os.name == "posix":
        try:
            if child.pid != os.getpgrp():
                os.killpg(child.pid, signum)
                return
        except (OSError, ProcessLookupError):
            pass
    if force:
        child.kill()
    else:
        child.terminate()


def sanitized_environment(target_root: Path, home: Path) -> Dict[str, str]:
    return {
        "PATH": "{}:{}".format(target_root / "bin", target_root / "sbin"),
        "HOME": str(home),
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
    }


def run_bounded_process(
    command: Sequence[str],
    *,
    timeout: float,
    cwd: Path,
    env: Mapping[str, str],
    lifecycle: Optional[BootstrapSignalLifecycle] = None,
    maximum_output: int = MAX_PROCESS_OUTPUT,
) -> ProcessResult:
    """Run one shell-free child with process-group, time, and output bounds."""

    child = None
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    try:
        child = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd),
            env=dict(env),
            start_new_session=os.name == "posix",
        )
        if lifecycle is not None:
            lifecycle.child_started(child)
        assert child.stdout is not None and child.stderr is not None
        selector.register(child.stdout, selectors.EVENT_READ, "stdout")
        selector.register(child.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BootstrapApplyError("Bootstrap subprocess timed out.")
            events = selector.select(min(remaining, 0.25))
            if lifecycle is not None:
                lifecycle.raise_if_requested()
            if not events and child.poll() is not None:
                events = [(key, selectors.EVENT_READ) for key in selector.get_map().values()]
            for key, _ in events:
                try:
                    chunk = os.read(key.fileobj.fileno(), 8192)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffers[key.data].extend(chunk)
                if len(buffers["stdout"]) + len(buffers["stderr"]) > maximum_output:
                    raise BootstrapApplyError("Bootstrap subprocess output exceeded its limit.")
        remaining = max(0.0, deadline - time.monotonic())
        try:
            returncode = child.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise BootstrapApplyError("Bootstrap subprocess timed out.") from None
        if lifecycle is not None:
            lifecycle.child_reaped()
            lifecycle.raise_if_requested(returncode if returncode != 0 else None)
        return ProcessResult(returncode, bytes(buffers["stdout"]), bytes(buffers["stderr"]))
    except BootstrapTermination:
        if lifecycle is not None:
            lifecycle.shutdown_active_child()
        elif child is not None:
            _signal_child(child, force=True)
            child.wait()
        raise
    except BaseException:
        if child is not None and child.poll() is None:
            _signal_child(child, force=True)
            try:
                child.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass
        if lifecycle is not None and child is not None and child.poll() is not None:
            lifecycle.child_reaped()
        raise
    finally:
        selector.close()
        if child is not None:
            for stream in (child.stdout, child.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_existing_directory(path: Path, *, description: str) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        raise BootstrapApplyError("{} is unavailable.".format(description)) from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise BootstrapApplyError("{} must be a non-symlink directory.".format(description))


def _directory_chain(root: Path, path: Path) -> List[Path]:
    root = Path(root)
    path = Path(path)
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise BootstrapApplyError("Bootstrap directory escapes the target root.") from None
    return [root.joinpath(*relative.parts[:index]) for index in range(1, len(relative.parts) + 1)]


def _validate_directory_chain(root: Path, path: Path) -> Optional[Path]:
    _safe_existing_directory(root, description="Target root")
    last_existing = root
    for item in _directory_chain(root, path):
        try:
            metadata = item.lstat()
        except FileNotFoundError:
            return last_existing
        except OSError:
            raise BootstrapApplyError("A required target directory is unavailable.") from None
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise BootstrapApplyError(
                "A required target directory must not traverse a symlink."
            )
        last_existing = item
    return None


def _ensure_directory(path: Path, *, root: Path, mode: int = 0o700) -> None:
    _validate_directory_chain(root, path)
    for item in _directory_chain(root, path):
        try:
            metadata = item.lstat()
        except FileNotFoundError:
            metadata = None
        except OSError:
            raise BootstrapApplyError("Bootstrap directory is unavailable.") from None
        if metadata is not None:
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise BootstrapApplyError("Bootstrap directory path is unsafe.")
            continue
        try:
            os.mkdir(item, mode)
        except FileExistsError:
            pass
        _safe_existing_directory(item, description="Bootstrap directory")
    if os.name == "posix":
        metadata = path.lstat()
        if metadata.st_uid != os.geteuid():
            raise BootstrapApplyError("A private RouterKit directory has an unexpected owner.")
        os.chmod(path, mode)


def validate_apply_environment(target_root: Path, *, create: bool = False) -> None:
    target_root = Path(target_root)
    _safe_existing_directory(target_root, description="Target root")
    if not os.access(str(target_root), os.W_OK | os.X_OK):
        raise BootstrapApplyError("Target root is not writable by the current process.")
    for relative in (Path("sbin"), STAGING_RELATIVE_DIR, STATE_RELATIVE_PATH.parent):
        path = target_root / relative
        missing_parent = _validate_directory_chain(target_root, path)
        if missing_parent is None:
            if create and relative in (STAGING_RELATIVE_DIR, STATE_RELATIVE_PATH.parent):
                _ensure_directory(path, root=target_root, mode=0o700)
            if not os.access(str(path), os.W_OK | os.X_OK):
                raise BootstrapApplyError("A required target directory is not writable.")
        elif create:
            _ensure_directory(path, root=target_root)
        else:
            if not os.access(str(missing_parent), os.W_OK | os.X_OK):
                raise BootstrapApplyError("A required target directory cannot be created safely.")


def validate_existing_target_metadata(target_root: Path) -> None:
    target = Path(target_root) / TARGET_RELATIVE_PATH
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        return
    except OSError:
        raise BootstrapApplyError("Existing Xray could not be inspected safely.") from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise BootstrapApplyError("Existing Xray must be a regular non-symlink file.")
    if not os.access(str(target), os.X_OK):
        raise BootstrapApplyError("Existing Xray must be executable.")


def resolve_opkg(target_root: Path) -> OpkgHandle:
    root = Path(target_root).resolve()
    for relative in OPKG_RELATIVE_CANDIDATES:
        candidate = Path(target_root) / relative
        try:
            before = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise BootstrapApplyError("Entware opkg could not be inspected safely.") from None
        try:
            resolved = candidate.resolve(strict=True)
            after = candidate.lstat()
            resolved_metadata = resolved.stat()
        except OSError:
            raise BootstrapApplyError("Entware opkg could not be inspected safely.") from None
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise BootstrapApplyError("Entware opkg changed during validation.")
        if not _is_within(resolved, root):
            raise BootstrapApplyError("Entware opkg resolves outside the target root.")
        if not stat.S_ISREG(resolved_metadata.st_mode) or not os.access(str(resolved), os.X_OK):
            raise BootstrapApplyError("Entware opkg must be a regular executable file.")
        return OpkgHandle(
            path=candidate,
            resolved_path=resolved,
            identity=(resolved_metadata.st_dev, resolved_metadata.st_ino),
        )
    raise BootstrapApplyError("A trusted /opt-scoped Entware opkg executable was not found.")


def _revalidate_opkg(handle: OpkgHandle, target_root: Path) -> None:
    current = resolve_opkg(target_root)
    if current.path != handle.path or current.identity != handle.identity:
        raise BootstrapApplyError("Entware opkg changed during the transaction.")


def _package_is_installed(
    handle: OpkgHandle,
    package: str,
    *,
    target_root: Path,
    lifecycle: BootstrapSignalLifecycle,
    runner: Callable[..., ProcessResult],
) -> bool:
    _revalidate_opkg(handle, target_root)
    result = runner(
        [str(handle.path), "status", package],
        timeout=PROCESS_TIMEOUT,
        cwd=target_root,
        env=sanitized_environment(target_root, target_root),
        lifecycle=lifecycle,
    )
    return result.returncode == 0 and b"Status: install ok installed" in result.stdout


def ensure_required_packages(
    target_root: Path,
    handle: OpkgHandle,
    *,
    lifecycle: BootstrapSignalLifecycle,
    runner: Callable[..., ProcessResult] = run_bounded_process,
) -> Tuple[List[str], List[str]]:
    present = []
    missing = []
    for package in REQUIRED_PACKAGES:
        if _package_is_installed(
            handle,
            package,
            target_root=target_root,
            lifecycle=lifecycle,
            runner=runner,
        ):
            present.append(package)
        else:
            missing.append(package)
    if missing:
        _revalidate_opkg(handle, target_root)
        completed = runner(
            [str(handle.path), "install"] + missing,
            timeout=PROCESS_TIMEOUT,
            cwd=target_root,
            env=sanitized_environment(target_root, target_root),
            lifecycle=lifecycle,
        )
        if completed.returncode != 0:
            raise BootstrapApplyError(
                "Required Entware package installation failed; attempted package additions may remain: {}.".format(
                    ", ".join(missing)
                )
            )
    for package in REQUIRED_PACKAGES:
        if not _package_is_installed(
            handle,
            package,
            target_root=target_root,
            lifecycle=lifecycle,
            runner=runner,
        ):
            raise BootstrapApplyError(
                "A required Entware package is still missing; attempted package additions may remain: {}.".format(
                    ", ".join(missing) or "none"
                )
            )
    return present, missing


def _hash_open_fd(fd: int, *, maximum: int = MAX_BINARY_BYTES) -> Tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    while count <= maximum:
        chunk = os.read(fd, min(65536, maximum + 1 - count))
        if not chunk:
            break
        count += len(chunk)
        if count > maximum:
            raise BootstrapApplyError("Executable exceeds the supported size limit.")
        digest.update(chunk)
    return digest.hexdigest(), count


def _expected_version(tag: str) -> str:
    if not isinstance(tag, str) or not tag.startswith("v") or len(tag) < 2:
        raise BootstrapApplyError("Pinned release tag is invalid.")
    return "Xray " + tag[1:]


def validate_version_output(output: bytes, expected: str) -> str:
    try:
        text = output.decode("utf-8")
    except UnicodeDecodeError:
        raise BootstrapApplyError("Xray version output is invalid.") from None
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not first or any(not char.isprintable() for char in first) or first != expected:
        raise BootstrapApplyError("Xray version does not match the pinned release.")
    return first


def probe_exact_version(
    path: Path,
    expected: str,
    *,
    target_root: Path,
    cwd: Path,
    lifecycle: BootstrapSignalLifecycle,
    runner: Callable[..., ProcessResult] = run_bounded_process,
) -> str:
    completed = runner(
        [str(path), "version"],
        timeout=VERSION_TIMEOUT,
        cwd=cwd,
        env=sanitized_environment(target_root, cwd),
        lifecycle=lifecycle,
    )
    if completed.returncode != 0:
        raise BootstrapApplyError("Xray version command failed.")
    return validate_version_output(completed.stdout, expected)


def inspect_binary(
    path: Path,
    *,
    target_root: Path,
    lifecycle: BootstrapSignalLifecycle,
    runner: Callable[..., ProcessResult] = run_bounded_process,
) -> Optional[BinaryRecord]:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise BootstrapApplyError("Existing Xray could not be inspected.") from None
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise BootstrapApplyError("Existing Xray must be a regular non-symlink file.")
    if not os.access(str(path), os.X_OK):
        raise BootstrapApplyError("Existing Xray must be executable.")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = -1
    try:
        fd = os.open(path, flags)
        opened = os.fstat(fd)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise BootstrapApplyError("Existing Xray changed during inspection.")
        digest, count = _hash_open_fd(fd)
        final = os.fstat(fd)
        current = path.lstat()
        if (
            (opened.st_dev, opened.st_ino) != (final.st_dev, final.st_ino)
            or (final.st_dev, final.st_ino) != (current.st_dev, current.st_ino)
            or final.st_size != opened.st_size
            or final.st_mtime_ns != opened.st_mtime_ns
        ):
            raise BootstrapApplyError("Existing Xray changed during inspection.")
    except BootstrapApplyError:
        raise
    except OSError:
        raise BootstrapApplyError("Existing Xray could not be inspected.") from None
    finally:
        if fd >= 0:
            os.close(fd)
    version = None
    try:
        completed = runner(
            [str(path), "version"],
            timeout=VERSION_TIMEOUT,
            cwd=path.parent,
            env=sanitized_environment(target_root, path.parent),
            lifecycle=lifecycle,
        )
        if completed.returncode == 0:
            text = completed.stdout.decode("utf-8", errors="strict")
            first = next((line.strip() for line in text.splitlines() if line.strip()), "")
            if first.startswith("Xray ") and len(first) <= 200 and all(
                char.isprintable() for char in first
            ):
                version = first
    except (BootstrapApplyError, UnicodeDecodeError):
        version = None
    return BinaryRecord(path, digest, version, (before.st_dev, before.st_ino), count)


def _normalized_member_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or name.startswith("/"):
        raise BootstrapApplyError("Xray archive contains an unsafe member name.")
    pieces = name.split("/")
    if ".." in pieces or any("\x00" in piece for piece in pieces):
        raise BootstrapApplyError("Xray archive contains an unsafe member name.")
    return "/".join(piece for piece in pieces if piece not in ("", "."))


def extract_xray_candidate(archive_path: Path, candidate_path: Path) -> str:
    """Extract only one normalized root xray member to an exclusive 0600 file."""

    created = False
    completed = False
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = archive.infolist()
            if len(members) > MAX_ZIP_ENTRIES:
                raise BootstrapApplyError("Xray archive has too many entries.")
            candidates = []
            normalized_seen = set()
            for info in members:
                normalized = _normalized_member_name(info.filename)
                if normalized in normalized_seen:
                    raise BootstrapApplyError("Xray archive contains duplicate member names.")
                normalized_seen.add(normalized)
                if info.flag_bits & 0x1:
                    raise BootstrapApplyError("Encrypted Xray archive entries are not supported.")
                if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise BootstrapApplyError("Xray archive compression method is not supported.")
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                file_type = stat.S_IFMT(unix_mode)
                if info.is_dir() or file_type not in (0, stat.S_IFREG):
                    raise BootstrapApplyError("Xray archive contains a special member.")
                if normalized == "xray":
                    candidates.append(info)
            if len(candidates) != 1:
                raise BootstrapApplyError("Xray archive must contain exactly one root xray member.")
            selected = candidates[0]
            if selected.file_size > MAX_XRAY_MEMBER_BYTES:
                raise BootstrapApplyError("Xray archive member is too large.")
            if selected.file_size and selected.compress_size == 0:
                raise BootstrapApplyError("Xray archive compression ratio is invalid.")
            if selected.compress_size and selected.file_size > (
                selected.compress_size * MAX_COMPRESSION_RATIO
            ):
                raise BootstrapApplyError("Xray archive compression ratio is too high.")

            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(candidate_path, flags, 0o600)
            created = True
            digest = hashlib.sha256()
            count = 0
            try:
                with archive.open(selected, "r") as source:
                    while count <= MAX_XRAY_MEMBER_BYTES:
                        chunk = source.read(min(65536, MAX_XRAY_MEMBER_BYTES + 1 - count))
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > MAX_XRAY_MEMBER_BYTES:
                            raise BootstrapApplyError("Xray archive member is too large.")
                        digest.update(chunk)
                        offset = 0
                        while offset < len(chunk):
                            written = os.write(fd, chunk[offset:])
                            if written <= 0:
                                raise OSError("short write")
                            offset += written
                if count != selected.file_size:
                    raise BootstrapApplyError("Xray archive member is incomplete.")
                os.fsync(fd)
            finally:
                os.close(fd)
            if os.name == "posix":
                os.chmod(candidate_path, 0o700)
            completed = True
            return digest.hexdigest()
    except BootstrapApplyError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, EOFError):
        raise BootstrapApplyError("Pinned Xray archive could not be extracted safely.") from None
    finally:
        if created and not completed:
            try:
                candidate_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _read_receipt(path: Path) -> Optional[Dict[str, Any]]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        return None
    if os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o077:
        return None
    if metadata.st_size > MAX_RECEIPT_BYTES:
        return None
    fd = -1
    try:
        fd = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            return None
        chunks = bytearray()
        while len(chunks) <= MAX_RECEIPT_BYTES:
            chunk = os.read(fd, min(65536, MAX_RECEIPT_BYTES + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        current = path.lstat()
        final = os.fstat(fd)
        if (
            (metadata.st_dev, metadata.st_ino) != (final.st_dev, final.st_ino)
            or (final.st_dev, final.st_ino) != (current.st_dev, current.st_ino)
            or final.st_size != opened.st_size
            or final.st_mtime_ns != opened.st_mtime_ns
        ):
            return None
        data = bytes(chunks)
        if len(data) > MAX_RECEIPT_BYTES:
            return None
        parsed = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    finally:
        if fd >= 0:
            os.close(fd)
    if not isinstance(parsed, dict) or parsed.get("schema_version") != STATE_SCHEMA_VERSION:
        return None
    required_strings = (
        "release",
        "archive_sha256",
        "installed_binary_sha256",
        "installed_version",
    )
    if any(not isinstance(parsed.get(name), str) for name in required_strings):
        return None
    for name in ("archive_sha256", "installed_binary_sha256"):
        value = parsed[name]
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            return None
    packages = parsed.get("packages_installed_by_routerkit")
    if not isinstance(packages, list) or any(
        package not in REQUIRED_PACKAGES for package in packages
    ):
        return None
    if packages != [package for package in REQUIRED_PACKAGES if package in packages]:
        return None
    backup_path = parsed.get("backup_path")
    backup_hash = parsed.get("backup_sha256")
    if (backup_path is None) != (backup_hash is None):
        return None
    if backup_path is not None and not isinstance(backup_path, str):
        return None
    if backup_hash is not None and (
        not isinstance(backup_hash, str)
        or len(backup_hash) != 64
        or any(char not in "0123456789abcdef" for char in backup_hash)
    ):
        return None
    return parsed


def _remove_receipt(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError:
        raise BootstrapRollbackError(
            "Binary rollback succeeded but provenance cleanup could not be proven."
        ) from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise BootstrapRollbackError(
            "Binary rollback succeeded but provenance cleanup could not be proven."
        )
    try:
        path.unlink()
        _fsync_directory(path.parent)
    except OSError:
        raise BootstrapRollbackError(
            "Binary rollback succeeded but provenance cleanup could not be proven."
        ) from None


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_receipt(path: Path, receipt: Mapping[str, Any], *, target_root: Path) -> None:
    _ensure_directory(path.parent, root=target_root, mode=0o700)
    encoded = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    fd, temporary = tempfile.mkstemp(prefix=".bootstrap-state-", dir=str(path.parent))
    temporary_path = Path(temporary)
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        offset = 0
        while offset < len(encoded):
            written = os.write(fd, encoded[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(fd)
        os.close(fd)
        fd = -1
        if path.exists():
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise BootstrapApplyError("Bootstrap state destination is unsafe.")
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    except BootstrapApplyError:
        raise
    except OSError:
        raise BootstrapApplyError("Bootstrap provenance receipt could not be published.") from None
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _copy_verified(source: Path, destination: Path, expected_hash: str, *, mode: int) -> None:
    source_fd = -1
    destination_fd = -1
    created = False
    completed = False
    digest = hashlib.sha256()
    count = 0
    try:
        before = source.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise BootstrapApplyError("Backup source is unsafe.")
        source_fd = os.open(
            source,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(source_fd)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise BootstrapApplyError("Backup source changed during validation.")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        destination_fd = os.open(destination, flags, mode)
        created = True
        while count <= MAX_BINARY_BYTES:
            chunk = os.read(source_fd, min(65536, MAX_BINARY_BYTES + 1 - count))
            if not chunk:
                break
            count += len(chunk)
            if count > MAX_BINARY_BYTES:
                raise BootstrapApplyError("Backup source exceeds the supported size limit.")
            digest.update(chunk)
            offset = 0
            while offset < len(chunk):
                written = os.write(destination_fd, chunk[offset:])
                if written <= 0:
                    raise OSError("short write")
                offset += written
        if not hmac.compare_digest(digest.hexdigest(), expected_hash):
            raise BootstrapApplyError("Backup source hash changed during copy.")
        if os.name == "posix":
            os.fchmod(destination_fd, mode)
        os.fsync(destination_fd)
        completed = True
    except BootstrapApplyError:
        raise
    except OSError:
        raise BootstrapApplyError("Verified executable copy failed.") from None
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        if destination_fd >= 0:
            os.close(destination_fd)
        if created and not completed:
            try:
                destination.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _validate_file_hash(path: Path, expected_hash: str, *, executable: bool = True) -> None:
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise BootstrapApplyError("Verified executable is not a regular file.")
        if executable and not os.access(str(path), os.X_OK):
            raise BootstrapApplyError("Verified executable is not executable.")
        fd = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            digest, _ = _hash_open_fd(fd)
        finally:
            os.close(fd)
    except BootstrapApplyError:
        raise
    except OSError:
        raise BootstrapApplyError("Verified executable could not be inspected.") from None
    if not hmac.compare_digest(digest, expected_hash):
        raise BootstrapApplyError("Verified executable hash does not match.")


def create_or_reuse_backup(
    existing: BinaryRecord, target_root: Path
) -> Tuple[Path, str]:
    _ensure_directory(
        target_root / STATE_RELATIVE_PATH.parent,
        root=target_root,
        mode=0o700,
    )
    directory = target_root / BACKUP_RELATIVE_DIR
    _ensure_directory(directory, root=target_root, mode=0o700)
    backup = directory / ("xray-" + existing.sha256)
    if backup.exists():
        _validate_file_hash(backup, existing.sha256)
        return backup, "reused"
    _copy_verified(existing.path, backup, existing.sha256, mode=0o755)
    _validate_file_hash(backup, existing.sha256)
    _fsync_directory(directory)
    return backup, "created"


def _install_candidate(candidate: Path, candidate_hash: str, target: Path) -> None:
    _safe_existing_directory(target.parent, description="Xray destination directory")
    fd, temporary = tempfile.mkstemp(prefix=".routerkit-xray-", dir=str(target.parent))
    os.close(fd)
    temporary_path = Path(temporary)
    temporary_path.unlink()
    try:
        _copy_verified(candidate, temporary_path, candidate_hash, mode=0o755)
        _validate_file_hash(temporary_path, candidate_hash)
        os.replace(temporary_path, target)
        _validate_file_hash(target, candidate_hash)
        _fsync_directory(target.parent)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _rollback(
    target: Path,
    existing: Optional[BinaryRecord],
    backup: Optional[Path],
    *,
    target_root: Path,
    lifecycle: BootstrapSignalLifecycle,
    runner: Callable[..., ProcessResult],
) -> None:
    if existing is None:
        try:
            metadata = target.lstat()
        except FileNotFoundError:
            _fsync_directory(target.parent)
            return
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise BootstrapRollbackError(
                "Replacement failed and automatic rollback could not be proven."
            )
        target.unlink()
        _fsync_directory(target.parent)
        if target.exists():
            raise BootstrapRollbackError(
                "Replacement failed and automatic rollback could not be proven."
            )
        return
    if backup is None:
        raise BootstrapRollbackError(
            "Replacement failed and automatic rollback could not be proven."
        )
    _validate_file_hash(backup, existing.sha256)
    _install_candidate(backup, existing.sha256, target)
    _validate_file_hash(target, existing.sha256)
    completed = runner(
        [str(target), "version"],
        timeout=VERSION_TIMEOUT,
        cwd=target.parent,
        env=sanitized_environment(target_root, target.parent),
        lifecycle=lifecycle,
    )
    if completed.returncode != 0:
        raise BootstrapRollbackError(
            "Replacement failed and automatic rollback could not be proven; backup: {}".format(
                backup
            )
        )


def _cleanup_staging(
    path: Optional[Path], expected_identity: Optional[Tuple[int, int]]
) -> None:
    if path is None:
        return
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError:
        raise BootstrapApplyError(
            "Private bootstrap staging cleanup failed; inspect the target var/tmp directory."
        ) from None
    if (
        expected_identity is None
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != expected_identity
    ):
        raise BootstrapApplyError(
            "Private bootstrap staging identity changed; inspect the target var/tmp directory."
        )
    try:
        entries = list(path.iterdir())
        if len(entries) > 8:
            raise OSError("unexpected staging entry count")
        for entry in entries:
            entry_metadata = entry.lstat()
            if stat.S_ISLNK(entry_metadata.st_mode) or not stat.S_ISREG(
                entry_metadata.st_mode
            ):
                raise OSError("unexpected staging entry type")
            entry.unlink()
        current = path.lstat()
        if (current.st_dev, current.st_ino) != expected_identity:
            raise OSError("staging identity changed")
        path.rmdir()
    except OSError:
        raise BootstrapApplyError(
            "Private bootstrap staging cleanup failed; inspect the target var/tmp directory."
        ) from None


def apply_bootstrap_transaction(
    manifest: Mapping[str, Any],
    *,
    target_root: Path = Path("/opt"),
    downloader: Callable[..., Any] = download_pinned_archive,
    runner: Callable[..., ProcessResult] = run_bounded_process,
    lifecycle: Optional[BootstrapSignalLifecycle] = None,
) -> TransactionResult:
    """Apply fixed packages and one manifest-pinned Xray transaction."""

    target_root = Path(target_root)
    artifact = manifest["artifacts"]["linux-arm64"]
    release = manifest["upstream"]["release_tag"]
    expected_version = _expected_version(release)
    expected_archive_hash = artifact["sha256"]
    source_url = artifact["download_url"]
    result = TransactionResult(
        environment="Linux arm64 {}".format(target_root), artifact_release=release
    )
    owned_lifecycle = lifecycle is None
    lifecycle = lifecycle or BootstrapSignalLifecycle()
    staging = None
    staging_identity = None
    replacement_started = False
    existing = None
    backup = None
    try:
        validate_apply_environment(target_root, create=False)
        validate_existing_target_metadata(target_root)
        handle = resolve_opkg(target_root)
        if owned_lifecycle:
            lifecycle.install()
        validate_apply_environment(target_root, create=True)
        already, installed = ensure_required_packages(
            target_root, handle, lifecycle=lifecycle, runner=runner
        )
        result.packages_already_installed = already
        result.packages_installed = installed

        target = target_root / TARGET_RELATIVE_PATH
        existing = inspect_binary(
            target, target_root=target_root, lifecycle=lifecycle, runner=runner
        )
        result.existing_binary_present = existing is not None
        result.existing_binary_hash = existing.sha256 if existing else None
        receipt_path = target_root / STATE_RELATIVE_PATH
        receipt = _read_receipt(receipt_path)
        receipt_backup_valid = True
        if receipt is not None and receipt.get("backup_path") is not None:
            expected_backup = target_root / BACKUP_RELATIVE_DIR / (
                "xray-" + str(receipt.get("backup_sha256"))
            )
            if Path(receipt["backup_path"]) != expected_backup:
                receipt_backup_valid = False
            else:
                try:
                    _validate_file_hash(expected_backup, receipt["backup_sha256"])
                except BootstrapApplyError:
                    receipt_backup_valid = False
        if (
            not installed
            and receipt is not None
            and receipt_backup_valid
            and existing is not None
            and receipt["release"] == release
            and hmac.compare_digest(receipt["archive_sha256"], expected_archive_hash)
            and hmac.compare_digest(receipt["installed_binary_sha256"], existing.sha256)
            and existing.version == expected_version
            and receipt["installed_version"] == expected_version
        ):
            result.archive_sha256_verified = True
            result.candidate_version = expected_version
            result.post_install_verified = True
            result.idempotent_noop = True
            result.backup_path = receipt.get("backup_path")
            result.backup_created_or_reused = (
                "reused" if result.backup_path is not None else None
            )
            return result

        staging_parent = target_root / STAGING_RELATIVE_DIR
        staging = Path(tempfile.mkdtemp(prefix=".routerkit-bootstrap-", dir=str(staging_parent)))
        if os.name == "posix":
            os.chmod(staging, 0o700)
        staging_metadata = staging.lstat()
        staging_identity = (staging_metadata.st_dev, staging_metadata.st_ino)
        if staging_metadata.st_dev != target.parent.stat().st_dev:
            raise BootstrapApplyError("Bootstrap staging and Xray destination are not on one filesystem.")
        archive_path = staging / "xray.zip"
        download = downloader(
            source_url,
            archive_path,
            expected_url=source_url,
        )
        if not hmac.compare_digest(download.sha256, expected_archive_hash):
            raise BootstrapApplyError("Pinned Xray archive checksum did not match.")
        result.archive_sha256_verified = True

        candidate = staging / "xray.candidate"
        candidate_hash = extract_xray_candidate(archive_path, candidate)
        version = probe_exact_version(
            candidate,
            expected_version,
            target_root=target_root,
            cwd=staging,
            lifecycle=lifecycle,
            runner=runner,
        )
        result.candidate_version = version
        if os.name == "posix":
            os.chmod(candidate, 0o755)

        if existing is not None:
            current = inspect_binary(
                target, target_root=target_root, lifecycle=lifecycle, runner=runner
            )
            if current is None or current.identity != existing.identity or not hmac.compare_digest(
                current.sha256, existing.sha256
            ):
                raise BootstrapApplyError("Existing Xray changed before replacement.")
            backup, disposition = create_or_reuse_backup(existing, target_root)
            result.backup_path = str(backup)
            result.backup_created_or_reused = disposition

        replacement_started = True
        _install_candidate(candidate, candidate_hash, target)
        result.replacement_performed = True
        try:
            installed_version = probe_exact_version(
                target,
                expected_version,
                target_root=target_root,
                cwd=target.parent,
                lifecycle=lifecycle,
                runner=runner,
            )
            _validate_file_hash(target, candidate_hash)
            previous_packages = receipt.get("packages_installed_by_routerkit", []) if receipt else []
            receipt_packages = [
                package
                for package in REQUIRED_PACKAGES
                if package in set(previous_packages).union(installed)
            ]
            new_receipt = {
                "schema_version": STATE_SCHEMA_VERSION,
                "release": release,
                "archive_sha256": expected_archive_hash,
                "installed_binary_sha256": candidate_hash,
                "installed_version": installed_version,
                "backup_path": str(backup) if backup is not None else None,
                "backup_sha256": existing.sha256 if existing is not None else None,
                "packages_installed_by_routerkit": receipt_packages,
            }
            _atomic_receipt(receipt_path, new_receipt, target_root=target_root)
            result.post_install_verified = True
        except BootstrapTermination:
            raise
        except BaseException as original:
            result.rollback_attempted = True
            try:
                _rollback(
                    target,
                    existing,
                    backup,
                    target_root=target_root,
                    lifecycle=lifecycle,
                    runner=runner,
                )
                result.rollback_verified = True
                _remove_receipt(receipt_path)
            except BootstrapRollbackError:
                raise
            except BaseException:
                message = "Replacement failed and automatic rollback could not be proven."
                if backup is not None:
                    message += " Backup: {}".format(backup)
                raise BootstrapRollbackError(message) from None
            if isinstance(original, BootstrapApplyError):
                raise original
            raise BootstrapApplyError("Post-install validation failed; rollback was verified.") from None
        return result
    except BootstrapApplyError as exc:
        if result.packages_installed:
            raise BootstrapApplyError(
                "{} Newly installed Entware packages may remain: {}.".format(
                    exc, ", ".join(result.packages_installed)
                ),
                exit_code=exc.exit_code,
            ) from None
        raise
    except BootstrapTermination:
        if replacement_started and not result.post_install_verified:
            result.rollback_attempted = True
            try:
                _rollback(
                    target_root / TARGET_RELATIVE_PATH,
                    existing,
                    backup,
                    target_root=target_root,
                    lifecycle=lifecycle,
                    runner=runner,
                )
                result.rollback_verified = True
                _remove_receipt(target_root / STATE_RELATIVE_PATH)
            except BaseException:
                pass
        raise
    finally:
        lifecycle.begin_cleanup()
        cleanup_error = None
        try:
            _cleanup_staging(staging, staging_identity)
        except BootstrapApplyError as exc:
            cleanup_error = exc
        if owned_lifecycle:
            lifecycle.restore()
        if cleanup_error is not None:
            raise cleanup_error
