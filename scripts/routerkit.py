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


class RouterkitCliError(Exception):
    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def _repo_script(repo_root: Path, name: str) -> str:
    return str(repo_root / "scripts" / name)


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
        if args.enable_autostart:
            raise RouterkitCliError(AUTOSTART_RESERVED_MESSAGE, exit_code=2)
        if args.apply:
            return ["sh", _repo_script(repo_root, "install-xray-direct.sh"), args.generated]
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


def run_command(command: Sequence[str], dry_run: bool = False) -> int:
    if dry_run:
        print(shlex.join(list(command)))
        return 0

    try:
        completed = subprocess.run(list(command), check=False)
    except OSError as exc:
        print(f"routerkit: could not run command: {exc}", file=sys.stderr)
        return 127

    return completed.returncode


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
        command = build_command(args, repo_root)
    except RouterkitCliError as exc:
        print(f"routerkit: {exc}", file=sys.stderr)
        return exc.exit_code
    if args.command == "install" and not args.apply and not args.dry_run:
        print(INSTALL_PLAN_NOTICE)
    return run_command(command, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
