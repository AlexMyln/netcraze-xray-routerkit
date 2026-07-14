#!/usr/bin/env python3
"""
Unified routerkit CLI wrapper.

This entrypoint delegates individual tools and owns setup workspace, child,
cleanup, confirmation, and apply orchestration. It does not implement Netcraze
Web UI automation, firewall automation, or hidden writes under /opt.
"""

from __future__ import annotations

import argparse
import errno
import os
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Dict, List, Optional, Sequence, Tuple

from routerkit_private_io import (
    PrivateFileError,
    read_owner_only_text_file,
    write_private_text_exclusive,
)
from routerkit_profile_source import PayloadValidationError, validate_env_name

INSTALL_PLAN_NOTICE = (
    "Install is running in plan-only mode.\n"
    "Use --apply to install generated configs and S23xray-direct."
)

AUTOSTART_RESERVED_MESSAGE = (
    "Autostart enabling will be added after install apply flow is tested. "
    "For now, run chmod manually after healthcheck."
)

INSTALL_APPLY_TARGET_ROOT_MESSAGE = "install --apply currently supports only --target-root /opt."

SETUP_APPLY_TARGET_ROOT_MESSAGE = "setup --apply currently supports only --target-root /opt."

SETUP_YES_REQUIRES_APPLY_MESSAGE = "setup --yes requires --apply."

SETUP_BOOTSTRAP_REQUIRES_APPLY_MESSAGE = "setup --bootstrap-apply requires --apply."

MAX_REUSE_PROFILES_BYTES = 1024 * 1024

SKIP_FLAGS_REQUIRE_APPLY_MESSAGE = (
    "--skip-preflight, --skip-backup, and --skip-healthcheck require --apply."
)


class RouterkitCliError(Exception):
    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class CommandStep:
    name: str
    command: List[str]
    rollback_relevant: bool = False
    suppress_output: bool = False
    cwd: Optional[Path] = None
    remove_env_names: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SetupBootstrapResult:
    returncode: int
    first_signal: Optional[int] = None
    spawn_failed: bool = False
    supervision_failed: bool = False


class SetupCleanupError(Exception):
    pass


class SetupSignalRegistrationError(Exception):
    pass


class SetupTermination(BaseException):
    def __init__(self, signum: int, prior_exit_code: Optional[int] = None) -> None:
        super().__init__(signum)
        self.signum = signum
        self.prior_exit_code = prior_exit_code


def setup_termination_exit_code(termination: SetupTermination) -> int:
    if termination.prior_exit_code not in (None, 0):
        return termination.prior_exit_code
    return 128 + termination.signum


class SetupSignalLifecycle:
    """Own catchable setup signals and the active private-workspace child."""

    graceful_timeout = 2.0
    forced_timeout = 2.0

    def __init__(self) -> None:
        self.active_child: Optional[subprocess.Popen] = None
        self.active_child_captures_output = False
        self.requested_signum: Optional[int] = None
        self._phase = "inactive"
        self._previous_handlers: Dict[int, object] = {}
        self._previous_signal_mask = None

    @staticmethod
    def handled_signals() -> Tuple[int, ...]:
        result = []
        for name in ("SIGTERM", "SIGHUP"):
            signum = getattr(signal, name, None)
            if signum is not None and signum not in result:
                result.append(signum)
        return tuple(result)

    def install(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            raise SetupSignalRegistrationError(
                "signal-aware setup must run in the main Python thread"
            )
        try:
            for signum in self.handled_signals():
                self._previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, self._handle_signal)
        except (OSError, RuntimeError, ValueError) as exc:
            self.restore()
            raise SetupSignalRegistrationError(str(exc)) from None
        self._phase = "active"

    def block_during_workspace_creation(self) -> None:
        if (
            os.name == "posix"
            and threading.current_thread() is threading.main_thread()
            and hasattr(signal, "pthread_sigmask")
        ):
            self._previous_signal_mask = signal.pthread_sigmask(
                signal.SIG_BLOCK,
                self.handled_signals(),
            )

    def restore_signal_mask(self) -> None:
        if self._previous_signal_mask is None:
            return
        previous = self._previous_signal_mask
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        finally:
            self._previous_signal_mask = None

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
            raise SetupTermination(signum)

    def begin_spawn(self) -> None:
        self._phase = "spawning"

    def child_started(self, child: subprocess.Popen, *, captures_output: bool) -> None:
        self.active_child = child
        self.active_child_captures_output = captures_output
        self._phase = "active"
        self.raise_if_requested()

    def child_reaped(self) -> None:
        self.active_child = None
        self.active_child_captures_output = False
        self._phase = "active"

    def begin_cleanup(self) -> None:
        self._phase = "cleanup"

    def raise_if_requested(self, prior_exit_code: Optional[int] = None) -> None:
        if self.requested_signum is not None:
            self._phase = "terminating"
            raise SetupTermination(self.requested_signum, prior_exit_code)

    def _signal_active_child(self, *, force: bool) -> None:
        child = self.active_child
        if child is None or child.poll() is not None:
            return
        if os.name == "posix":
            child_group = child.pid
            try:
                if child_group != os.getpgrp():
                    os.killpg(child_group, signal.SIGKILL if force else signal.SIGTERM)
                    return
            except ProcessLookupError:
                return
            except OSError:
                pass
        if force:
            child.kill()
        else:
            child.terminate()

    def _wait_active_child(self, timeout: float) -> None:
        child = self.active_child
        if child is None:
            return
        if self.active_child_captures_output:
            child.communicate(timeout=timeout)
        else:
            child.wait(timeout=timeout)

    def shutdown_active_child(self) -> None:
        child = self.active_child
        if child is None:
            self._phase = "terminating"
            return
        self._phase = "shutdown"
        try:
            self._signal_active_child(force=False)
            try:
                self._wait_active_child(self.graceful_timeout)
            except subprocess.TimeoutExpired:
                self._signal_active_child(force=True)
                self._wait_active_child(self.forced_timeout)
        finally:
            if child.poll() is not None:
                self.active_child = None
                self.active_child_captures_output = False
            self._phase = "terminating"


@dataclass
class SetupSecretWorkspace:
    directory: Path
    profiles_path: Path

    def cleanup(self) -> None:
        errors: List[OSError] = []
        try:
            entries = list(self.directory.iterdir())
        except FileNotFoundError:
            return
        except OSError as exc:
            entries = []
            errors.append(exc)
        if len(entries) > 32:
            entries = entries[:32]
            errors.append(OSError("too many private workspace entries"))
        for entry in entries:
            try:
                entry.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                errors.append(exc)
        try:
            self.directory.rmdir()
        except FileNotFoundError:
            pass
        except OSError as exc:
            errors.append(exc)
        if errors:
            raise SetupCleanupError("private setup workspace cleanup failed")


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def _repo_script(repo_root: Path, name: str) -> str:
    return str(repo_root / "scripts" / name)


