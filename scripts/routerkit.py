#!/usr/bin/env python3
"""
Unified routerkit CLI wrapper.

This entrypoint only delegates to existing scripts. It does not implement
router runtime changes, Netcraze Web UI automation, firewall automation, or
hidden writes under /opt.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

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
    if args.yes and not args.apply:
        raise RouterkitCliError(SETUP_YES_REQUIRES_APPLY_MESSAGE, exit_code=2)
    if args.apply and args.target_root != "/opt":
        raise RouterkitCliError(SETUP_APPLY_TARGET_ROOT_MESSAGE, exit_code=2)


def build_command(args: argparse.Namespace, repo_root: Path) -> List[str]:
    repo_root = Path(repo_root)

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


def build_setup_steps(
    args: argparse.Namespace,
    repo_root: Path,
    profiles_exists: bool,
) -> List[CommandStep]:
    validate_setup_args(args)
    repo_root = Path(repo_root)
    steps: List[CommandStep] = []
    if args.force_wizard or not profiles_exists:
        steps.append(
            CommandStep(
                "wizard",
                [
                    sys.executable,
                    _repo_script(repo_root, "routerkit-wizard.py"),
                    "--profiles",
                    args.profiles,
                    "--no-generator-prompt",
                ],
            )
        )
    steps.extend(
        [
            CommandStep(
                "generator",
                [
                    sys.executable,
                    _repo_script(repo_root, "generate-xray-profiles.py"),
                    "--profiles",
                    args.profiles,
                    "--out",
                    args.generated,
                ],
                suppress_output=True,
            ),
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
            ),
        ]
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


def render_setup_steps(
    steps: Sequence[CommandStep],
    repo_root: Path,
    generated: str,
    include_apply: bool,
    include_confirmation: bool,
) -> str:
    lines = ["Would run setup pipeline:"]
    index = 1
    for step in steps:
        command = [_render_token(token, repo_root) for token in step.command]
        lines.append(f"{index}. {shlex.join(command)}")
        index += 1
    if include_apply:
        if include_confirmation:
            lines.append(f"{index}. confirmation gate")
            index += 1
        for step in build_router_apply_steps(generated, repo_root):
            command = [_render_token(token, repo_root) for token in step.command]
            lines.append(f"{index}. {shlex.join(command)}")
            index += 1
    return "\n".join(lines)


def confirm_setup_apply(input_fn=input) -> bool:
    answer = input_fn("Proceed with router apply stages? [y/N]: ").strip().lower()
    return answer in {"y", "yes"}


def print_setup_plan_summary() -> None:
    print("Setup plan completed.")
    print("No router apply steps were executed.")
    print("Use --apply to continue through preflight, backup, install, and healthcheck.")
    print("Autostart and Netcraze policy automation are not implemented in this phase.")


def print_setup_apply_summary() -> None:
    print("Setup apply completed.")
    print("Profiles were generated locally.")
    print("Strict plan passed.")
    print("Preflight passed.")
    print("Backup completed.")
    print("Install completed.")
    print("Healthcheck passed.")
    print("Autostart was not enabled.")
    print("Netcraze proxy connections and policies were not changed.")
    print("Firewall rules were not changed.")
    print("xkeen -start was not called.")


def run_setup(args: argparse.Namespace, repo_root: Path, input_fn=input) -> int:
    validate_setup_args(args)
    repo_root = Path(repo_root)
    profiles_exists = Path(args.profiles).exists()
    steps = build_setup_steps(args, repo_root, profiles_exists)
    reusing_profiles = profiles_exists and not args.force_wizard

    if reusing_profiles:
        print(f"Reusing existing profiles file: {args.profiles}")

    if args.dry_run:
        print(
            render_setup_steps(
                steps,
                repo_root,
                args.generated,
                include_apply=args.apply,
                include_confirmation=args.apply and not args.yes,
            )
        )
        return 0

    code = run_steps(steps)
    if code != 0:
        return code

    if not args.apply:
        print_setup_plan_summary()
        return 0

    if not args.yes and not confirm_setup_apply(input_fn=input_fn):
        print("Cancelled before router apply.")
        print("Generated local files may exist, but no router apply stages were started.")
        return 1

    apply_steps = build_router_apply_steps(args.generated, repo_root)
    code = run_steps(apply_steps)
    if code == 0:
        print_setup_apply_summary()
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
                )
            else:
                completed = subprocess.run(list(step.command), check=False)
        except OSError as exc:
            if step.suppress_output:
                print(f"routerkit: could not run {step.name}.", file=sys.stderr)
                print(
                    "Generator output was suppressed to avoid exposing subscription or profile details.",
                    file=sys.stderr,
                )
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

    setup = subparsers.add_parser(
        "setup",
        help="Run the local setup plan; use --apply for confirmed router apply stages.",
        description="Run wizard/reuse, generation, and strict planning before any optional apply stages.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    setup.add_argument(
        "--profiles",
        default="profiles.json",
        help="Profiles file path; default: profiles.json.",
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
        action="store_true",
        help="Run the wizard even when the profiles file already exists.",
    )
    setup.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
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
    code = run_steps(steps, dry_run=args.dry_run, repo_root=repo_root)
    if code == 0 and args.command == "install" and args.apply and not args.dry_run:
        print_apply_summary(steps)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
