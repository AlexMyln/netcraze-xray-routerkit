#!/usr/bin/env python3
"""
Preview what the guided installer would do without changing router state.

This script reads local generated Xray config fragments, validates the parts
that affect install safety, and renders a secret-safe install plan. It never
copies files, calls runtime commands, edits firewall rules, or changes Web UI
policies.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


GENERATED_FILES = [
    "03_inbounds.json",
    "04_outbounds.json",
    "05_routing.json",
]

LOOPBACK_LISTENERS = {"127.0.0.1", "::1"}

SECRET_FIELD_NAMES = {
    "id",
    "uuid",
    "users",
    "publickey",
    "shortid",
    "spiderx",
    "pbk",
    "sid",
    "spx",
    "realitysettings",
    "servername",
    "address",
    "subscription_url",
    "subscriptionurl",
}

WILL_NOT = [
    "call xkeen -start",
    "touch firewall",
    "change Netcraze Web UI policies",
    "enable autostart unless explicitly confirmed",
    "publish/store secrets",
]


def load_json_file(path: Path) -> Any:
    """Load a JSON file without logging or printing its contents."""
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _string_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def summarize_inbounds(data: Any) -> List[Dict[str, Any]]:
    inbounds = []
    if isinstance(data, dict):
        inbounds = _as_list(data.get("inbounds"))

    summary = []
    for inbound in inbounds:
        if not isinstance(inbound, dict):
            continue
        summary.append(
            {
                "tag": _string_or_none(inbound.get("tag")),
                "listen": _string_or_none(inbound.get("listen")),
                "port": inbound.get("port"),
                "protocol": _string_or_none(inbound.get("protocol")),
            }
        )
    return summary


def _has_secret_bearing_field(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in SECRET_FIELD_NAMES:
                return True
            if _has_secret_bearing_field(child):
                return True
    elif isinstance(value, list):
        return any(_has_secret_bearing_field(child) for child in value)
    elif isinstance(value, str):
        scheme = "vl" + "ess" + "://"
        if scheme in value.lower():
            return True
    return False


def summarize_outbounds(data: Any) -> List[Dict[str, Any]]:
    outbounds = []
    if isinstance(data, dict):
        outbounds = _as_list(data.get("outbounds"))

    summary = []
    for outbound in outbounds:
        if not isinstance(outbound, dict):
            continue
        summary.append(
            {
                "tag": _string_or_none(outbound.get("tag")),
                "protocol": _string_or_none(outbound.get("protocol")),
                "secret_fields_suppressed": _has_secret_bearing_field(outbound),
            }
        )
    return summary


def summarize_routing(data: Any) -> List[Dict[str, Optional[str]]]:
    routing = data.get("routing", {}) if isinstance(data, dict) else {}
    rules = _as_list(routing.get("rules")) if isinstance(routing, dict) else []

    summary = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        outbound = _string_or_none(rule.get("outboundTag"))
        for inbound in _as_list(rule.get("inboundTag")):
            summary.append(
                {
                    "inboundTag": _string_or_none(inbound),
                    "outboundTag": outbound,
                }
            )
    return summary


def validate_loopback_inbounds(inbounds: Iterable[Dict[str, Any]]) -> Dict[str, List[str]]:
    errors = []
    warnings = []

    for index, inbound in enumerate(inbounds, 1):
        tag = inbound.get("tag") or f"inbound #{index}"
        listen = inbound.get("listen")
        if listen is None or listen == "":
            warnings.append(f"{tag}: missing listen; expected 127.0.0.1 or ::1")
            continue
        if str(listen) not in LOOPBACK_LISTENERS:
            errors.append(f"{tag}: listen {listen} is not loopback")

    return {"errors": errors, "warnings": warnings}


def _display_path(path: Path) -> str:
    return str(path)


def _target_path(target_root: Path, *parts: str) -> str:
    return str(target_root.joinpath(*parts))


def build_plan(generated_dir: Path, target_root: Path, strict: bool = False) -> Dict[str, Any]:
    generated_dir = Path(generated_dir)
    target_root = Path(target_root)

    plan: Dict[str, Any] = {
        "title": "RouterKit install plan",
        "generated_dir": _display_path(generated_dir),
        "target_root": _display_path(target_root),
        "files": [],
        "inbounds": [],
        "outbounds": [],
        "routing": [],
        "install": [],
        "disabled": [_target_path(target_root, "etc", "init.d", "S24xray")],
        "will_not": list(WILL_NOT),
        "warnings": [],
        "errors": [],
        "config_error": False,
        "secret_fields_suppressed": False,
    }

    if generated_dir.exists() and not generated_dir.is_dir():
        plan["config_error"] = True
        plan["errors"].append(f"Generated path is not a directory: {generated_dir}")
        return plan

    parsed: Dict[str, Any] = {}
    for filename in GENERATED_FILES:
        path = generated_dir / filename
        file_info = {
            "path": _display_path(path),
            "exists": path.exists(),
            "valid_json": False,
        }
        if not path.exists():
            message = f"Missing generated file: {path}"
            if strict:
                plan["errors"].append(message)
            else:
                plan["warnings"].append(message)
            plan["files"].append(file_info)
            continue

        try:
            parsed[filename] = load_json_file(path)
        except json.JSONDecodeError as exc:
            plan["errors"].append(f"Invalid JSON in {path}: line {exc.lineno} column {exc.colno}")
        except OSError as exc:
            plan["errors"].append(f"Could not read {path}: {exc}")
        else:
            file_info["valid_json"] = True
        plan["files"].append(file_info)

    if "03_inbounds.json" in parsed:
        plan["inbounds"] = summarize_inbounds(parsed["03_inbounds.json"])
        validation = validate_loopback_inbounds(plan["inbounds"])
        plan["errors"].extend(validation["errors"])
        missing_listen = validation["warnings"]
        if strict:
            plan["errors"].extend(missing_listen)
        else:
            plan["warnings"].extend(missing_listen)

    if "04_outbounds.json" in parsed:
        plan["outbounds"] = summarize_outbounds(parsed["04_outbounds.json"])
        plan["secret_fields_suppressed"] = any(
            outbound.get("secret_fields_suppressed") for outbound in plan["outbounds"]
        )

    if "05_routing.json" in parsed:
        plan["routing"] = summarize_routing(parsed["05_routing.json"])

    for filename in GENERATED_FILES:
        plan["install"].append(
            {
                "source": _display_path(generated_dir / filename),
                "target": _target_path(target_root, "etc", "xray", "configs", filename),
            }
        )
    plan["install"].append(
        {
            "source": "templates/S23xray-direct",
            "target": _target_path(target_root, "etc", "init.d", "S23xray-direct"),
        }
    )

    return plan


def _format_value(value: Any) -> str:
    if value is None or value == "":
        return "<missing>"
    return str(value)


def _render_section(title: str, lines: Iterable[str]) -> List[str]:
    rendered = [title]
    rendered.extend(f"- {line}" for line in lines)
    return rendered


def render_text_plan(plan: Dict[str, Any]) -> str:
    lines = [plan.get("title", "RouterKit install plan"), ""]

    lines.extend(
        _render_section(
            "Would read:",
            (file_info["path"] for file_info in plan.get("files", [])),
        )
    )

    lines.append("")
    lines.append("Profile summary:")
    if plan.get("inbounds"):
        lines.extend(
            _render_section(
                "Inbounds:",
                (
                    "tag={tag} listen={listen} port={port} protocol={protocol}".format(
                        tag=_format_value(inbound.get("tag")),
                        listen=_format_value(inbound.get("listen")),
                        port=_format_value(inbound.get("port")),
                        protocol=_format_value(inbound.get("protocol")),
                    )
                    for inbound in plan["inbounds"]
                ),
            )
        )
    else:
        lines.extend(_render_section("Inbounds:", ["<none>"]))

    if plan.get("outbounds"):
        lines.extend(
            _render_section(
                "Outbounds:",
                (
                    "tag={tag} protocol={protocol}".format(
                        tag=_format_value(outbound.get("tag")),
                        protocol=_format_value(outbound.get("protocol")),
                    )
                    for outbound in plan["outbounds"]
                ),
            )
        )
    else:
        lines.extend(_render_section("Outbounds:", ["<none>"]))

    if plan.get("secret_fields_suppressed"):
        lines.append("- secret-bearing outbound fields detected and suppressed")

    if plan.get("routing"):
        lines.extend(
            _render_section(
                "Routing:",
                (
                    f"{_format_value(item.get('inboundTag'))} -> {_format_value(item.get('outboundTag'))}"
                    for item in plan["routing"]
                ),
            )
        )
    else:
        lines.extend(_render_section("Routing:", ["<none>"]))

    lines.append("")
    lines.extend(
        _render_section(
            "Would install:",
            (f"{item['source']} -> {item['target']}" for item in plan.get("install", [])),
        )
    )

    lines.append("")
    lines.extend(_render_section("Would keep disabled:", plan.get("disabled", [])))

    lines.append("")
    lines.extend(_render_section("Would NOT:", plan.get("will_not", [])))

    if plan.get("warnings"):
        lines.append("")
        lines.extend(_render_section("Warnings:", plan["warnings"]))

    if plan.get("errors"):
        lines.append("")
        lines.extend(_render_section("Critical validation failures:", plan["errors"]))

    return "\n".join(lines) + "\n"


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview routerkit install operations without changing /opt.")
    parser.add_argument("--generated", default="generated", help="Generated config directory")
    parser.add_argument("--target-root", default="/opt", help="Install target root")
    parser.add_argument("--json", action="store_true", help="Render machine-readable JSON output")
    parser.add_argument("--strict", action="store_true", help="Treat missing generated files as critical")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    plan = build_plan(Path(args.generated), Path(args.target_root), strict=args.strict)

    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print(render_text_plan(plan), end="")

    if plan.get("config_error"):
        return 2
    return 1 if plan.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