def validate_install_args(args: argparse.Namespace) -> None:
    if args.enable_autostart:
        raise RouterkitCliError(AUTOSTART_RESERVED_MESSAGE, exit_code=2)
    if not args.apply and any((args.skip_preflight, args.skip_backup, args.skip_healthcheck)):
        raise RouterkitCliError(SKIP_FLAGS_REQUIRE_APPLY_MESSAGE, exit_code=2)
    if args.apply and args.target_root != "/opt":
        raise RouterkitCliError(INSTALL_APPLY_TARGET_ROOT_MESSAGE, exit_code=2)


def validate_setup_args(args: argparse.Namespace) -> None:
    if args.bootstrap_apply and not args.apply:
        raise RouterkitCliError(SETUP_BOOTSTRAP_REQUIRES_APPLY_MESSAGE, exit_code=2)
    if args.source_env:
        validate_setup_source_env_name(args.source_env)

    source_options = bool(args.source_env or args.source_file)
    indexes = ([] if args.primary_index is None else [args.primary_index]) + args.fallback_index
    reuse_requested = bool(args.reuse_profiles or args.deprecated_profiles)
    legacy_requested = bool(args.legacy_wizard or args.deprecated_force_wizard)

    if args.source_env and args.source_file:
        raise RouterkitCliError("--source-env and --source-file are mutually exclusive.")
    if args.reuse_profiles and args.deprecated_profiles:
        raise RouterkitCliError("--profiles conflicts with --reuse-profiles.")
    if args.legacy_wizard and args.deprecated_force_wizard:
        raise RouterkitCliError("--force-wizard conflicts with --legacy-wizard.")
    if reuse_requested and (source_options or indexes or legacy_requested):
        raise RouterkitCliError(
            "Existing-profiles reuse conflicts with source options, node indexes, and legacy wizard mode."
        )
    if legacy_requested and (source_options or indexes):
        raise RouterkitCliError("Legacy wizard mode conflicts with source options and node indexes.")
    if args.fallback_index and args.primary_index is None:
        raise RouterkitCliError("--fallback-index requires --primary-index.")
    if len(args.fallback_index) > 2:
        raise RouterkitCliError("At most two --fallback-index values are allowed.")
    if len(indexes) != len(set(indexes)):
        raise RouterkitCliError("Primary and fallback indexes must be distinct.")
    if args.yes and not args.apply:
        raise RouterkitCliError(SETUP_YES_REQUIRES_APPLY_MESSAGE, exit_code=2)
    if args.apply and args.target_root != "/opt":
        raise RouterkitCliError(SETUP_APPLY_TARGET_ROOT_MESSAGE, exit_code=2)


def validate_setup_source_env_name(name: str) -> None:
    try:
        validate_env_name(name)
    except PayloadValidationError:
        raise RouterkitCliError(
            "setup --source-env requires a valid dedicated ROUTERKIT_* environment variable name."
        ) from None
    if not name.startswith("ROUTERKIT_") or len(name) == len("ROUTERKIT_"):
        raise RouterkitCliError(
            "setup --source-env requires a valid dedicated ROUTERKIT_* environment variable name."
        )


def setup_profile_mode(args: argparse.Namespace) -> str:
    if args.reuse_profiles or args.deprecated_profiles:
        return "reuse"
    if args.legacy_wizard or args.deprecated_force_wizard:
        return "legacy"
    return "source"


def setup_reuse_path(args: argparse.Namespace) -> Optional[str]:
    return args.reuse_profiles or args.deprecated_profiles


def build_command(args: argparse.Namespace, repo_root: Path) -> List[str]:
    repo_root = Path(repo_root)

    if args.command == "profile-source":
        command = [sys.executable, _repo_script(repo_root, "routerkit-profile-source.py")]
        if args.source_env:
            command.extend(["--source-env", args.source_env])
        if args.source_file:
            command.extend(["--source-file", args.source_file])
        if args.output != "profiles.json":
            command.extend(["--output", args.output])
        if args.list:
            command.append("--list")
        if args.json:
            command.append("--json")
        if args.primary_index is not None:
            command.extend(["--primary-index", str(args.primary_index)])
        for index in args.fallback_index:
            command.extend(["--fallback-index", str(index)])
        if args.dry_run:
            command.append("--dry-run")
        if args.yes:
            command.append("--yes")
        if args.force:
            command.append("--force")
        return command

    if args.command == "bootstrap":
        command = [sys.executable, _repo_script(repo_root, "routerkit-bootstrap.py")]
        if args.manifest:
            command.extend(["--manifest", args.manifest])
        if args.json:
            command.append("--json")
        if args.dry_run:
            command.append("--dry-run")
        if args.inventory_file:
            command.extend(["--inventory-file", args.inventory_file])
        if args.target_root != "/opt":
            command.extend(["--target-root", args.target_root])
        if args.apply:
            command.append("--apply")
        if args.yes:
            command.append("--yes")
        return command

    if args.command == "wizard":
        return [
            sys.executable,
            _repo_script(repo_root, "routerkit-wizard.py"),
            "--profiles",
            args.profiles,
        ]

    if args.command == "generate":
        return [
            sys.executable,
            _repo_script(repo_root, "generate-xray-profiles.py"),
            "--profiles",
            args.profiles,
            "--out",
            args.out,
        ]

    if args.command == "plan":
        command = [
            sys.executable,
            _repo_script(repo_root, "routerkit-plan.py"),
            "--generated",
            args.generated,
        ]
        if args.json:
            command.append("--json")
        if args.strict:
            command.append("--strict")
        return command

    if args.command == "install":
        validate_install_args(args)
        if args.apply:
            raise ValueError("install --apply uses build_install_apply_steps")
        return [
            sys.executable,
            _repo_script(repo_root, "routerkit-plan.py"),
            "--generated",
            args.generated,
            "--target-root",
            args.target_root,
            "--strict",
        ]

    if args.command == "preflight":
        return ["sh", _repo_script(repo_root, "preflight.sh")]

    if args.command == "healthcheck":
        return ["sh", _repo_script(repo_root, "healthcheck.sh")]

    if args.command == "backup":
        return ["sh", _repo_script(repo_root, "backup.sh")]

    raise ValueError(f"unsupported command: {args.command}")


