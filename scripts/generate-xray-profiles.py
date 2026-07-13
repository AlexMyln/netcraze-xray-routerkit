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
import urllib.request
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


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_url(url: str, user_agent: str = "netcraze-xray-routerkit/0.1") -> str:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = resp.read()
    return data.decode("utf-8", errors="replace")


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", required=True, help="Path to profiles JSON")
    parser.add_argument("--out", required=True, help="Output directory")
    args = parser.parse_args()

    cfg = load_json(Path(args.profiles))
    profiles = cfg.get("profiles", [])
    validate_profile_names(profiles)

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

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    files = {
        "03_inbounds.json": build_inbounds(profiles),
        "04_outbounds.json": build_outbounds(profiles, nodes),
        "05_routing.json": build_routing(profiles),
    }

    for filename, data in files.items():
        (out_dir / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(out_dir / filename, 0o600)

    eprint(f"Wrote Xray configs to: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
