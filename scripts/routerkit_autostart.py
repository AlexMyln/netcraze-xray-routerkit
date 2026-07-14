#!/usr/bin/env python3
"""
Safe explicit S23xray-direct autostart transaction.

This module deliberately keeps read-only inspection separate from apply. Apply
is limited to literal /opt, delegates runtime start/restart to the installed
reviewed init script, and enables boot execution only after strict local
loopback runtime verification succeeds.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import Dict, List, Optional, Sequence, Set, Tuple


EXPECTED_PORTS = (1082, 1083, 1084)
EXPECTED_HOST = "127.0.0.1"
DEFAULT_TARGET_ROOT = "/opt"
MAX_INIT_ENTRIES = 128
MAX_PID_FILE_BYTES = 32
USAGE_ERROR = 2
ROLLBACK_UNPROVEN = 3
SPAWN_ERROR = 127


class AutostartError(Exception):
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class AutostartPaths:
    target_root: Path

    @property
    def init_dir(self) -> Path:
        return self.target_root / "etc" / "init.d"

    @property
    def s23(self) -> Path:
        return self.init_dir / "S23xray-direct"

    @property
    def s24(self) -> Path:
        return self.init_dir / "S24xray"

    @property
    def xray(self) -> Path:
        return self.target_root / "sbin" / "xray"

    @property
    def conf_dir(self) -> Path:
        return self.target_root / "etc" / "xray" / "configs"

    @property
    def pid_file(self) -> Path:
        return self.target_root / "var" / "run" / "xray-direct.pid"

    @property
    def receipt(self) -> Path:
        return self.target_root / "var" / "lib" / "routerkit" / "autostart.json"


@dataclass
class RuntimeVerification:
    ok: bool
    pid: Optional[int] = None
    messages: List[str] = field(default_factory=list)
    listeners: Dict[int, str] = field(default_factory=dict)


@dataclass
class AutostartStatus:
    target_root: str
    s23_present: bool
    s23_regular: bool
    s23_symlink: bool
    s23_mode: Optional[str]
    s23_hash_match: bool
    s24_present: bool
    s24_regular: bool
    s24_symlink: bool
    s24_mode: Optional[str]
    s23_enabled: bool
    s24_enabled: bool
    conflicts: List[str]
    pid_file_state: str
    runtime: RuntimeVerification
    reboot_verification: str = "not_proven"

    @property
    def verify_ok(self) -> bool:
        return (
            self.s23_present
            and self.s23_regular
            and not self.s23_symlink
            and self.s23_enabled
            and self.s23_hash_match
            and (not self.s24_present or (self.s24_regular and not self.s24_enabled and not self.s24_symlink))
            and not self.conflicts
            and self.runtime.ok
        )

    def to_json(self) -> Dict[str, object]:
        return {
            "target_root": self.target_root,
            "s23": {
                "present": self.s23_present,
                "regular": self.s23_regular,
                "symlink": self.s23_symlink,
                "mode": self.s23_mode,
                "template_hash_match": self.s23_hash_match,
                "enabled": self.s23_enabled,
            },
            "s24": {
                "present": self.s24_present,
                "regular": self.s24_regular,
                "symlink": self.s24_symlink,
                "mode": self.s24_mode,
                "enabled": self.s24_enabled,
            },
            "conflicting_executable_init_scripts": self.conflicts,
            "pid_file_state": self.pid_file_state,
            "runtime_verification": {
                "ok": self.runtime.ok,
                "pid": self.runtime.pid,
                "messages": list(self.runtime.messages),
                "listeners": {str(port): owner for port, owner in self.runtime.listeners.items()},
            },
            "reboot_verification": self.reboot_verification,
            "verify_ok": self.verify_ok,
        }


@dataclass
class TransactionResult:
    action: str
    changed_s23_mode: bool = False
    disabled_s24: bool = False
    restarted_process: bool = False
    noop: bool = False
    strict_restart_verified: bool = False
    rollback_unproven: bool = False
    message: str = ""

    def to_json(self) -> Dict[str, object]:
        return {
            "action": self.action,
            "changed_s23_mode": self.changed_s23_mode,
            "disabled_s24": self.disabled_s24,
            "restarted_process": self.restarted_process,
            "noop": self.noop,
            "strict_restart_verified": self.strict_restart_verified,
            "rollback_unproven": self.rollback_unproven,
            "message": self.message,
        }


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def template_path() -> Path:
    return repo_root_from_script() / "templates" / "S23xray-direct"


def _read_file_no_symlink(path: Path, maximum: Optional[int] = None) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise AutostartError("expected a regular file")
        if maximum is not None and metadata.st_size > maximum:
            raise AutostartError("file is too large")
        return os.read(fd, metadata.st_size if maximum is None else maximum)
    finally:
        os.close(fd)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reviewed_template_hash() -> str:
    return _sha256(_read_file_no_symlink(template_path()))


def installed_template_matches(path: Path) -> bool:
    try:
        return _sha256(_read_file_no_symlink(path)) == reviewed_template_hash()
    except (OSError, AutostartError):
        return False


def _file_info(path: Path) -> Tuple[bool, bool, bool, Optional[str], bool]:
    try:
        metadata = path.lstat()
    except OSError:
        return False, False, False, None, False
    is_link = stat.S_ISLNK(metadata.st_mode)
    is_regular = stat.S_ISREG(metadata.st_mode)
    mode_text = oct(stat.S_IMODE(metadata.st_mode))
    executable = bool(metadata.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    return True, is_regular, is_link, mode_text, executable


def _require_regular_nonsymlink(path: Path, description: str, *, allow_missing: bool = False) -> os.stat_result:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            raise
        raise AutostartError(f"{description} is missing; rerun the normal install stage.")
    except OSError as exc:
        raise AutostartError(f"{description} could not be inspected: {exc}") from None
    if stat.S_ISLNK(metadata.st_mode):
        raise AutostartError(f"{description} is a symlink and was rejected.")
    if not stat.S_ISREG(metadata.st_mode):
        raise AutostartError(f"{description} is not a regular file and was rejected.")
    if getattr(metadata, "st_nlink", 1) > 1:
        raise AutostartError(f"{description} has unexpected hardlinks and was rejected.")
    return metadata


def _safe_chmod(path: Path, mode: int, description: str) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags)
    try:
        before = os.fstat(fd)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise AutostartError(f"{description} is not a safe regular file.")
        os.fchmod(fd, mode)
        os.fsync(fd)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise AutostartError(f"{description} changed identity during chmod.")
        if stat.S_IMODE(after.st_mode) != mode:
            raise AutostartError(f"{description} mode verification failed.")
    finally:
        os.close(fd)
    _fsync_parent(path)


def _fsync_parent(path: Path) -> None:
    if os.name != "posix":
        return
    try:
        fd = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        with contextlib.suppress(OSError):
            os.fsync(fd)
    finally:
        os.close(fd)


def _disable_executable(path: Path, description: str) -> bool:
    metadata = _require_regular_nonsymlink(path, description)
    current = stat.S_IMODE(metadata.st_mode)
    new_mode = current & ~0o111
    if new_mode == current:
        return False
    _safe_chmod(path, new_mode, description)
    return True


def _enable_executable(path: Path, description: str) -> bool:
    metadata = _require_regular_nonsymlink(path, description)
    current = stat.S_IMODE(metadata.st_mode)
    new_mode = 0o755
    if new_mode == current:
        return False
    _safe_chmod(path, new_mode, description)
    return True


def _bounded_init_conflicts(paths: AutostartPaths) -> List[str]:
    try:
        entries = list(paths.init_dir.iterdir())
    except OSError:
        return []
    conflicts: List[str] = []
    for entry in entries[:MAX_INIT_ENTRIES]:
        if entry.name in {"S23xray-direct", "S24xray"}:
            continue
        lowered = entry.name.lower()
        if "xray" not in lowered:
            continue
        present, regular, symlink, _mode, executable = _file_info(entry)
        if present and regular and not symlink and executable:
            conflicts.append(str(Path("/opt/etc/init.d") / entry.name))
    if len(entries) > MAX_INIT_ENTRIES:
        conflicts.append("/opt/etc/init.d/<too-many-entries>")
    return conflicts


def _pid_file_state(paths: AutostartPaths) -> str:
    try:
        metadata = paths.pid_file.lstat()
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unreadable"
    if stat.S_ISLNK(metadata.st_mode):
        return "symlink_rejected"
    if not stat.S_ISREG(metadata.st_mode):
        return "non_regular_rejected"
    if metadata.st_size > MAX_PID_FILE_BYTES:
        return "too_large_rejected"
    return "regular"


def _read_pid(paths: AutostartPaths) -> int:
    _require_regular_nonsymlink(paths.pid_file, "PID file")
    text = _read_file_no_symlink(paths.pid_file, MAX_PID_FILE_BYTES).decode("ascii", "strict").strip()
    if not text.isdecimal():
        raise AutostartError("PID file does not contain an ASCII decimal PID.")
    pid = int(text, 10)
    if pid <= 1 or pid > 4_194_304:
        raise AutostartError("PID file contains an out-of-range PID.")
    return pid


def _proc_path(proc_root: Path, pid: int, *parts: str) -> Path:
    return proc_root / str(pid) / Path(*parts)


def _same_executable(proc_root: Path, pid: int, expected: Path) -> bool:
    try:
        actual = _proc_path(proc_root, pid, "exe").resolve(strict=True)
        expected_resolved = expected.resolve(strict=True)
    except OSError:
        return False
    return actual == expected_resolved


def _cmdline_ok(proc_root: Path, pid: int, expected_xray: Path, expected_conf_dir: Path) -> bool:
    try:
        raw = _proc_path(proc_root, pid, "cmdline").read_bytes()
    except OSError:
        return False
    args = [item.decode("utf-8", "replace") for item in raw.split(b"\0") if item]
    if len(args) != 4:
        return False
    exe_arg, run_arg, conf_arg, conf_dir_arg = args
    return (
        Path(exe_arg) == expected_xray
        and run_arg == "run"
        and conf_arg == "-confdir"
        and Path(conf_dir_arg) == expected_conf_dir
    )


def _decode_tcp4(hex_addr: str) -> str:
    raw = bytes.fromhex(hex_addr)
    return ".".join(str(part) for part in raw[::-1])


def _decode_tcp6(hex_addr: str) -> str:
    if hex_addr == "00000000000000000000000001000000":
        return "::1"
    if hex_addr == "00000000000000000000000000000000":
        return "::"
    if hex_addr.endswith("0000FFFF") or hex_addr.startswith("0000000000000000FFFF0000"):
        return "non-loopback"
    return "non-loopback"


def _parse_proc_net(path: Path, *, ipv6: bool) -> List[Tuple[str, int, str, str]]:
    rows: List[Tuple[str, int, str, str]] = []
    try:
        lines = path.read_text(encoding="ascii", errors="replace").splitlines()
    except OSError:
        return rows
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 10 or parts[3] != "0A":
            continue
        try:
            addr_hex, port_hex = parts[1].split(":", 1)
            address = _decode_tcp6(addr_hex) if ipv6 else _decode_tcp4(addr_hex)
            port = int(port_hex, 16)
        except (ValueError, IndexError):
            continue
        rows.append((address, port, parts[9], "tcp6" if ipv6 else "tcp"))
    return rows


def _socket_inodes_for_pid(proc_root: Path, pid: int) -> Set[str]:
    fd_dir = _proc_path(proc_root, pid, "fd")
    inodes: Set[str] = set()
    try:
        entries = list(fd_dir.iterdir())
    except OSError:
        return inodes
    for entry in entries[:256]:
        try:
            target = os.readlink(str(entry))
        except OSError:
            continue
        if target.startswith("socket:[") and target.endswith("]"):
            inodes.add(target[len("socket:[") : -1])
    return inodes


def verify_runtime(paths: AutostartPaths, *, proc_root: Path = Path("/proc")) -> RuntimeVerification:
    messages: List[str] = []
    try:
        pid = _read_pid(paths)
    except (AutostartError, OSError, UnicodeError) as exc:
        return RuntimeVerification(False, messages=[str(exc)])

    if not _same_executable(proc_root, pid, paths.xray):
        messages.append("PID does not belong to the expected /opt/sbin/xray executable.")
    if not _cmdline_ok(proc_root, pid, paths.xray, paths.conf_dir):
        messages.append("PID command line does not match run -confdir /opt/etc/xray/configs.")

    owned_inodes = _socket_inodes_for_pid(proc_root, pid)
    rows = _parse_proc_net(proc_root / "net" / "tcp", ipv6=False)
    rows.extend(_parse_proc_net(proc_root / "net" / "tcp6", ipv6=True))
    listeners: Dict[int, str] = {}
    for port in EXPECTED_PORTS:
        port_rows = [row for row in rows if row[1] == port]
        if not port_rows:
            messages.append(f"Missing listener on {EXPECTED_HOST}:{port}.")
            continue
        matching = [row for row in port_rows if row[0] == EXPECTED_HOST and row[2] in owned_inodes]
        exposed = [row for row in port_rows if row[0] != EXPECTED_HOST]
        wrong_owner = [row for row in port_rows if row[0] == EXPECTED_HOST and row[2] not in owned_inodes]
        if exposed:
            messages.append(f"Port {port} is exposed outside 127.0.0.1.")
        if wrong_owner:
            messages.append(f"Port {port} is owned by another process.")
        if len(matching) != 1:
            messages.append(f"Port {port} does not have exactly one expected listener.")
        else:
            listeners[port] = f"{EXPECTED_HOST}:{port}"
    return RuntimeVerification(not messages, pid=pid, messages=messages, listeners=listeners)


def inspect_status(target_root: Path, *, proc_root: Path = Path("/proc")) -> AutostartStatus:
    paths = AutostartPaths(target_root)
    s23_present, s23_regular, s23_symlink, s23_mode, s23_enabled = _file_info(paths.s23)
    s24_present, s24_regular, s24_symlink, s24_mode, s24_enabled = _file_info(paths.s24)
    runtime = verify_runtime(paths, proc_root=proc_root)
    return AutostartStatus(
        target_root=str(target_root),
        s23_present=s23_present,
        s23_regular=s23_regular,
        s23_symlink=s23_symlink,
        s23_mode=s23_mode,
        s23_hash_match=installed_template_matches(paths.s23) if s23_present and s23_regular and not s23_symlink else False,
        s24_present=s24_present,
        s24_regular=s24_regular,
        s24_symlink=s24_symlink,
        s24_mode=s24_mode,
        s23_enabled=s23_enabled,
        s24_enabled=s24_enabled,
        conflicts=_bounded_init_conflicts(paths),
        pid_file_state=_pid_file_state(paths),
        runtime=runtime,
    )


def _print_status(status: AutostartStatus) -> None:
    print("RouterKit autostart status")
    print(f"Target root: {status.target_root}")
    print(
        "S23xray-direct: "
        f"present={status.s23_present} regular={status.s23_regular} "
        f"symlink={status.s23_symlink} mode={status.s23_mode} "
        f"enabled={status.s23_enabled} template_match={status.s23_hash_match}"
    )
    print(
        "S24xray: "
        f"present={status.s24_present} regular={status.s24_regular} "
        f"symlink={status.s24_symlink} mode={status.s24_mode} enabled={status.s24_enabled}"
    )
    print(f"Conflicting executable Xray init scripts: {len(status.conflicts)}")
    print(f"PID file: {status.pid_file_state}")
    print(f"Runtime verification: {'ok' if status.runtime.ok else 'failed'}")
    for message in status.runtime.messages:
        print(f"- {message}")
    print("Reboot verification: not_proven")


def _confirm(prompt: str, input_fn=input) -> bool:
    return input_fn(prompt).strip().lower() in {"y", "yes"}


class TransactionSignals:
    def __init__(self) -> None:
        self.first_signal: Optional[int] = None
        self.previous: Dict[int, object] = {}

    @staticmethod
    def handled_signals() -> Tuple[int, ...]:
        result: List[int] = []
        for name in ("SIGINT", "SIGTERM", "SIGHUP"):
            signum = getattr(signal, name, None)
            if signum is not None and signum not in result:
                result.append(signum)
        return tuple(result)

    def _handle(self, signum: int, _frame: Optional[FrameType]) -> None:
        if self.first_signal is None:
            self.first_signal = signum

    def __enter__(self) -> "TransactionSignals":
        for signum in self.handled_signals():
            self.previous[signum] = signal.getsignal(signum)
            signal.signal(signum, self._handle)
        return self

    def __exit__(self, *_exc: object) -> None:
        for signum, handler in self.previous.items():
            signal.signal(signum, handler)

    def raise_if_requested(self) -> None:
        if self.first_signal is not None:
            raise AutostartError(
                f"transaction interrupted by signal {self.first_signal}",
                exit_code=128 + self.first_signal,
            )


def _run_init(paths: AutostartPaths, action: str) -> None:
    try:
        completed = subprocess.run(["sh", str(paths.s23), action], check=False)
    except OSError as exc:
        raise AutostartError(f"could not run S23xray-direct {action}: {exc}", SPAWN_ERROR) from None
    if completed.returncode != 0:
        raise AutostartError(
            f"S23xray-direct {action} failed with exit code {completed.returncode}.",
            completed.returncode,
        )


def _preflight_apply(paths: AutostartPaths) -> None:
    if str(paths.target_root) != DEFAULT_TARGET_ROOT:
        raise AutostartError("autostart apply supports only literal /opt.", USAGE_ERROR)
    if os.uname().sysname != "Linux":
        raise AutostartError("autostart apply must run on Entware/Linux router.")
    _require_regular_nonsymlink(paths.s23, "S23xray-direct")
    if not installed_template_matches(paths.s23):
        raise AutostartError(
            "S23xray-direct does not match the reviewed repository template; rerun the normal install stage."
        )
    _require_regular_nonsymlink(paths.xray, "Xray executable")
    if not os.access(str(paths.xray), os.X_OK):
        raise AutostartError("Xray executable is not executable.")
    if not paths.conf_dir.is_dir():
        raise AutostartError("Xray config directory is missing.")
    if paths.s24.exists():
        _require_regular_nonsymlink(paths.s24, "S24xray")
    conflicts = _bounded_init_conflicts(paths)
    if conflicts:
        raise AutostartError("conflicting executable Xray init scripts were found.")


def enable_autostart(paths: AutostartPaths, *, proc_root: Path = Path("/proc")) -> TransactionResult:
    _preflight_apply(paths)
    before_mode = stat.S_IMODE(paths.s23.lstat().st_mode)
    result = TransactionResult(action="enable")
    before = inspect_status(paths.target_root, proc_root=proc_root)
    if before.verify_ok:
        result.noop = True
        result.strict_restart_verified = True
        result.message = "Already enabled and strictly verified."
        return result

    with TransactionSignals() as signals:
        try:
            if paths.s24.exists():
                result.disabled_s24 = _disable_executable(paths.s24, "S24xray")
            _disable_executable(paths.s23, "S23xray-direct")
            signals.raise_if_requested()
            _run_init(paths, "restart")
            result.restarted_process = True
            signals.raise_if_requested()
            runtime = verify_runtime(paths, proc_root=proc_root)
            if not runtime.ok:
                raise AutostartError("strict runtime verification failed: " + "; ".join(runtime.messages))
            result.changed_s23_mode = _enable_executable(paths.s23, "S23xray-direct")
            signals.raise_if_requested()
            final_status = inspect_status(paths.target_root, proc_root=proc_root)
            if not final_status.verify_ok:
                raise AutostartError("final autostart verification failed.")
            _publish_receipt(paths, result)
            result.strict_restart_verified = True
            result.message = "Autostart enabled and restart-verified."
            return result
        except AutostartError:
            rollback_failed = False
            with contextlib.suppress(Exception):
                current = stat.S_IMODE(paths.s23.lstat().st_mode)
                if current != before_mode:
                    _safe_chmod(paths.s23, before_mode, "S23xray-direct")
            try:
                restored = inspect_status(paths.target_root, proc_root=proc_root)
                if stat.S_IMODE(paths.s23.lstat().st_mode) != before_mode:
                    rollback_failed = True
                if before.runtime.ok and not restored.runtime.ok:
                    rollback_failed = True
            except Exception:
                rollback_failed = True
            if rollback_failed:
                raise AutostartError(
                    "autostart enable failed and rollback could not be proven. "
                    "Safe disable command: routerkit autostart --disable --apply --yes",
                    ROLLBACK_UNPROVEN,
                ) from None
            raise


def disable_autostart(paths: AutostartPaths) -> TransactionResult:
    if str(paths.target_root) != DEFAULT_TARGET_ROOT:
        raise AutostartError("autostart apply supports only literal /opt.", USAGE_ERROR)
    result = TransactionResult(action="disable")
    if paths.s23.exists():
        result.changed_s23_mode = _disable_executable(paths.s23, "S23xray-direct")
    if paths.s24.exists():
        result.disabled_s24 = _disable_executable(paths.s24, "S24xray")
    _remove_receipt(paths)
    result.noop = not result.changed_s23_mode and not result.disabled_s24
    result.message = "Autostart disabled. Runtime may continue until manually stopped or rebooted."
    return result


def _publish_receipt(paths: AutostartPaths, result: TransactionResult) -> None:
    paths.receipt.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = json.dumps(result.to_json(), sort_keys=True) + "\n"
    tmp = paths.receipt.with_name(paths.receipt.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(paths.receipt))
    with contextlib.suppress(OSError):
        paths.receipt.chmod(0o600)
    _fsync_parent(paths.receipt)


def _remove_receipt(paths: AutostartPaths) -> None:
    try:
        _require_regular_nonsymlink(paths.receipt, "autostart receipt", allow_missing=True)
    except FileNotFoundError:
        return
    paths.receipt.unlink()
    _fsync_parent(paths.receipt)


def validate_args(args: argparse.Namespace) -> None:
    modes = [args.verify, args.enable, args.disable]
    if sum(bool(item) for item in modes) > 1:
        raise AutostartError("--verify, --enable, and --disable are mutually exclusive.", USAGE_ERROR)
    if (args.enable or args.disable) and not (args.apply or args.dry_run):
        raise AutostartError("--enable and --disable require either --apply or --dry-run.", USAGE_ERROR)
    if args.apply and not (args.enable or args.disable):
        raise AutostartError("--apply requires --enable or --disable.", USAGE_ERROR)
    if args.yes and not args.apply:
        raise AutostartError("--yes requires --apply.", USAGE_ERROR)
    if args.verify and (args.apply or args.yes or args.dry_run):
        raise AutostartError("--verify is read-only and conflicts with apply, yes, and dry-run.", USAGE_ERROR)
    if args.apply and args.target_root != DEFAULT_TARGET_ROOT:
        raise AutostartError("autostart apply supports only literal /opt.", USAGE_ERROR)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect, verify, enable, or disable S23xray-direct autostart.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify", action="store_true", help="Strict read-only verification.")
    mode.add_argument("--enable", action="store_true", help="Enable S23xray-direct after restart verification.")
    mode.add_argument("--disable", action="store_true", help="Disable S23xray-direct without stopping runtime.")
    parser.add_argument("--apply", action="store_true", help="Apply enable/disable transaction.")
    parser.add_argument("--dry-run", action="store_true", help="Render intended transaction without side effects.")
    parser.add_argument("--yes", action="store_true", help="Skip standalone confirmation prompt; requires --apply.")
    parser.add_argument("--json", action="store_true", help="Render deterministic secret-safe JSON.")
    parser.add_argument("--target-root", default=DEFAULT_TARGET_ROOT, help="Read-only inspection root; apply supports only /opt.")
    parser.add_argument("--proc-root", default="/proc", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    validate_args(args)
    return args


def _print_transaction_result(result: TransactionResult) -> None:
    print(result.message)
    if result.action == "enable" and result.strict_restart_verified:
        print("S24xray remains disabled.")
        print("No reboot was performed; reboot verification remains #16.")


def _dry_run(args: argparse.Namespace) -> int:
    if args.enable:
        print("Would enable S23xray-direct autostart and restart-verify loopback listeners.")
    elif args.disable:
        print("Would disable S23xray-direct autostart without stopping the currently running process.")
    else:
        print("Would inspect S23xray-direct autostart status without changes.")
    return 0


def main(argv: Optional[Sequence[str]] = None, *, input_fn=input) -> int:
    try:
        args = parse_args(argv)
    except AutostartError as exc:
        print(f"routerkit autostart: {exc}", file=sys.stderr)
        return exc.exit_code
    paths = AutostartPaths(Path(args.target_root))
    proc_root = Path(args.proc_root)

    if args.dry_run:
        return _dry_run(args)

    if args.enable and args.apply:
        if not args.yes and not _confirm(
            "Enable S23xray-direct autostart, keep S24xray disabled, restart xray-direct, and verify loopback listeners? [y/N]: ",
            input_fn,
        ):
            print("Cancelled; no autostart changes were made.")
            return 1
        try:
            result = enable_autostart(paths, proc_root=proc_root)
        except AutostartError as exc:
            print(f"routerkit autostart: {exc}", file=sys.stderr)
            print("Safe disable command: routerkit autostart --disable --apply --yes", file=sys.stderr)
            return exc.exit_code
        if args.json:
            print(json.dumps(result.to_json(), sort_keys=True))
        else:
            _print_transaction_result(result)
        return 0

    if args.disable and args.apply:
        if not args.yes and not _confirm(
            "Disable S23xray-direct autostart without stopping the currently running process? [y/N]: ",
            input_fn,
        ):
            print("Cancelled; no autostart changes were made.")
            return 1
        try:
            result = disable_autostart(paths)
        except AutostartError as exc:
            print(f"routerkit autostart: {exc}", file=sys.stderr)
            return exc.exit_code
        if args.json:
            print(json.dumps(result.to_json(), sort_keys=True))
        else:
            _print_transaction_result(result)
        return 0

    status = inspect_status(paths.target_root, proc_root=proc_root)
    if args.json:
        print(json.dumps(status.to_json(), sort_keys=True))
    else:
        _print_status(status)
    if args.verify and not status.verify_ok:
        return 1
    return 0