def build_router_apply_steps(
    generated: str,
    repo_root: Path,
    *,
    include_preflight: bool = True,
    include_backup: bool = True,
    include_healthcheck: bool = True,
    remove_env_names: Tuple[str, ...] = (),
) -> List[CommandStep]:
    repo_root = Path(repo_root)
    steps: List[CommandStep] = []
    if include_preflight:
        steps.append(
            CommandStep(
                "preflight",
                ["sh", _repo_script(repo_root, "preflight.sh")],
                remove_env_names=remove_env_names,
            )
        )
    if include_backup:
        steps.append(
            CommandStep(
                "backup",
                ["sh", _repo_script(repo_root, "backup.sh")],
                rollback_relevant=True,
                remove_env_names=remove_env_names,
            )
        )
    steps.append(
        CommandStep(
            "install",
            ["sh", _repo_script(repo_root, "install-xray-direct.sh"), generated],
            remove_env_names=remove_env_names,
        )
    )
    if include_healthcheck:
        steps.append(
            CommandStep(
                "healthcheck",
                ["sh", _repo_script(repo_root, "healthcheck.sh")],
                remove_env_names=remove_env_names,
            )
        )
    return steps


def build_setup_bootstrap_apply_step(
    repo_root: Path,
    *,
    remove_env_names: Tuple[str, ...] = (),
) -> CommandStep:
    return CommandStep(
        "bootstrap apply",
        [
            sys.executable,
            _repo_script(Path(repo_root), "routerkit-bootstrap.py"),
            "--apply",
            "--yes",
        ],
        remove_env_names=remove_env_names,
    )


def build_install_apply_steps(args: argparse.Namespace, repo_root: Path) -> List[CommandStep]:
    validate_install_args(args)

    repo_root = Path(repo_root)
    steps = [
        CommandStep(
            "strict plan",
            [
                sys.executable,
                _repo_script(repo_root, "routerkit-plan.py"),
                "--generated",
                args.generated,
                "--target-root",
                args.target_root,
                "--strict",
            ],
        )
    ]
    steps.extend(
        build_router_apply_steps(
            args.generated,
            repo_root,
            include_preflight=not args.skip_preflight,
            include_backup=not args.skip_backup,
            include_healthcheck=not args.skip_healthcheck,
        )
    )
    return steps


def create_setup_workspace() -> SetupSecretWorkspace:
    directory = Path(tempfile.mkdtemp(prefix="routerkit-setup-"))
    try:
        if os.name == "posix":
            directory.chmod(0o700)
            if stat.S_IMODE(directory.stat().st_mode) != 0o700:
                raise OSError("private workspace permissions are not restrictive")
        return SetupSecretWorkspace(directory=directory, profiles_path=directory / "profiles.json")
    except OSError:
        try:
            directory.rmdir()
        except OSError:
            pass
        raise


def secure_copy_reuse_profiles(source: Path, destination: Path) -> None:
    text = read_owner_only_text_file(
        Path(source),
        maximum_bytes=MAX_REUSE_PROFILES_BYTES,
        description="Profiles file",
    )
    write_private_text_exclusive(Path(destination), text)


def build_profile_source_step(
    args: argparse.Namespace,
    repo_root: Path,
    private_profiles_path: Path,
) -> CommandStep:
    repo_root = Path(repo_root)
    command = [
        sys.executable,
        _repo_script(repo_root, "routerkit-profile-source.py"),
        "--output",
        str(private_profiles_path),
        "--yes",
    ]
    if args.source_env:
        command.extend(["--source-env", args.source_env, "--consume-source-env"])
    if args.source_file:
        command.extend(["--source-file", args.source_file])
    if args.primary_index is not None:
        command.extend(["--primary-index", str(args.primary_index)])
    for index in args.fallback_index:
        command.extend(["--fallback-index", str(index)])
    return CommandStep("profile source", command)


def build_legacy_wizard_step(repo_root: Path, workspace: SetupSecretWorkspace) -> CommandStep:
    return CommandStep(
        "legacy wizard",
        [
            sys.executable,
            _repo_script(Path(repo_root), "routerkit-wizard.py"),
            "--profiles",
            workspace.profiles_path.name,
            "--no-generator-prompt",
        ],
        cwd=workspace.directory,
    )


def build_generator_step(
    repo_root: Path,
    private_profiles_path: Path,
    generated: str,
    *,
    remove_env_names: Tuple[str, ...] = (),
) -> CommandStep:
    return CommandStep(
        "generator",
        [
            sys.executable,
            _repo_script(Path(repo_root), "generate-xray-profiles.py"),
            "--profiles",
            str(private_profiles_path),
            "--out",
            generated,
        ],
        suppress_output=True,
        remove_env_names=remove_env_names,
    )


def build_strict_plan_step(
    repo_root: Path,
    generated: str,
    target_root: str,
    *,
    remove_env_names: Tuple[str, ...] = (),
) -> CommandStep:
    return CommandStep(
        "strict plan",
        [
            sys.executable,
            _repo_script(Path(repo_root), "routerkit-plan.py"),
            "--generated",
            generated,
            "--target-root",
            target_root,
            "--strict",
        ],
        remove_env_names=remove_env_names,
    )


def _ensure_private_setup_profiles(path: Path) -> None:
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise OSError("not a regular file")
        if os.name == "posix":
            path.chmod(0o600)
            metadata = path.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise OSError("permissions are not private")
    except OSError:
        raise PrivateFileError("Private setup profiles were not created safely.") from None


def build_setup_steps(
    args: argparse.Namespace,
    repo_root: Path,
    private_profiles_path: Path,
    workspace: Optional[SetupSecretWorkspace] = None,
) -> List[CommandStep]:
    """Build executable setup child steps; cleanup still separates generator and plan."""

    validate_setup_args(args)
    mode = setup_profile_mode(args)
    remove_env_names = (args.source_env,) if args.source_env else ()
    steps: List[CommandStep] = []
    if mode == "source":
        steps.append(build_profile_source_step(args, repo_root, private_profiles_path))
    elif mode == "legacy":
        if workspace is None:
            raise ValueError("legacy setup steps require a private workspace")
        steps.append(build_legacy_wizard_step(repo_root, workspace))
    steps.append(
        build_generator_step(
            repo_root,
            private_profiles_path,
            args.generated,
            remove_env_names=remove_env_names,
        )
    )
    steps.append(
        build_strict_plan_step(
            repo_root,
            args.generated,
            args.target_root,
            remove_env_names=remove_env_names,
        )
    )
    return steps


