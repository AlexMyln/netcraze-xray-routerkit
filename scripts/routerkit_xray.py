"""Shared deterministic RouterKit VLESS outbound generation."""
from __future__ import annotations

from typing import Any, Dict
from routerkit_profile_source import NodeRecord


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
