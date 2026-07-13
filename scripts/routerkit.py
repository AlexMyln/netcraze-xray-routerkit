#!/usr/bin/env python3
"""
Unified routerkit CLI wrapper.

This entrypoint only delegates to existing scripts. It does not implement
router runtime changes, Netcraze Web UI automation, firewall automation, or
hidden writes under /opt.
"""

from __future__ import annotations

import argparse
import os
import shlex
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from routerkit_private_io import (
    PrivateFileError,
    read_owner_only_text_file,
    write_private_text_exclusive,
)

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


class SetupCleanupError(Exception):
    pass


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
) -> List[CommandStep]:
    repo_root = Path(repo_root)
    steps: List[CommandStep] = []
    if include_preflight:
        steps.append(CommandStep("preflight", ["sh", _repo_script(repo_root, "preflight.sh")]))
    if include_backup:
        steps.append(CommandStep("backup", ["sh", _repo_script(repo_root, "backup.sh")], rollback_relevant=True))
    steps.append(CommandStep("install", ["sh", _repo_script(repo_root, "install-xray-direct.sh"), generated]))
    if include_healthcheck:
        steps.append(CommandStep("healthcheck", ["sh", _repo_script(repo_root, "healthcheck.sh")]))
    return steps


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
        command.extend(["--source-env", args.source_env])
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
    )


def build_strict_plan_step(repo_root: Path, generated: str, target_root: str) -> CommandStep:
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
    steps: List[CommandStep] = []
    if mode == "source":
        steps.append(build_profile_source_step(args, repo_root, private_profiles_path))
    elif mode == "legacy":
        if workspace is None:
            raise ValueError("legacy setup steps require a private workspace")
        steps.append(build_legacy_wizard_step(repo_root, workspace))
    steps.append(build_generator_step(repo_root, private_profiles_path, args.generated))
    steps.append(build_strict_plan_step(repo_root, args.generated, args.target_root))
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
) -> str:
    """Deprecated compatibility wrapper for callers using the older renderer."""

    del steps, repo_root
    args = argparse.Namespace(
        reuse_profiles=None,
        deprecated_profiles=None,
        legacy_wizard=False,
        deprecated_force_wizard=False,
        apply=include_apply,
        yes=not include_confirmation,
        generated=generated,
        target_root="/opt",
    )
    return render_setup_pipeline(args)


def confirm_setup_apply(input_fn=input) -> bool:
    answer = input_fn("Proceed with router apply stages? [y/N]: ").strip().lower()
    return answer in {"y", "yes"}


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
    print("Use --apply to continue through preflight, backup, install, and healthcheck.")
    print("Bootstrap apply, autostart, device discovery, and policy automation remain pending.")


def print_setup_apply_summary(mode: str = "source") -> None:
    print("Setup apply completed.")
    _print_setup_source_summary(mode)
    print("Strict plan passed.")
    print("Preflight passed.")
    print("Backup completed.")
    print("Install completed.")
    print("Healthcheck passed.")
    print("Autostart and device discovery were not enabled.")
    print("Netcraze proxy connections, policies, and default policy were not changed.")
    print("Firewall rules were not changed.")
    print("xkeen -start was not called.")


def run_setup(args: argparse.Namespace, repo_root: Path, input_fn=input) -> int:
    validate_setup_args(args)
    repo_root = Path(repo_root)
    mode = setup_profile_mode(args)

    if args.dry_run:
        print(render_setup_pipeline(args))
        return 0

    try:
        workspace = create_setup_workspace()
    except OSError:
        print("routerkit: could not create the private setup workspace.", file=sys.stderr)
        return 1

    code = 0
    cleanup_failed = False
    try:
        if mode == "source":
            code = run_steps([build_profile_source_step(args, repo_root, workspace.profiles_path)])
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
            code = run_steps([build_legacy_wizard_step(repo_root, workspace)])

        if code == 0:
            try:
                _ensure_private_setup_profiles(workspace.profiles_path)
            except PrivateFileError as exc:
                print(f"routerkit: {exc}", file=sys.stderr)
                code = 1
        if code == 0:
            code = run_steps(
                [build_generator_step(repo_root, workspace.profiles_path, args.generated)]
            )
    finally:
        try:
            workspace.cleanup()
        except SetupCleanupError:
            cleanup_failed = True
            print(
                "Warning: private setup workspace cleanup failed. "
                f"Remove it manually: {workspace.directory}",
                file=sys.stderr,
            )

    if cleanup_failed:
        return code if code != 0 else 1
    if code != 0:
        return code

    code = run_steps([build_strict_plan_step(repo_root, args.generated, args.target_root)])
    if code != 0:
        return code

    if not args.apply:
        print_setup_plan_summary(mode)
        return 0

    if not args.yes and not confirm_setup_apply(input_fn=input_fn):
        print("Cancelled before router apply.")
        print("Generated local files may exist, but no router apply stages were started.")
        return 1

    apply_steps = build_router_apply_steps(args.generated, repo_root)
    code = run_steps(apply_steps)
    if code == 0:
        print_setup_apply_summary(mode)
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


def run_steps(steps: Sequence[CommandStep], dry_run: bool = False, repo_root: Optional[Path] = None) -> int:
    if dry_run:
        print(render_steps(steps, repo_root))
        return 0

    completed_names: List[str] = []
    for step in steps:
        try:
            if step.suppress_output:
                completed = subprocess.run(
                    list(step.command),
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=str(step.cwd) if step.cwd is not None else None,
                )
            else:
                completed = subprocess.run(
                    list(step.command),
                    check=False,
                    cwd=str(step.cwd) if step.cwd is not None else None,
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
        help="Render an abstract no-prompt, no-read, no-network, no-write setup pipeline.",
    )

    bootstrap = subparsers.add_parser(
        "bootstrap",
        help="Inspect prerequisites and print a read-only pinned-Xray bootstrap plan.",
        description="Validate the pinned Xray manifest and inspect prerequisites without changing the system.",
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
        help="Reserved; delegated bootstrap will reject it without changes.",
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