def build_steps(args: argparse.Namespace, repo_root: Path) -> List[CommandStep]:
    if args.command == "install" and args.apply:
        return build_install_apply_steps(args, repo_root)
    return [CommandStep(args.command, build_command(args, repo_root))]


def _render_token(token: str, repo_root: Optional[Path]) -> str:
    if token == sys.executable:
        return "python3"
    if repo_root is not None:
        try:
            return Path(token).resolve().relative_to(repo_root).as_posix()
        except (OSError, ValueError):
            pass
    return token


def render_steps(steps: Sequence[CommandStep], repo_root: Optional[Path] = None) -> str:
    if len(steps) == 1:
        command = [_render_token(token, repo_root) for token in steps[0].command]
        return shlex.join(command)

    lines = ["Would run install apply pipeline:"]
    for index, step in enumerate(steps, start=1):
        command = [_render_token(token, repo_root) for token in step.command]
        lines.append(f"{index}. {shlex.join(command)}")
    return "\n".join(lines)


def render_setup_pipeline(args: argparse.Namespace) -> str:
    mode = setup_profile_mode(args)
    generator_stage = (
        "run generator with secret-bearing output suppressed "
        f"(generated output: {shlex.quote(args.generated)})"
    )
    plan_stage = f"run strict plan (target root: {shlex.quote(args.target_root)})"
    lines = ["Would run setup pipeline:"]
    if mode == "source":
        stages = [
            "acquire profile source (hidden/env/protected file)",
            "resolve HTTPS source if needed",
            "parse compatible nodes and select primary/fallback profiles",
            "create private setup profiles",
            generator_stage,
            "remove private setup profiles",
            plan_stage,
        ]
    elif mode == "reuse":
        stages = [
            "securely copy explicit private profiles file into setup workspace",
            generator_stage,
            "remove private setup profiles",
            plan_stage,
        ]
    else:
        stages = [
            "run legacy profiles wizard in private setup workspace",
            generator_stage,
            "remove private setup profiles",
            plan_stage,
        ]
    for index, stage in enumerate(stages, start=1):
        lines.append(f"{index}. {stage}")
    index = len(stages) + 1
    if args.apply:
        if not args.yes:
            lines.append(f"{index}. confirmation gate")
            index += 1
        if args.bootstrap_apply:
            lines.append(
                f"{index}. bootstrap apply (fixed missing packages + pinned Xray transaction)"
            )
            index += 1
        for stage in ("preflight", "backup", "install", "healthcheck"):
            lines.append(f"{index}. {stage}")
            index += 1
    return "\n".join(lines)


def render_setup_steps(
    steps: Sequence[CommandStep],
    repo_root: Path,
    generated: str,
    include_apply: bool,
    include_confirmation: bool,
    bootstrap_apply: bool = False,
) -> str:
    """Deprecated compatibility wrapper for callers using the older renderer."""

    del steps, repo_root
    args = argparse.Namespace(
        reuse_profiles=None,
        deprecated_profiles=None,
        legacy_wizard=False,
        deprecated_force_wizard=False,
        apply=include_apply,
        bootstrap_apply=bootstrap_apply,
        yes=not include_confirmation,
        generated=generated,
        target_root="/opt",
    )
    return render_setup_pipeline(args)


def confirm_setup_apply(input_fn=input, *, bootstrap_apply: bool = False) -> bool:
    prompt = (
        "Proceed with bootstrap and router apply stages? [y/N]: "
        if bootstrap_apply
        else "Proceed with router apply stages? [y/N]: "
    )
    answer = input_fn(prompt).strip().lower()
    return answer in {"y", "yes"}


def print_setup_bootstrap_warning() -> None:
    print("Bootstrap apply requested:")
    print("- may install the fixed Entware prerequisite set;")
    print("- may replace /opt/sbin/xray transactionally;")
    print("- package additions are not automatically rolled back;")
    print("- no service restart or autostart is performed.")


def _print_setup_source_summary(mode: str) -> None:
    if mode == "reuse":
        print("Explicit private profiles were securely reused.")
    elif mode == "legacy":
        print("Legacy wizard profile input completed.")
    else:
        print("Profile source acquisition, parsing, and selection completed.")
    print("Profiles were generated through a private setup workspace.")
    print("Private setup profiles were removed.")
    print("Generated config fragments remain locally and may contain secrets; do not publish them.")


def print_setup_plan_summary(mode: str = "source") -> None:
    print("Setup plan completed.")
    _print_setup_source_summary(mode)
    print("No router apply steps were executed.")
    print("No bootstrap was executed.")
    print("Use --apply to continue through preflight, backup, install, and healthcheck.")
    print("Use --apply --bootstrap-apply for explicit runtime preparation before router apply.")
    print(
        "Autostart, device discovery, and policy automation remain pending."
    )


def print_setup_apply_summary(mode: str = "source", *, bootstrap_apply: bool = False) -> None:
    print("Setup apply completed.")
    _print_setup_source_summary(mode)
    print("Strict plan passed.")
    if bootstrap_apply:
        print("Bootstrap apply completed before preflight.")
        print("Bootstrap performed no service restart or autostart.")
    print("Preflight passed.")
    print("Backup completed.")
    print("Install completed.")
    print("Healthcheck passed.")
    print("Autostart and device discovery were not enabled.")
    print("Netcraze proxy connections, policies, and default policy were not changed.")
    print("Firewall rules were not changed.")
    print("xkeen -start was not called.")


