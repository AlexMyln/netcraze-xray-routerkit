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
import base64
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


VLESS_SCHEME = "vless" + "://"
VLESS_RE = re.compile(re.escape(VLESS_SCHEME) + r"[^\s\"'<>]+")


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_url(url: str, user_agent: str = "netcraze-xray-routerkit/0.1") -> str:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = resp.read()
    return data.decode("utf-8", errors="replace")


def maybe_b64_decode(text: str) -> Optional[str]:
    compact = "".join(text.split())
    if not compact:
        return None
    padded = compact + "=" * (-len(compact) % 4)
    try:
        decoded = base64.b64decode(padded, validate=False)
        return decoded.decode("utf-8", errors="replace")
    except Exception:
        return None


def iter_strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from iter_strings(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_strings(item)


def extract_vless_links(text: str) -> List[str]:
    found: List[str] = []

    def add_from(s: str) -> None:
        for match in VLESS_RE.findall(s):
            # Strip common delimiters that may trail URLs in JSON/text.
            found.append(match.rstrip(",;"))

    add_from(text)

    try:
        parsed = json.loads(text)
        for s in iter_strings(parsed):
            add_from(s)
    except Exception:
        pass

    decoded = maybe_b64_decode(text)
    if decoded:
        add_from(decoded)
        try:
            parsed = json.loads(decoded)
            for s in iter_strings(parsed):
                add_from(s)
        except Exception:
            pass

    # Preserve order, dedupe.
    seen = set()
    result = []
    for link in found:
        if link not in seen:
            seen.add(link)
            result.append(link)
    return result


def qget(query: Dict[str, List[str]], *names: str, default: str = "") -> str:
    for name in names:
        values = query.get(name)
        if values:
            return values[0]
    return default


def parse_vless(link: str) -> Dict[str, Any]:
    u = urllib.parse.urlparse(link)
    if u.scheme != "vless":
        raise ValueError("not a VLESS URL")

    query = urllib.parse.parse_qs(u.query)
    name = urllib.parse.unquote(u.fragment or "")

    host = u.hostname
    port = u.port
    uuid = urllib.parse.unquote(u.username or "")

    if not host or not port or not uuid:
        raise ValueError("VLESS URL must include uuid, host, and port")

    network = qget(query, "type", "network", default="tcp") or "tcp"
    security = qget(query, "security", default="")
    flow = qget(query, "flow", default="")

    parsed = {
        "name": name,
        "uuid": uuid,
        "host": host,
        "port": port,
        "network": network,
        "security": security,
        "flow": flow,
        "sni": qget(query, "sni", "serverName", default=""),
        "fp": qget(query, "fp", "fingerprint", default="chrome"),
        "pbk": qget(query, "pbk", "publicKey", default=""),
        "sid": qget(query, "sid", "shortId", default=""),
        "spx": qget(query, "spx", "spiderX", default="/"),
    }
    return parsed


def masked(s: str, keep: int = 4) -> str:
    if not s:
        return ""
    return s[:keep] + "..."


def select_node(links: List[str], selector: Dict[str, Any]) -> Dict[str, Any]:
    parsed_nodes = []
    for link in links:
        try:
            node = parse_vless(link)
        except Exception as exc:
            eprint(f"Skipping non-parseable link: {exc}")
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
        raise SystemExit(f"No node name contains: {name_contains!r}")

    host_contains = selector.get("host_contains")
    if host_contains:
        for link, node in parsed_nodes:
            if host_contains.lower() in node["host"].lower():
                return node
        raise SystemExit(f"No node host contains: {host_contains!r}")

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


def build_vless_outbound(profile: Dict[str, Any], node: Dict[str, Any]) -> Dict[str, Any]:
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


def build_outbounds(profiles: List[Dict[str, Any]], nodes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
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

    nodes: Dict[str, Dict[str, Any]] = {}

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
            f"Selected {profile['name']}: "
            f"name={node['name']!r} host={node['host']} port={node['port']} "
            f"security={node['security']} network={node['network']} "
            f"flow={node.get('flow','')} uuid={masked(node['uuid'])} "
            f"pbk:{masked(node.get('pbk',''), 6)} sid:{masked(node.get('sid',''), 2)}"
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
