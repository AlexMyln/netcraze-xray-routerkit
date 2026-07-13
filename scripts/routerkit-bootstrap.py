#!/usr/bin/env python3
"""Build a read-only bootstrap plan or run the explicit standalone transaction."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse


SCRIPT_DIRECTORY = str(Path(__file__).resolve().parent)
if SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPT_DIRECTORY)


MANIFEST_SCHEMA_VERSION = 1
INVENTORY_SCHEMA_VERSION = 1
REPOSITORY = "XTLS/Xray-core"
GITHUB_HOST = "github.com"
DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "manifests" / "xray-artifacts.json"
DEFAULT_TARGET_ROOT = "/opt"
YES_REQUIRES_APPLY = "bootstrap --yes requires --apply."
INVENTORY_APPLY_CONFLICT = "bootstrap --inventory-file conflicts with --apply."
APPLY_TARGET_ROOT = "bootstrap --apply supports only literal --target-root /opt."

SHELL_COMMANDS = ("sh", "uname")
LATER_TOOL_PACKAGES = {
    "curl": "curl",
    "unzip": "unzip",
    "sha256sum": "coreutils-sha256sum",
    "python3": "python3",
}
BASE_PACKAGES = ("ca-bundle",)
LATER_COMMANDS = tuple(LATER_TOOL_PACKAGES)
LATER_PACKAGES = BASE_PACKAGES + tuple(dict.fromkeys(LATER_TOOL_PACKAGES.values()))
OPTIONAL_DIAGNOSTICS = ("jq",)
KNOWN_COMMANDS = SHELL_COMMANDS + LATER_COMMANDS + OPTIONAL_DIAGNOSTICS + ("opkg",)
INIT_SCRIPT_NAMES = ("S23xray-direct", "S24xray")

EVENTUAL_ACTIONS = [
    "install missing prerequisite packages",
    "download the pinned artifact",
    "verify SHA-256 before replacement",
    "preserve the existing binary",
    "validate the candidate binary",
    "atomically replace only after validation",
]

WILL_NOT = [
    "update package indexes",
    "install packages",
    "download Xray",
    "modify /opt",
    "replace Xray",
    "enable autostart",
    "start or stop services",
    "call xkeen -start",
    "touch firewall or policies",
]


class BootstrapError(Exception):
    """Base error for manifest, inventory, and environment failures."""


class BootstrapConfigurationError(BootstrapError):
    pass


class ManifestValidationError(BootstrapError):
    pass


class InventoryValidationError(BootstrapError):
    pass


class UnsupportedEnvironmentError(BootstrapError):
    pass


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ManifestValidationError(f"{label} must be an object")
    return value


def _require_nonempty_string(mapping: Mapping[str, Any], field: str, label: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise ManifestValidationError(f"{label}.{field} must be a non-empty string")
    return value


def _validate_https_url(url: str, label: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ManifestValidationError(f"{label} must use HTTPS")
    if parsed.hostname != GITHUB_HOST or parsed.port is not None or parsed.username is not None:
        raise ManifestValidationError(f"{label} must use github.com without credentials or a port")
    if parsed.query or parsed.fragment:
        raise ManifestValidationError(f"{label} must not contain a query or fragment")
    if "/latest/" in parsed.path.lower():
        raise ManifestValidationError(f"{label} must not use /latest/")


def load_manifest(path: Path) -> Dict[str, Any]:
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise BootstrapConfigurationError(f"could not read manifest {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestValidationError(f"could not load manifest {path}: {exc}") from exc
    validate_manifest(data)
    return data


def validate_manifest(data: Any) -> None:
    root = _require_mapping(data, "manifest")
    if root.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ManifestValidationError(
            f"unsupported manifest schema_version: {root.get('schema_version')!r}"
        )

    upstream = _require_mapping(root.get("upstream"), "upstream")
    repository = _require_nonempty_string(upstream, "repository", "upstream")
    tag = _require_nonempty_string(upstream, "release_tag", "upstream")
    release_url = _require_nonempty_string(upstream, "release_url", "upstream")
    checksum_url = _require_nonempty_string(upstream, "checksum_source_url", "upstream")

    if repository != REPOSITORY:
        raise ManifestValidationError(f"upstream.repository must be {REPOSITORY}")
    if not re.fullmatch(r"v[0-9]+(?:\.[0-9]+)+", tag):
        raise ManifestValidationError("upstream.release_tag must be an explicit version tag")
    _validate_https_url(release_url, "upstream.release_url")
    _validate_https_url(checksum_url, "upstream.checksum_source_url")
    expected_release_path = f"/{REPOSITORY}/releases/tag/{tag}"
    if urlparse(release_url).path != expected_release_path:
        raise ManifestValidationError("release URL repository or tag does not match manifest fields")

    artifacts = _require_mapping(root.get("artifacts"), "artifacts")
    if not artifacts:
        raise ManifestValidationError("artifacts must not be empty")
    if "linux-arm64" not in artifacts:
        raise ManifestValidationError("required linux-arm64 architecture mapping is missing")
    if set(artifacts) != {"linux-arm64"}:
        raise ManifestValidationError("schema version 1 supports only the linux-arm64 artifact")

    seen_aliases: Dict[Tuple[str, str], str] = {}
    for artifact_key, raw_artifact in artifacts.items():
        if not isinstance(artifact_key, str) or not artifact_key:
            raise ManifestValidationError("artifact keys must be non-empty strings")
        artifact = _require_mapping(raw_artifact, f"artifacts.{artifact_key}")
        os_name = _require_nonempty_string(artifact, "os", f"artifacts.{artifact_key}")
        canonical_arch = _require_nonempty_string(
            artifact, "canonical_arch", f"artifacts.{artifact_key}"
        )
        filename = _require_nonempty_string(artifact, "filename", f"artifacts.{artifact_key}")
        download_url = _require_nonempty_string(
            artifact, "download_url", f"artifacts.{artifact_key}"
        )
        checksum = _require_nonempty_string(artifact, "sha256", f"artifacts.{artifact_key}")
        aliases = artifact.get("uname_machines")

        if os_name != "Linux":
            raise ManifestValidationError(f"artifacts.{artifact_key}.os must be Linux")
        if artifact_key == "linux-arm64" and canonical_arch != "arm64":
            raise ManifestValidationError("linux-arm64 canonical_arch must be arm64")
        if not isinstance(aliases, list) or not aliases:
            raise ManifestValidationError(
                f"artifacts.{artifact_key}.uname_machines must be a non-empty array"
            )
        if not all(isinstance(alias, str) and alias for alias in aliases):
            raise ManifestValidationError(
                f"artifacts.{artifact_key}.uname_machines entries must be strings"
            )
        if artifact_key == "linux-arm64" and set(aliases) != {"aarch64", "arm64"}:
            raise ManifestValidationError(
                "linux-arm64 uname_machines must contain only aarch64 and arm64"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ManifestValidationError(
                f"artifacts.{artifact_key}.sha256 must be 64 lowercase hexadecimal characters"
            )
        if Path(filename).name != filename or not filename.endswith(".zip"):
            raise ManifestValidationError(
                f"artifacts.{artifact_key}.filename must be an immutable ZIP asset name"
            )

        _validate_https_url(download_url, f"artifacts.{artifact_key}.download_url")
        expected_download_path = f"/{REPOSITORY}/releases/download/{tag}/{filename}"
        if urlparse(download_url).path != expected_download_path:
            raise ManifestValidationError(
                f"artifacts.{artifact_key}.download_url repository, tag, or filename mismatch"
            )

        for alias in aliases:
            alias_key = (os_name, alias)
            if alias_key in seen_aliases:
                raise ManifestValidationError(
                    f"duplicate machine alias {alias!r} in {seen_aliases[alias_key]} and {artifact_key}"
                )
            seen_aliases[alias_key] = artifact_key

    pinned_filename = artifacts["linux-arm64"]["filename"]
    expected_checksum_path = (
        f"/{REPOSITORY}/releases/download/{tag}/{pinned_filename}.dgst"
    )
    if urlparse(checksum_url).path != expected_checksum_path:
        raise ManifestValidationError(
            "checksum source URL repository, tag, or pinned asset filename mismatch"
        )

    required_aliases = {("Linux", "aarch64"), ("Linux", "arm64")}
    if not required_aliases.issubset(seen_aliases):
        raise ManifestValidationError("linux-arm64 must map both aarch64 and arm64")


def resolve_artifact(
    manifest: Mapping[str, Any], os_name: str, machine: str
) -> Tuple[str, Mapping[str, Any]]:
    for artifact_key, artifact in manifest["artifacts"].items():
        if artifact["os"] == os_name and machine in artifact["uname_machines"]:
            return artifact_key, artifact
    raise UnsupportedEnvironmentError(
        f"unsupported environment: OS {os_name!r}, machine {machine!r}; "
        "only Linux aarch64/arm64 is supported"
    )


def _command_record(name: str) -> Dict[str, Any]:
    path = shutil.which(name)
    return {"available": path is not None, "path": path}


def _run_read_only(command: Sequence[str], timeout: float = 5.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(command),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )


def _xray_version(path: Path) -> Tuple[Optional[str], Optional[str]]:
    try:
        completed = _run_read_only([str(path), "version"])
    except (OSError, subprocess.TimeoutExpired):
        return None, "unavailable"
    if completed.returncode != 0:
        return None, "unavailable"
    first_line = next((line.strip() for line in completed.stdout.splitlines() if line.strip()), "")
    if not first_line.startswith("Xray "):
        return None, "unavailable"
    safe_line = "".join(char for char in first_line[:200] if char.isprintable())
    return safe_line or None, None


def _package_status(opkg_path: Optional[str], package: str) -> Dict[str, Any]:
    if not opkg_path:
        return {"installed": False, "query": "opkg unavailable"}
    try:
        completed = _run_read_only([opkg_path, "status", package])
    except (OSError, subprocess.TimeoutExpired):
        return {"installed": False, "query": "unavailable"}
    installed = completed.returncode == 0 and "Status: install ok installed" in completed.stdout
    return {"installed": installed, "query": "opkg status"}


def collect_inventory(target_root: Path = Path(DEFAULT_TARGET_ROOT)) -> Dict[str, Any]:
    target_root = Path(target_root)
    uname = os.uname()
    commands = {name: _command_record(name) for name in KNOWN_COMMANDS}
    xray_path = target_root / "sbin" / "xray"
    xray_exists = xray_path.exists()
    xray_executable = xray_exists and os.access(str(xray_path), os.X_OK)
    version: Optional[str] = None
    version_error: Optional[str] = None
    if xray_executable:
        version, version_error = _xray_version(xray_path)

    return {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "os": uname.sysname,
        "machine": uname.machine,
        "target_root": {
            "path": str(target_root),
            "exists": target_root.exists(),
            "is_directory": target_root.is_dir(),
            "writable": target_root.is_dir() and os.access(str(target_root), os.W_OK),
        },
        "commands": commands,
        # Default planning and pre-confirmation apply inventory never invoke
        # the package manager. Exact package state is queried only after apply
        # confirmation through the fixed /opt-scoped opkg policy.
        "packages": {
            package: {"installed": False, "query": "not queried"}
            for package in LATER_PACKAGES
        },
        "xray": {
            "path": str(xray_path),
            "exists": xray_exists,
            "executable": xray_executable,
            "version": version,
            "version_error": version_error,
        },
        "configs": {
            "path": str(target_root / "etc" / "xray" / "configs"),
            "exists": (target_root / "etc" / "xray" / "configs").is_dir(),
        },
        "init_scripts": {
            str(target_root / "etc" / "init.d" / name): {
                "exists": (target_root / "etc" / "init.d" / name).exists()
            }
            for name in INIT_SCRIPT_NAMES
        },
    }


def load_inventory_file(path: Path) -> Dict[str, Any]:
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise BootstrapConfigurationError(f"could not read inventory {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise InventoryValidationError(f"could not load inventory {path}: {exc}") from exc
    validate_inventory(data)
    return data


def validate_inventory(data: Any) -> None:
    if not isinstance(data, dict):
        raise InventoryValidationError("inventory must be an object")
    if data.get("schema_version") != INVENTORY_SCHEMA_VERSION:
        raise InventoryValidationError("unsupported inventory schema_version")
    for field in ("os", "machine"):
        if not isinstance(data.get(field), str) or not data[field]:
            raise InventoryValidationError(f"inventory.{field} must be a non-empty string")
    for field in ("target_root", "commands", "packages", "xray", "configs", "init_scripts"):
        if not isinstance(data.get(field), dict):
            raise InventoryValidationError(f"inventory.{field} must be an object")
    target = data["target_root"]
    if not isinstance(target.get("path"), str):
        raise InventoryValidationError("inventory.target_root.path must be a string")
    for field in ("exists", "is_directory", "writable"):
        if not isinstance(target.get(field), bool):
            raise InventoryValidationError(f"inventory.target_root.{field} must be boolean")
    for name in KNOWN_COMMANDS:
        record = data["commands"].get(name)
        if not isinstance(record, dict) or not isinstance(record.get("available"), bool):
            raise InventoryValidationError(f"inventory.commands.{name} is missing or invalid")
        if record.get("path") is not None and not isinstance(record.get("path"), str):
            raise InventoryValidationError(f"inventory.commands.{name}.path must be string or null")
    for package in LATER_PACKAGES:
        record = data["packages"].get(package)
        if not isinstance(record, dict) or not isinstance(record.get("installed"), bool):
            raise InventoryValidationError(f"inventory.packages.{package} is missing or invalid")
    xray = data["xray"]
    if not isinstance(xray.get("path"), str):
        raise InventoryValidationError("inventory.xray.path must be a string")
    for field in ("exists", "executable"):
        if not isinstance(xray.get(field), bool):
            raise InventoryValidationError(f"inventory.xray.{field} must be boolean")
    if xray.get("version") is not None and not isinstance(xray.get("version"), str):
        raise InventoryValidationError("inventory.xray.version must be string or null")
    configs = data["configs"]
    if not isinstance(configs.get("path"), str) or not isinstance(configs.get("exists"), bool):
        raise InventoryValidationError("inventory.configs path/exists fields are invalid")
    for path, record in data["init_scripts"].items():
        if not isinstance(path, str) or not isinstance(record, dict):
            raise InventoryValidationError("inventory.init_scripts entries are invalid")
        if not isinstance(record.get("exists"), bool):
            raise InventoryValidationError(
                f"inventory.init_scripts.{path}.exists must be boolean"
            )


def build_bootstrap_plan(
    inventory: Mapping[str, Any], manifest: Mapping[str, Any], target_root: Path
) -> Dict[str, Any]:
    validate_inventory(inventory)
    artifact_key, artifact = resolve_artifact(
        manifest, str(inventory["os"]), str(inventory["machine"])
    )
    requested_target = str(Path(target_root))
    inventory_target = inventory["target_root"]
    if inventory_target["path"] != requested_target:
        raise InventoryValidationError(
            f"inventory target root {inventory_target['path']!r} does not match {requested_target!r}"
        )

    missing_shell = [
        name for name in SHELL_COMMANDS if not inventory["commands"][name]["available"]
    ]
    missing_later_commands = [
        name for name in LATER_COMMANDS if not inventory["commands"][name]["available"]
    ]
    missing_packages = [
        name for name in LATER_PACKAGES if not inventory["packages"][name]["installed"]
    ]
    warnings: List[str] = []
    if missing_shell:
        warnings.append("missing shell commands: " + ", ".join(missing_shell))
    if missing_later_commands:
        warnings.append(
            "commands needed by a later apply stage are missing: "
            + ", ".join(missing_later_commands)
        )
    if missing_packages:
        warnings.append(
            "Entware packages needed by later setup stages are missing or unconfirmed: "
            + ", ".join(missing_packages)
        )
    if not inventory_target["exists"]:
        warnings.append(f"target root {requested_target} does not exist")
    elif not inventory_target["is_directory"]:
        warnings.append(f"target root {requested_target} is not a directory")
    elif not inventory_target["writable"]:
        warnings.append(f"target root {requested_target} does not appear writable")

    return {
        "schema_version": 1,
        "title": "RouterKit bootstrap plan",
        "mode": "read-only",
        "environment": {
            "os": inventory["os"],
            "machine": inventory["machine"],
            "target_root": dict(inventory_target),
            "opkg": dict(inventory["commands"]["opkg"]),
            "commands": {
                name: {
                    "available": inventory["commands"][name]["available"],
                    "path": inventory["commands"][name].get("path"),
                }
                for name in KNOWN_COMMANDS
            },
            "packages": {
                name: {"installed": inventory["packages"][name]["installed"]}
                for name in LATER_PACKAGES
            },
            "xray": {
                "path": inventory["xray"].get("path", f"{requested_target}/sbin/xray"),
                "exists": inventory["xray"]["exists"],
                "executable": inventory["xray"]["executable"],
                "version": inventory["xray"].get("version"),
            },
            "configs": {
                "path": inventory["configs"].get(
                    "path", f"{requested_target}/etc/xray/configs"
                ),
                "exists": bool(inventory["configs"].get("exists", False)),
            },
            "init_scripts": {
                path: {"exists": bool(record.get("exists", False))}
                for path, record in sorted(inventory["init_scripts"].items())
                if isinstance(path, str) and isinstance(record, dict)
            },
        },
        "pinned_artifact": {
            "key": artifact_key,
            "release": manifest["upstream"]["release_tag"],
            "architecture": artifact["canonical_arch"],
            "filename": artifact["filename"],
            "sha256": artifact["sha256"],
            "download_url": artifact["download_url"],
            "checksum_source_url": manifest["upstream"]["checksum_source_url"],
        },
        "requirements": {
            "shell_commands_required_before_bootstrap": list(SHELL_COMMANDS),
            "later_commands_required_by_later_stages": list(LATER_COMMANDS),
            "later_command_packages": dict(LATER_TOOL_PACKAGES),
            "base_entware_packages": list(BASE_PACKAGES),
            "entware_packages_required_by_later_stages": list(LATER_PACKAGES),
            "optional_diagnostics": list(OPTIONAL_DIAGNOSTICS),
            "missing_shell_commands": missing_shell,
            "missing_later_commands": missing_later_commands,
            "missing_packages": missing_packages,
        },
        "warnings": warnings,
        "eventual_actions": list(EVENTUAL_ACTIONS),
        "will_not": list(WILL_NOT),
    }


def _availability(record: Mapping[str, Any]) -> str:
    return "available" if record.get("available") else "missing"


def render_text_plan(plan: Mapping[str, Any]) -> str:
    env = plan["environment"]
    target = env["target_root"]
    xray = env["xray"]
    artifact = plan["pinned_artifact"]
    version = xray.get("version") or "unavailable"
    lines = [
        str(plan["title"]),
        "",
        "Mode:",
        "- read-only planning (default and --dry-run perform the same non-mutating checks)",
        "",
        "Environment:",
        f"- OS: {env['os']}",
        f"- machine: {env['machine']}",
        f"- target root: {target['path']}",
        f"- target root exists/directory/writable: {target['exists']}/{target['is_directory']}/{target['writable']}",
        f"- opkg: {_availability(env['opkg'])}",
        f"- existing Xray: {'present' if xray['exists'] else 'missing'}",
        f"- existing Xray executable: {xray['executable']}",
        f"- existing Xray version: {version}",
        "",
        "Pinned artifact:",
        f"- release: {artifact['release']}",
        f"- architecture: {artifact['key']}",
        f"- filename: {artifact['filename']}",
        f"- SHA-256: {artifact['sha256']}",
        f"- immutable official URL: {artifact['download_url']}",
        f"- official checksum source: {artifact['checksum_source_url']}",
        "",
        "Required before later apply:",
        "- shell commands: "
        + ", ".join(plan["requirements"]["shell_commands_required_before_bootstrap"]),
        "- later command packages:",
    ]
    lines.extend(
        f"  {command} -> {package}"
        for command, package in plan["requirements"]["later_command_packages"].items()
    )
    lines.append(
        "- base packages: "
        + ", ".join(plan["requirements"]["base_entware_packages"])
    )
    if plan["warnings"]:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in plan["warnings"])
    lines.extend(["", "Would eventually:"])
    lines.extend(f"- {item};" for item in plan["eventual_actions"])
    lines.extend(["", "Would NOT in this release:"])
    lines.extend(f"- {item};" for item in plan["will_not"])
    return "\n".join(lines) + "\n"


def render_apply_preview(plan: Mapping[str, Any]) -> str:
    artifact = plan["pinned_artifact"]
    return "\n".join(
        [
            "RouterKit bootstrap apply plan",
            "",
            "Mode:",
            "- no-write apply preview",
            "",
            "After explicit apply confirmation the transaction would:",
            "- query only the fixed required Entware packages",
            "- install only missing packages: " + ", ".join(LATER_PACKAGES),
            "- acquire and checksum the manifest-pinned {} archive".format(
                artifact["release"]
            ),
            "- extract and validate only the root xray candidate",
            "- verify or create a rollback backup of an existing Xray binary",
            "- atomically replace, validate, and roll back on failure",
            "- retain a restrictive provenance receipt and verified backup",
            "",
            "This preview performs no package command, network request, staging, or write.",
        ]
    ) + "\n"


def render_transaction_summary(plan: Mapping[str, Any]) -> str:
    artifact = plan["pinned_artifact"]
    return "\n".join(
        [
            "RouterKit bootstrap apply",
            "- target: /opt/sbin/xray",
            "- fixed packages: " + ", ".join(LATER_PACKAGES),
            "- pinned release: {}".format(artifact["release"]),
            "- archive SHA-256: {}".format(artifact["sha256"]),
            "- package additions are not automatically removed on later failure",
            "- Xray replacement uses verified backup, atomic replace, and rollback",
            "- no service, autostart, firewall, policy, or setup action is performed",
        ]
    )


def render_apply_result(result: Mapping[str, Any]) -> str:
    lines = [
        "RouterKit bootstrap apply completed",
        "- pinned release: {}".format(result["artifact_release"]),
        "- packages already installed: {}".format(
            ", ".join(result["packages_already_installed"]) or "none"
        ),
        "- packages installed now: {}".format(
            ", ".join(result["packages_installed"]) or "none"
        ),
        "- idempotent no-op: {}".format(str(result["idempotent_noop"]).lower()),
        "- replacement performed: {}".format(
            str(result["replacement_performed"]).lower()
        ),
        "- post-install verified: {}".format(
            str(result["post_install_verified"]).lower()
        ),
    ]
    if result.get("backup_path"):
        lines.append("- rollback backup: {}".format(result["backup_path"]))
    lines.extend(
        [
            "- package installs are additive and are not automatically rolled back",
            "- no service restart or autostart action was performed",
        ]
    )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan or explicitly apply the standalone RouterKit bootstrap transaction."
    )
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help="Pinned Xray manifest path; default: repository manifest.",
    )
    parser.add_argument("--json", action="store_true", help="Render deterministic JSON output.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicitly request read-only planning; normal mode is also read-only.",
    )
    parser.add_argument(
        "--inventory-file",
        help="Load synthetic inventory JSON and execute no environment subprocesses.",
    )
    parser.add_argument(
        "--target-root",
        default=DEFAULT_TARGET_ROOT,
        help="Target root to inspect; default: /opt.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Run the standalone package and pinned-Xray transaction after confirmation.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip only the bootstrap apply confirmation; requires --apply.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.yes and not args.apply:
        print(YES_REQUIRES_APPLY, file=sys.stderr)
        return 2
    if args.apply and args.inventory_file:
        print(INVENTORY_APPLY_CONFLICT, file=sys.stderr)
        return 2
    if args.apply and args.target_root != DEFAULT_TARGET_ROOT:
        print(APPLY_TARGET_ROOT, file=sys.stderr)
        return 2

    try:
        manifest = load_manifest(Path(args.manifest))
        if args.inventory_file:
            inventory = load_inventory_file(Path(args.inventory_file))
        else:
            inventory = collect_inventory(Path(args.target_root))
        plan = build_bootstrap_plan(inventory, manifest, Path(args.target_root))
        if args.apply:
            from routerkit_bootstrap_apply import (
                BootstrapApplyError,
                BootstrapTermination,
                apply_bootstrap_transaction,
                resolve_opkg,
                termination_exit_code,
                validate_apply_environment,
                validate_existing_target_metadata,
            )

            try:
                validate_apply_environment(Path(args.target_root), create=False)
                validate_existing_target_metadata(Path(args.target_root))
                resolve_opkg(Path(args.target_root))
            except BootstrapApplyError as exc:
                print("bootstrap: {}".format(exc), file=sys.stderr)
                return exc.exit_code
    except UnsupportedEnvironmentError as exc:
        print(f"bootstrap: {exc}", file=sys.stderr)
        return 1
    except (ManifestValidationError, InventoryValidationError) as exc:
        print(f"bootstrap: {exc}", file=sys.stderr)
        return 1
    except BootstrapConfigurationError as exc:
        print(f"bootstrap: {exc}", file=sys.stderr)
        return 2

    plan["dry_run_requested"] = bool(args.dry_run)
    if args.apply and args.dry_run:
        if args.json:
            preview = {
                "mode": "apply-preview",
                "environment": plan["environment"],
                "artifact_release": plan["pinned_artifact"]["release"],
                "archive_sha256": plan["pinned_artifact"]["sha256"],
                "required_packages": list(LATER_PACKAGES),
                "side_effects_performed": False,
            }
            print(json.dumps(preview, indent=2, sort_keys=True, ensure_ascii=False))
        else:
            print(render_apply_preview(plan), end="")
        return 0
    if args.apply:
        summary = render_transaction_summary(plan)
        print(summary, file=sys.stderr if args.json else sys.stdout)
        if not args.yes:
            try:
                if args.json:
                    print("Proceed with bootstrap apply? [y/N]: ", end="", file=sys.stderr)
                    response = sys.stdin.readline()
                else:
                    response = input("Proceed with bootstrap apply? [y/N]: ")
            except (EOFError, KeyboardInterrupt):
                print("bootstrap: apply cancelled; no package or artifact action was started.", file=sys.stderr)
                return 1
            if response.strip().lower() not in ("y", "yes"):
                print("bootstrap: apply declined; no package or artifact action was started.", file=sys.stderr)
                return 1
        try:
            transaction = apply_bootstrap_transaction(
                manifest, target_root=Path(args.target_root)
            )
        except BootstrapTermination as exc:
            print(
                "bootstrap: terminated after bounded child shutdown and staging cleanup.",
                file=sys.stderr,
            )
            return termination_exit_code(exc)
        except BootstrapApplyError as exc:
            print("bootstrap: {}".format(exc), file=sys.stderr)
            return exc.exit_code
        result = transaction.as_dict()
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
        else:
            print(render_apply_result(result), end="")
        return 0
    if args.json:
        print(json.dumps(plan, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(render_text_plan(plan), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