class SetupBootstrapSupervisor:
    """Forward setup signals while allowing standalone bootstrap recovery to finish."""

    cleanup_attempts = 2
    supervision_retry_sleep = 0.01

    def __init__(self) -> None:
        self.child: Optional[subprocess.Popen] = None
        self.first_signal: Optional[int] = None
        self._pending_signals: List[int] = []
        self._previous_handlers: Dict[int, object] = {}
        self._previous_signal_mask = None

    @staticmethod
    def handled_signals() -> Tuple[int, ...]:
        result = []
        for name in ("SIGINT", "SIGTERM", "SIGHUP"):
            signum = getattr(signal, name, None)
            if signum is not None and signum not in result:
                result.append(signum)
        return tuple(result)

    def _handle_signal(self, signum: int, _frame: Optional[FrameType]) -> None:
        if self.first_signal is None:
            self.first_signal = signum
        child = self.child
        if child is None:
            self._pending_signals.append(signum)
            return
        try:
            child.send_signal(signum)
        except OSError:
            pass

    def _block_signals(self) -> None:
        if (
            os.name == "posix"
            and threading.current_thread() is threading.main_thread()
            and hasattr(signal, "pthread_sigmask")
        ):
            self._previous_signal_mask = signal.pthread_sigmask(
                signal.SIG_BLOCK,
                self.handled_signals(),
            )

    def _restore_signal_mask(self) -> bool:
        if self._previous_signal_mask is None:
            return True
        previous = self._previous_signal_mask
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        except (OSError, RuntimeError, ValueError):
            return False
        self._previous_signal_mask = None
        return True

    def _install_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        for signum in self.handled_signals():
            self._previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, self._handle_signal)

    def _restore_handlers(self) -> bool:
        restored_all = True
        for signum, previous in list(self._previous_handlers.items()):
            try:
                signal.signal(signum, previous)
            except (OSError, RuntimeError, ValueError):
                restored_all = False
                continue
            self._previous_handlers.pop(signum, None)
        return restored_all

    @staticmethod
    def _child_result(returncode: int) -> int:
        if returncode < 0:
            return 128 + abs(returncode)
        return returncode

    def _result_code(
        self,
        raw_returncode: int,
        *,
        first_signal: Optional[int],
        spawn_failed: bool,
        supervision_failed: bool,
    ) -> int:
        child_returncode = self._child_result(raw_returncode)
        if child_returncode != 0:
            return child_returncode
        if first_signal is not None:
            return 128 + first_signal
        if spawn_failed:
            return 127
        if supervision_failed:
            return 1
        return 0

    def _restore_signal_state(self) -> Tuple[bool, bool]:
        had_failure = False
        for attempt in range(self.cleanup_attempts):
            mask_restored = self._restore_signal_mask()
            handlers_restored = self._restore_handlers()
            if mask_restored and handlers_restored:
                return True, had_failure
            had_failure = True
            if attempt + 1 < self.cleanup_attempts:
                time.sleep(self.supervision_retry_sleep)
        return False, True

    def _forward_pending_signals(self) -> None:
        child = self.child
        if child is None:
            self._pending_signals.clear()
            return
        for signum in self._pending_signals:
            try:
                child.send_signal(signum)
            except OSError:
                pass
        self._pending_signals.clear()

    def _wait_owned_child(self) -> Tuple[int, bool]:
        child = self.child
        if child is None:
            return 127, True

        supervision_failed = False
        while True:
            should_sleep = False
            try:
                raw_returncode = child.wait()
            except InterruptedError:
                continue
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                supervision_failed = True
                should_sleep = True
            except (RuntimeError, ValueError):
                supervision_failed = True
                should_sleep = True
            else:
                self.child = None
                return raw_returncode, supervision_failed

            try:
                polled = child.poll()
            except InterruptedError:
                continue
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                supervision_failed = True
                polled = None
                should_sleep = True
            except (RuntimeError, ValueError):
                supervision_failed = True
                polled = None
                should_sleep = True
            if polled is not None:
                self.child = None
                return polled, supervision_failed
            if should_sleep:
                time.sleep(self.supervision_retry_sleep)

    def run(self, step: CommandStep) -> SetupBootstrapResult:
        child_env = None
        raw_returncode = 127
        spawn_failed = False
        supervision_failed = False
        if step.remove_env_names:
            child_env = os.environ.copy()
            for name in step.remove_env_names:
                child_env.pop(name, None)

        try:
            self._block_signals()
            self._install_handlers()
            # Do not let the bootstrap child inherit the parent's temporary
            # blocked mask. Signals in the remaining spawn window are recorded
            # by the installed handlers and forwarded after child registration.
            if not self._restore_signal_mask():
                spawn_failed = True
                supervision_failed = True
            else:
                try:
                    self.child = subprocess.Popen(
                        list(step.command),
                        cwd=str(step.cwd) if step.cwd is not None else None,
                        env=child_env,
                        start_new_session=os.name == "posix",
                    )
                except (OSError, RuntimeError, ValueError):
                    spawn_failed = True
                    raw_returncode = 127
                else:
                    self._forward_pending_signals()
                    raw_returncode, wait_failed = self._wait_owned_child()
                    supervision_failed = supervision_failed or wait_failed
        except (OSError, RuntimeError, ValueError):
            spawn_failed = True
            raw_returncode = 127
        finally:
            restored, restore_failed = self._restore_signal_state()
            supervision_failed = supervision_failed or restore_failed or not restored
            self.child = None
            self._pending_signals.clear()

        first_signal = self.first_signal
        return SetupBootstrapResult(
            self._result_code(
                raw_returncode,
                first_signal=first_signal,
                spawn_failed=spawn_failed,
                supervision_failed=supervision_failed,
            ),
            first_signal=first_signal,
            spawn_failed=spawn_failed,
            supervision_failed=supervision_failed,
        )


def run_setup_bootstrap_apply(step: CommandStep) -> SetupBootstrapResult:
    return SetupBootstrapSupervisor().run(step)


def print_setup_bootstrap_failure(result: SetupBootstrapResult) -> None:
    if result.spawn_failed:
        print("routerkit: could not run bootstrap apply.", file=sys.stderr)
        return
    if result.supervision_failed:
        print(
            "routerkit: bootstrap supervision did not complete cleanly.",
            file=sys.stderr,
        )
        if result.first_signal is not None:
            try:
                signal_name = signal.Signals(result.first_signal).name
            except ValueError:
                signal_name = str(result.first_signal)
            print(
                f"routerkit: bootstrap apply ended after setup received {signal_name} "
                f"(exit code {result.returncode}).",
                file=sys.stderr,
            )
        elif result.returncode not in (0, 1):
            print(
                f"routerkit: bootstrap apply failed with exit code {result.returncode}.",
                file=sys.stderr,
            )
        print(
            "No preflight, backup, install, or healthcheck stage was started.",
            file=sys.stderr,
        )
        print(
            "Bootstrap package additions may remain; review the bootstrap output above.",
            file=sys.stderr,
        )
        return
    if result.first_signal is not None:
        try:
            signal_name = signal.Signals(result.first_signal).name
        except ValueError:
            signal_name = str(result.first_signal)
        print(
            f"routerkit: bootstrap apply ended after setup received {signal_name} "
            f"(exit code {result.returncode}).",
            file=sys.stderr,
        )
    else:
        print(
            f"routerkit: bootstrap apply failed with exit code {result.returncode}.",
            file=sys.stderr,
        )
    print(
        "No preflight, backup, install, or healthcheck stage was started.",
        file=sys.stderr,
    )
    print(
        "Bootstrap package additions may remain; review the bootstrap output above.",
        file=sys.stderr,
    )


