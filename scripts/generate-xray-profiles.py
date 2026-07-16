#!/usr/bin/env python3
"""
Generate Xray config files for multiple local SOCKS profiles.

Input: JSON file with profile definitions.
Output:
  03_inbounds.json
  04_outbounds.json
  05_routing.json

No external Python dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from routerkit_profile_source import (  # noqa: E402
    NodeRecord,
    NodeValidationError,
    extract_vless_links,
    parse_vless,
)
from routerkit_profile_network import (  # noqa: E402
    ProfileNetworkError,
    normalize_https_source_value,
    resolve_https_source,
)
from routerkit_private_io import (  # noqa: E402
    PrivateFileError,
    ensure_private_directory,
    remove_private_file_if_valid,
    write_private_bytes_atomic,
)


LOCAL_ENDPOINT_MANIFEST = "routerkit-local-endpoints.json"
LOCAL_ENDPOINT_SCHEMA = "routerkit.local-endpoints.v1"
SUPPORTED_LOCAL_PORTS = (1082, 1083, 1084)
MAX_LOCAL_ENDPOINT_MANIFEST_BYTES = 32 * 1024
SAFE_MANIFEST_LABELS = {
    1: "primary",
    2: "fallback-1",
    3: "fallback-2",
}


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_url(url: str, user_agent: str = "netcraze-xray-routerkit/0.1") -> str:
    del user_agent  # Retained for compatibility with callers of this helper.
    try:
        return resolve_https_source(normalize_https_source_value(url)).payload
    except ProfileNetworkError:
        raise SystemExit("HTTPS subscription could not be resolved securely.") from None


def select_node(links: List[str], selector: Dict[str, Any]) -> NodeRecord:
    parsed_nodes = []
    for link in links:
        try:
            node = parse_vless(link)
        except NodeValidationError:
            continue

        require_security = selector.get("require_security")
        require_network = selector.get("require_network")

        if require_security and node["security"] != require_security:
            continue
        if require_network and node["network"] not in (require_network, "", None):
            continue

        parsed_nodes.append((link, node))

    if not parsed_nodes:
        raise SystemExit("No matching VLESS nodes found for selector")

    name_contains = selector.get("name_contains")
    if name_contains:
        for link, node in parsed_nodes:
            if name_contains.lower() in node["name"].lower():
                return node
        raise SystemExit("No node name matches the requested selector")

    host_contains = selector.get("host_contains")
    if host_contains:
        for link, node in parsed_nodes:
            if host_contains.lower() in node["host"].lower():
                return node
        raise SystemExit("No node host matches the requested selector")

    index = int(selector.get("index", 0))
    if index < 0 or index >= len(parsed_nodes):
        raise SystemExit(f"Selector index {index} out of range; found {len(parsed_nodes)} matching nodes")

    return parsed_nodes[index][1]


def build_inbounds(profiles: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "inbounds": [
            {
                "tag": f"socks-{p['name']}",
                "listen": "127.0.0.1",
                "port": int(p["port"]),
                "protocol": "socks",
                "settings": {
                    "auth": "noauth",
                    "udp": True
                },
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls", "quic"]
                }
            }
            for p in profiles
        ]
    }


def build_vless_outbound(profile: Dict[str, Any], node: NodeRecord) -> Dict[str, Any]:
    user = {
        "id": node["uuid"],
        "encryption": "none",
    }
    if node.get("flow"):
        user["flow"] = node["flow"]

    reality = {
        "serverName": node.get("sni") or node["host"],
        "fingerprint": node.get("fp") or "chrome",
        "publicKey": node["pbk"],
        "shortId": node.get("sid", ""),
        "spiderX": node.get("spx") or "/",
    }

    return {
        "tag": f"vless-{profile['name']}",
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": node["host"],
                    "port": int(node["port"]),
                    "users": [user],
                }
            ]
        },
        "streamSettings": {
            "network": node.get("network") or "tcp",
            "security": node.get("security") or "reality",
            "realitySettings": reality,
        },
    }


def build_outbounds(profiles: List[Dict[str, Any]], nodes: Dict[str, NodeRecord]) -> Dict[str, Any]:
    outbounds = [build_vless_outbound(p, nodes[p["name"]]) for p in profiles]
    outbounds.extend([
        {"tag": "direct", "protocol": "freedom"},
        {"tag": "block", "protocol": "blackhole"},
    ])
    return {"outbounds": outbounds}


def build_routing(profiles: List[Dict[str, Any]]) -> Dict[str, Any]:
    rules = [
        {
            "type": "field",
            "inboundTag": [f"socks-{p['name']}"],
            "outboundTag": f"vless-{p['name']}",
        }
        for p in profiles
    ]
    return {
        "routing": {
            "domainStrategy": "AsIs",
            "rules": rules,
        }
    }


def get_subscription_text(profile: Dict[str, Any]) -> str:
    if "subscription_url" in profile:
        return fetch_url(profile["subscription_url"])

    env_name = profile.get("subscription_url_env")
    if env_name:
        url = os.environ.get(env_name)
        if not url:
            raise SystemExit(f"Environment variable {env_name} is not set")
        return fetch_url(url)

    file_path = profile.get("subscription_file")
    if file_path:
        return Path(file_path).read_text(encoding="utf-8")

    vless_link = profile.get("vless")
    if vless_link:
        return vless_link

    raise SystemExit(f"Profile {profile.get('name')} has no subscription source")


def validate_profile_names(profiles: List[Dict[str, Any]]) -> None:
    seen = set()
    for p in profiles:
        name = p.get("name")
        if not name or not re.match(r"^[a-z0-9][a-z0-9_-]*$", name):
            raise SystemExit(f"Invalid profile name: {name!r}. Use lowercase letters, digits, _ or -")
        if name in seen:
            raise SystemExit(f"Duplicate profile name: {name}")
        seen.add(name)


def build_local_endpoint_manifest(profiles: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not 1 <= len(profiles) <= 3:
        raise SystemExit("One to three local profiles are required")
    seen_ports = set()
    endpoints = []
    for slot, profile in enumerate(profiles, start=1):
        port = profile.get("port")
        if not isinstance(port, int) or isinstance(port, bool) or port not in SUPPORTED_LOCAL_PORTS:
            raise SystemExit("Profile local SOCKS port is outside the supported scope")
        if port in seen_ports:
            raise SystemExit("Duplicate profile local SOCKS port")
        seen_ports.add(port)
        endpoints.append(
            {
                "slot": slot,
                "label": SAFE_MANIFEST_LABELS[slot],
                "listen": "127.0.0.1",
                "port": port,
                "enabled": True,
                "protocol": "socks5",
            }
        )
    return {"schema": LOCAL_ENDPOINT_SCHEMA, "profiles": endpoints}


def validate_local_endpoint_manifest_text(text: str) -> None:
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        raise PrivateFileError("Local endpoint manifest is not recognized.") from None
    if not isinstance(value, dict) or set(value) != {"schema", "profiles"}:
        raise PrivateFileError("Local endpoint manifest is not recognized.")
    if value.get("schema") != LOCAL_ENDPOINT_SCHEMA:
        raise PrivateFileError("Local endpoint manifest is not recognized.")
    profiles = value.get("profiles")
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= 3:
        raise PrivateFileError("Local endpoint manifest is not recognized.")
    seen_ports = set()
    for expected_slot, profile in enumerate(profiles, start=1):
        if not isinstance(profile, dict) or set(profile) != {
            "slot",
            "label",
            "listen",
            "port",
            "enabled",
            "protocol",
        }:
            raise PrivateFileError("Local endpoint manifest is not recognized.")
        port = profile.get("port")
        if (
            profile.get("slot") != expected_slot
            or profile.get("label") != SAFE_MANIFEST_LABELS[expected_slot]
            or profile.get("listen") not in ("127.0.0.1", "::1")
            or not isinstance(port, int)
            or isinstance(port, bool)
            or port not in SUPPORTED_LOCAL_PORTS
            or port in seen_ports
            or not isinstance(profile.get("enabled"), bool)
            or profile.get("protocol") != "socks5"
        ):
            raise PrivateFileError("Local endpoint manifest is not recognized.")
        seen_ports.add(port)


def write_private_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    encoded = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        validate_local_endpoint_manifest_text(encoded.decode("utf-8"))
        write_private_bytes_atomic(
            Path(path),
            encoded,
            maximum_bytes=MAX_LOCAL_ENDPOINT_MANIFEST_BYTES,
            description="Local endpoint manifest",
            validate_existing_text=validate_local_endpoint_manifest_text,
        )
    except PrivateFileError as exc:
        raise SystemExit(str(exc)) from None


def retire_stale_local_endpoint_manifest(path: Path) -> None:
    try:
        remove_private_file_if_valid(
            Path(path),
            maximum_bytes=MAX_LOCAL_ENDPOINT_MANIFEST_BYTES,
            description="Local endpoint manifest",
            validate_text=validate_local_endpoint_manifest_text,
        )
    except PrivateFileError as exc:
        raise SystemExit(str(exc)) from None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", required=True, help="Path to profiles JSON")
    parser.add_argument("--out", required=True, help="Output directory")
    args = parser.parse_args()

    cfg = load_json(Path(args.profiles))
    profiles = cfg.get("profiles", [])
    validate_profile_names(profiles)

    out_dir = Path(args.out)
    try:
        ensure_private_directory(out_dir, description="Generated output directory")
    except PrivateFileError as exc:
        raise SystemExit(str(exc)) from None
    manifest_path = out_dir / LOCAL_ENDPOINT_MANIFEST
    retire_stale_local_endpoint_manifest(manifest_path)

    nodes: Dict[str, NodeRecord] = {}

    for profile in profiles:
        text = get_subscription_text(profile)
        links = extract_vless_links(text)
        if not links:
            raise SystemExit(f"No VLESS links found for profile {profile['name']}")

        node = select_node(links, profile.get("select", {}))
        if node.get("security") != "reality" or node.get("network") not in ("tcp", "", None):
            raise SystemExit(
                f"Profile {profile['name']} selected unsupported node: "
                f"security={node.get('security')} network={node.get('network')}"
            )
        nodes[profile["name"]] = node
        eprint(
            f"Selected profile {profile['name']}: port={node['port']} "
            f"security={node['security']} network={node['network']} "
            f"flow={node.get('flow') or 'none'}"
        )

    files = {
        "03_inbounds.json": build_inbounds(profiles),
        "04_outbounds.json": build_outbounds(profiles, nodes),
        "05_routing.json": build_routing(profiles),
    }

    for filename, data in files.items():
        (out_dir / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(out_dir / filename, 0o600)

    write_private_json_atomic(
        manifest_path,
        build_local_endpoint_manifest(profiles),
    )

    eprint(f"Wrote Xray configs to: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