def run_setup(args: argparse.Namespace, repo_root: Path, input_fn=input) -> int:
    validate_setup_args(args)
    repo_root = Path(repo_root)
    mode = setup_profile_mode(args)
    remove_env_names = (args.source_env,) if args.source_env else ()

    if args.dry_run:
        print(render_setup_pipeline(args))
        return 0

    lifecycle = SetupSignalLifecycle()
    lifecycle.block_during_workspace_creation()
    try:
        workspace = create_setup_workspace()
    except OSError:
        lifecycle.restore_signal_mask()
        print("routerkit: could not create the private setup workspace.", file=sys.stderr)
        return 1
    except BaseException:
        lifecycle.restore_signal_mask()
        raise

    code = 0
    cleanup_failed = False
    termination: Optional[SetupTermination] = None
    registration_failed = False
    try:
        try:
            lifecycle.install()
            lifecycle.restore_signal_mask()
            lifecycle.raise_if_requested()
            if mode == "source":
                try:
                    code = run_steps(
                        [build_profile_source_step(args, repo_root, workspace.profiles_path)],
                        setup_lifecycle=lifecycle,
                    )
                finally:
                    if args.source_env:
                        os.environ.pop(args.source_env, None)
            elif mode == "reuse":
                reuse_path = setup_reuse_path(args)
                if reuse_path is None:
                    raise AssertionError("reuse mode requires an explicit path")
                try:
                    secure_copy_reuse_profiles(Path(reuse_path), workspace.profiles_path)
                except PrivateFileError as exc:
                    print(f"routerkit: explicit profiles reuse was rejected: {exc}", file=sys.stderr)
                    code = 2
            else:
                code = run_steps(
                    [build_legacy_wizard_step(repo_root, workspace)],
                    setup_lifecycle=lifecycle,
                )

            lifecycle.raise_if_requested(code if code != 0 else None)
            if code == 0:
                try:
                    _ensure_private_setup_profiles(workspace.profiles_path)
                except PrivateFileError as exc:
                    print(f"routerkit: {exc}", file=sys.stderr)
                    code = 1
            lifecycle.raise_if_requested(code if code != 0 else None)
            if code == 0:
                code = run_steps(
                    [
                        build_generator_step(
                            repo_root,
                            workspace.profiles_path,
                            args.generated,
                            remove_env_names=remove_env_names,
                        )
                    ],
                    setup_lifecycle=lifecycle,
                )
            lifecycle.raise_if_requested(code if code != 0 else None)
        except SetupSignalRegistrationError as exc:
            registration_failed = True
            print(f"routerkit: could not install setup signal handlers: {exc}.", file=sys.stderr)
        except SetupTermination as exc:
            termination = exc
    finally:
        lifecycle.begin_cleanup()
        try:
            try:
                workspace.cleanup()
            except SetupCleanupError:
                cleanup_failed = True
                print(
                    "Warning: private setup workspace cleanup failed. "
                    f"Remove it manually: {workspace.directory}",
                    file=sys.stderr,
                )
        finally:
            lifecycle.restore()
            lifecycle.restore_signal_mask()

    if termination is None and lifecycle.requested_signum is not None:
        termination = SetupTermination(
            lifecycle.requested_signum,
            code if code != 0 else None,
        )
    if registration_failed:
        return 1
    if termination is not None:
        code = setup_termination_exit_code(termination)
        if not cleanup_failed:
            signal_name = signal.Signals(termination.signum).name
            print(
                f"routerkit: setup terminated by {signal_name}; "
                "private setup workspace was removed.",
                file=sys.stderr,
            )

    if cleanup_failed:
        return code if code != 0 else 1
    if code != 0:
        return code

    code = run_steps(
        [
            build_strict_plan_step(
                repo_root,
                args.generated,
                args.target_root,
                remove_env_names=remove_env_names,
            )
        ]
    )
    if code != 0:
        return code

    if not args.apply:
        print_setup_plan_summary(mode)
        return 0

    if args.bootstrap_apply:
        print_setup_bootstrap_warning()

    if not args.yes and not confirm_setup_apply(
        input_fn=input_fn,
        bootstrap_apply=args.bootstrap_apply,
    ):
        if args.bootstrap_apply:
            print("Cancelled before bootstrap and router apply.")
        else:
            print("Cancelled before router apply.")
        print("Generated local files may exist, but no router apply stages were started.")
        return 1

    if args.bootstrap_apply:
        bootstrap_result = run_setup_bootstrap_apply(
            build_setup_bootstrap_apply_step(
                repo_root,
                remove_env_names=remove_env_names,
            )
        )
        if (
            bootstrap_result.returncode != 0
            or bootstrap_result.first_signal is not None
            or bootstrap_result.spawn_failed
            or bootstrap_result.supervision_failed
        ):
            print_setup_bootstrap_failure(bootstrap_result)
            return bootstrap_result.returncode if bootstrap_result.returncode != 0 else 1

    apply_steps = build_router_apply_steps(
        args.generated,
        repo_root,
        remove_env_names=remove_env_names,
    )
    code = run_steps(apply_steps)
    if code == 0:
        print_setup_apply_summary(mode, bootstrap_apply=args.bootstrap_apply)
    return code


def _backup_completed(steps: Sequence[CommandStep], completed_names: Sequence[str]) -> bool:
    return any(step.name == "backup" for step in steps) and "backup" in completed_names


def _backup_skipped(steps: Sequence[CommandStep]) -> bool:
    return not any(step.name == "backup" for step in steps)


def _print_install_rollback_hint(steps: Sequence[CommandStep], completed_names: Sequence[str]) -> None:
    print("Rollback hint:", file=sys.stderr)
    if _backup_completed(steps, completed_names):
        print("- Backup was created by the previous safety step.", file=sys.stderr)
        print("- Use the backup output/path printed by scripts/backup.sh above.", file=sys.stderr)
    elif _backup_skipped(steps):
        print("- Backup was skipped; rollback files may not be available.", file=sys.stderr)
    else:
        print("- Backup did not complete; rollback files may not be available.", file=sys.stderr)
    print("- Backup archives may contain secrets.", file=sys.stderr)
    print("- Do not publish backup archives.", file=sys.stderr)


def _print_healthcheck_warning(steps: Sequence[CommandStep], completed_names: Sequence[str]) -> None:
    print("Warning:", file=sys.stderr)
    print("- Install may have completed but healthcheck failed.", file=sys.stderr)
    print("- Inspect logs.", file=sys.stderr)
    if _backup_completed(steps, completed_names):
        print("- Use the backup created before apply if rollback is needed.", file=sys.stderr)
    elif _backup_skipped(steps):
        print("- Backup was skipped; rollback files may not be available.", file=sys.stderr)
    else:
        print("- Backup did not complete; rollback files may not be available.", file=sys.stderr)
    print("- Do not publish backup archives.", file=sys.stderr)


def print_apply_summary(steps: Sequence[CommandStep]) -> None:
    step_names = {step.name for step in steps}
    print("Apply summary:")
    print("- Strict plan passed.")
    if "preflight" in step_names:
        print("- Preflight passed.")
    else:
        print("- Preflight was skipped.")
    if "backup" in step_names:
        print("- Backup completed.")
    else:
        print("- Backup was skipped; rollback files may not be available.")
    print("- Install completed.")
    if "healthcheck" in step_names:
        print("- Healthcheck passed.")
    else:
        print("- Healthcheck was skipped.")
    print("- Autostart was not enabled.")
    print("- Web UI policies were not changed.")
    print("- Firewall rules were not changed.")
    print("- xkeen -start was not called.")


def _run_owned_setup_step(
    step: CommandStep,
    lifecycle: SetupSignalLifecycle,
    child_env: Optional[Dict[str, str]],
) -> subprocess.CompletedProcess:
    lifecycle.begin_spawn()
    try:
        child = subprocess.Popen(
            list(step.command),
            stdout=subprocess.PIPE if step.suppress_output else None,
            stderr=subprocess.PIPE if step.suppress_output else None,
            text=True,
            cwd=str(step.cwd) if step.cwd is not None else None,
            env=child_env,
            start_new_session=os.name == "posix",
        )
    except BaseException:
        lifecycle._phase = "active"
        lifecycle.raise_if_requested()
        raise

    try:
        lifecycle.child_started(child, captures_output=step.suppress_output)
        stdout, stderr = child.communicate()
        returncode = child.returncode
        lifecycle.child_reaped()
        lifecycle.raise_if_requested(returncode if returncode != 0 else None)
        return subprocess.CompletedProcess(step.command, returncode, stdout, stderr)
    except SetupTermination as exc:
        known_returncode = child.poll()
        lifecycle.shutdown_active_child()
        if exc.prior_exit_code is None and known_returncode not in (None, 0):
            raise SetupTermination(exc.signum, known_returncode) from None
        raise
    except BaseException:
        lifecycle.shutdown_active_child()
        raise


def run_steps(
    steps: Sequence[CommandStep],
    dry_run: bool = False,
    repo_root: Optional[Path] = None,
    setup_lifecycle: Optional[SetupSignalLifecycle] = None,
) -> int:
    if dry_run:
        print(render_steps(steps, repo_root))
        return 0

    completed_names: List[str] = []
    for step in steps:
        child_env = None
        if step.remove_env_names:
            child_env = os.environ.copy()
            for name in step.remove_env_names:
                child_env.pop(name, None)
        try:
            if setup_lifecycle is not None:
                completed = _run_owned_setup_step(step, setup_lifecycle, child_env)
            elif step.suppress_output:
                completed = subprocess.run(
                    list(step.command),
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=str(step.cwd) if step.cwd is not None else None,
                    env=child_env,
                )
            else:
                completed = subprocess.run(
                    list(step.command),
                    check=False,
                    cwd=str(step.cwd) if step.cwd is not None else None,
                    env=child_env,
                )
        except OSError as exc:
            if step.suppress_output:
                print(f"routerkit: could not run {step.name}.", file=sys.stderr)
                print(
                    "Generator output was suppressed to avoid exposing subscription or profile details.",
                    file=sys.stderr,
                )
            elif step.name in {"profile source", "legacy wizard"}:
                print(f"routerkit: could not run {step.name}.", file=sys.stderr)
            else:
                print(f"routerkit: could not run {step.name}: {exc}", file=sys.stderr)
            if step.name == "install":
                _print_install_rollback_hint(steps, completed_names)
            elif step.name == "healthcheck":
                _print_healthcheck_warning(steps, completed_names)
            return 127

        if completed.returncode != 0:
            print(f"routerkit: {step.name} failed with exit code {completed.returncode}.", file=sys.stderr)
            if step.suppress_output:
                print(
                    "Generator output was suppressed to avoid exposing subscription or profile details.",
                    file=sys.stderr,
                )
            if step.name == "install":
                _print_install_rollback_hint(steps, completed_names)
            elif step.name == "healthcheck":
                _print_healthcheck_warning(steps, completed_names)
            return completed.returncode

        completed_names.append(step.name)
        if step.suppress_output:
            print("Generator completed. Secret-bearing output was suppressed.")

    return 0


def run_command(command: Sequence[str], dry_run: bool = False) -> int:
    return run_steps([CommandStep("command", list(command))], dry_run=dry_run)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified wrapper for netcraze-xray-routerkit helper scripts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the delegated command and exit without running it.",
    )
    parser.add_argument(
        "--repo-root",
        help="Repository root path; default: auto-detect from this script location.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    wizard = subparsers.add_parser(
        "wizard",
        help="Run the local profiles.json wizard.",
        description="Run the local profiles.json wizard.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    wizard.add_argument(
        "--profiles",
        default="profiles.json",
        help="Profiles file path; default: profiles.json.",
    )

    generate = subparsers.add_parser(
        "generate",
        help="Generate local Xray config fragments from profiles.json.",
        description="Generate local Xray config fragments from profiles.json.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    generate.add_argument("--profiles", required=True, help="Path to profiles JSON.")
    generate.add_argument("--out", required=True, help="Output directory.")

    profile_source = subparsers.add_parser(
        "profile-source",
        help="Safely acquire or parse VLESS payloads and select compatible nodes.",
        description="Safely acquire HTTPS sources or parse local VLESS payloads and select compatible nodes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    profile_source_modes = profile_source.add_mutually_exclusive_group()
    profile_source_modes.add_argument("--source-env", metavar="ENV_NAME")
    profile_source_modes.add_argument("--source-file", metavar="PATH")
    profile_source.add_argument("--output", default="profiles.json")
    profile_source.add_argument("--list", action="store_true")
    profile_source.add_argument("--json", action="store_true")
    profile_source.add_argument("--primary-index", type=int)
    profile_source.add_argument("--fallback-index", type=int, action="append", default=[])
    profile_source.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Acquire if needed, parse, and select nodes without writing a profiles file.",
    )
    profile_source.add_argument("--yes", action="store_true")
    profile_source.add_argument("--force", action="store_true")

    setup = subparsers.add_parser(
        "setup",
        help="Run the local setup plan; use --apply for confirmed router apply stages.",
        description=(
            "Acquire/select profiles into a private workspace, generate configs, and run strict "
            "planning before any optional apply stages."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    setup_source = setup.add_mutually_exclusive_group()
    setup_source.add_argument(
        "--source-env",
        metavar="ENV_NAME",
        help="Read the raw profile source from the named environment variable.",
    )
    setup_source.add_argument(
        "--source-file",
        metavar="PATH",
        help="Read the raw profile source from a protected owner-only file.",
    )
    setup.add_argument("--primary-index", type=int, help="Select this compatible node as primary.")
    setup.add_argument(
        "--fallback-index",
        type=int,
        action="append",
        default=[],
        help="Select a fallback node; repeat at most twice and use with --primary-index.",
    )
    setup.add_argument(
        "--reuse-profiles",
        metavar="PATH",
        help="Advanced: securely copy an explicit owner-only profiles file into setup workspace.",
    )
    setup.add_argument(
        "--profiles",
        dest="deprecated_profiles",
        metavar="PATH",
        help="Deprecated explicit alias for --reuse-profiles; there is no implicit profiles.json reuse.",
    )
    setup.add_argument(
        "--legacy-wizard",
        action="store_true",
        help="Compatibility: run the legacy profiles wizard inside the private setup workspace.",
    )
    setup.add_argument(
        "--generated",
        default="generated",
        help="Generated config directory; default: generated.",
    )
    setup.add_argument(
        "--target-root",
        default="/opt",
        help="Install target root for strict plan mode; default: /opt.",
    )
    setup.add_argument(
        "--apply",
        action="store_true",
        help="Continue through confirmed preflight, backup, install, and healthcheck stages.",
    )
    setup.add_argument(
        "--bootstrap-apply",
        action="store_true",
        help=(
            "Before preflight, run the confirmed standalone pinned-Xray bootstrap transaction. "
            "Requires --apply."
        ),
    )
    setup.add_argument(
        "--yes",
        action="store_true",
        help="Skip only the apply confirmation prompt; requires --apply.",
    )
    setup.add_argument(
        "--force-wizard",
        dest="deprecated_force_wizard",
        action="store_true",
        help="Deprecated alias for --legacy-wizard.",
    )
    setup.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=argparse.SUPPRESS,
        help=(
            "Render an abstract setup pipeline without secret-input reads, prompts, "
            "network, subprocesses, private workspace, or writes."
        ),
    )

    bootstrap = subparsers.add_parser(
        "bootstrap",
        help="Plan or explicitly apply the standalone pinned-Xray bootstrap transaction.",
        description="Keep bootstrap read-only by default; use --apply for the confirmed standalone transaction.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    bootstrap.add_argument(
        "--manifest",
        help="Pinned Xray manifest path; default: repository manifest.",
    )
    bootstrap.add_argument("--json", action="store_true", help="Render deterministic JSON output.")
    bootstrap.add_argument(
        "--inventory-file",
        help="Load synthetic inventory JSON instead of inspecting the environment.",
    )
    bootstrap.add_argument(
        "--target-root",
        default="/opt",
        help="Target root to inspect; default: /opt.",
    )
    bootstrap.add_argument(
        "--apply",
        action="store_true",
        help="Run the standalone package and pinned-Xray transaction after confirmation.",
    )
    bootstrap.add_argument(
        "--yes",
        action="store_true",
        help="Skip only the bootstrap confirmation prompt; requires --apply.",
    )
    bootstrap.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Explicit read-only planning; normal bootstrap mode is also read-only.",
    )

    plan = subparsers.add_parser(
        "plan",
        help="Preview install operations without changing router state.",
        description="Preview install operations without changing router state.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    plan.add_argument(
        "--generated",
        default="generated",
        help="Generated config directory; default: generated.",
    )
    plan.add_argument("--json", action="store_true", help="Render machine-readable JSON output.")
    plan.add_argument("--strict", action="store_true", help="Treat missing listen values as critical.")

    install = subparsers.add_parser(
        "install",
        help="Run a safe install plan by default; use --apply for the install script.",
        description="Run a safe install plan by default; use --apply for the install script.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    install.add_argument(
        "--generated",
        default="generated",
        help="Generated config directory; default: generated.",
    )
    install.add_argument(
        "--target-root",
        default="/opt",
        help="Install target root for plan mode; default: /opt.",
    )
    install.add_argument(
        "--apply",
        action="store_true",
        help="Run the install shell script instead of the plan-only mode.",
    )
    install.add_argument(
        "--enable-autostart",
        action="store_true",
        help="Reserved for a later explicit autostart flow.",
    )
    install.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Advanced/debug only: skip router preflight before apply.",
    )
    install.add_argument(
        "--skip-backup",
        action="store_true",
        help="Advanced/debug only: skip backup before apply; rollback may be harder.",
    )
    install.add_argument(
        "--skip-healthcheck",
        action="store_true",
        help="Advanced/debug only: skip healthcheck after apply.",
    )
    install.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )

    subparsers.add_parser(
        "preflight",
        help="Run router preflight checks. preflight is intended to run on Entware/router Linux, not on macOS/Windows.",
        description="preflight is intended to run on Entware/router Linux, not on macOS/Windows.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers.add_parser(
        "healthcheck",
        help="Run router healthcheck. healthcheck is intended to run on the router/Entware shell.",
        description="healthcheck is intended to run on the router/Entware shell.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers.add_parser(
        "backup",
        help="Run router backup. backup may collect secret-bearing router files; do not publish backup archives.",
        description="backup may collect secret-bearing router files; do not publish backup archives.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve() if args.repo_root else repo_root_from_script()
    try:
        if args.command == "setup":
            return run_setup(args, repo_root)
        steps = build_steps(args, repo_root)
    except RouterkitCliError as exc:
        print(f"routerkit: {exc}", file=sys.stderr)
        return exc.exit_code
    if args.command == "install" and not args.apply and not args.dry_run:
        print(INSTALL_PLAN_NOTICE)
    # Bootstrap and profile-source own their dry-run semantics. Profile-source
    # must still parse and validate while guaranteeing that it writes nothing.
    wrapper_dry_run = args.dry_run and args.command not in {"bootstrap", "profile-source"}
    code = run_steps(steps, dry_run=wrapper_dry_run, repo_root=repo_root)
    if code == 0 and args.command == "install" and args.apply and not args.dry_run:
        print_apply_summary(steps)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
