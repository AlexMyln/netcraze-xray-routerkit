#!/usr/bin/env python3
"""
Interactive local profiles.json helper.

This script only writes a local profiles file and can optionally run the local
config generator. It does not connect to routers or install anything under /opt.
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Set


NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEFAULT_PORTS = [1082, 1083, 1084]


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    default_text = "Y/n" if default else "y/N"
    while True:
        value = input(f"{prompt} [{default_text}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please answer y or n.")


def ask_choice(prompt: str, choices: List[str], default_index: int = 0) -> str:
    while True:
        print(prompt)
        for index, choice in enumerate(choices, 1):
            marker = " default" if index - 1 == default_index else ""
            print(f"  {index}. {choice}{marker}")
        raw = input("Choose number: ").strip()
        if not raw:
            return choices[default_index]
        try:
            selected = int(raw)
        except ValueError:
            print("Please enter a number.")
            continue
        if 1 <= selected <= len(choices):
            return choices[selected - 1]
        print("Choice out of range.")


def ask_profile_name(existing: Set[str]) -> str:
    while True:
        name = ask("Profile name")
        if not NAME_RE.match(name):
            print("Use lowercase letters, digits, hyphen, or underscore. Start with a letter or digit.")
            continue
        if name in existing:
            print("Profile name is already used.")
            continue
        return name


def ask_port(default: int, used_ports: Set[int]) -> int:
    while True:
        value = ask("Local SOCKS port", str(default))
        try:
            port = int(value)
        except ValueError:
            print("Port must be an integer.")
            continue
        if port < 1 or port > 65535:
            print("Port must be between 1 and 65535.")
            continue
        if port in used_ports:
            print("Port is already used by another profile in this file.")
            continue
        return port


def ask_source() -> Dict[str, str]:
    source = ask_choice(
        "Subscription source",
        ["hidden URL", "environment variable name", "local file path"],
    )

    if source == "hidden URL":
        while True:
            url = getpass.getpass("Subscription URL (hidden, not echoed): ").strip()
            if url:
                return {"subscription_url": url}
            print("Subscription URL cannot be empty.")

    if source == "environment variable name":
        while True:
            env_name = ask("Environment variable name")
            if ENV_RE.match(env_name):
                return {"subscription_url_env": env_name}
            print("Use a valid environment variable name.")

    while True:
        file_path = ask("Local subscription file path")
        if file_path:
            return {"subscription_file": file_path}
        print("File path cannot be empty.")


def ask_selector() -> Dict[str, Any]:
    strategy = ask_choice(
        "Selection strategy",
        ["first matching node", "name contains", "host contains", "index"],
    )

    selector: Dict[str, Any] = {
        "require_security": "reality",
        "require_network": "tcp",
    }

    if strategy == "first matching node":
        selector["index"] = 0
    elif strategy == "name contains":
        while True:
            value = ask("Node name contains")
            if value:
                selector["name_contains"] = value
                break
            print("Value cannot be empty.")
    elif strategy == "host contains":
        while True:
            value = ask("Node host contains")
            if value:
                selector["host_contains"] = value
                break
            print("Value cannot be empty.")
    else:
        while True:
            value = ask("Node index", "0")
            try:
                index = int(value)
            except ValueError:
                print("Index must be an integer.")
                continue
            if index < 0:
                print("Index must be zero or greater.")
                continue
            selector["index"] = index
            break

    return selector


def source_type(profile: Dict[str, Any]) -> str:
    if "subscription_url" in profile:
        return "hidden URL"
    if "subscription_url_env" in profile:
        return "environment variable"
    if "subscription_file" in profile:
        return "local file"
    return "unknown"


def mask_value(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "***"
    return f"{value[:2]}...{value[-2:]}"


def selector_summary(selector: Dict[str, Any]) -> str:
    if "name_contains" in selector:
        return f"name contains {mask_value(str(selector['name_contains']))}"
    if "host_contains" in selector:
        return f"host contains {mask_value(str(selector['host_contains']))}"
    return f"index {selector.get('index', 0)}"


def print_summary(profiles: List[Dict[str, Any]]) -> None:
    print()
    print("Profile summary:")
    for profile in profiles:
        print(
            "- "
            f"name={profile['name']} "
            f"port={profile['port']} "
            f"source={source_type(profile)} "
            f"selector={selector_summary(profile.get('select', {}))}"
        )


def write_profiles(path: Path, profiles: List[Dict[str, Any]]) -> None:
    data = {"profiles": profiles}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError as exc:
        print(f"Warning: could not set private permissions on {path}: {exc}")


def run_generator(profiles_path: Path) -> int:
    cmd = [
        "python3",
        "scripts/generate-xray-profiles.py",
        "--profiles",
        str(profiles_path),
        "--out",
        "generated",
    ]
    print("Running local generator with secret output suppressed...")
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    except OSError as exc:
        print(f"Could not run generator: {exc}")
        return 1
    if result.returncode == 0:
        print("Generation completed. Output directory: generated")
        print("generated/ may contain secrets. Do not commit it.")
    else:
        print(f"Generation failed with exit code {result.returncode}.")
        print("Generator output was suppressed to avoid printing subscription details.")
    return result.returncode


def build_profiles() -> List[Dict[str, Any]]:
    profiles: List[Dict[str, Any]] = []
    names: Set[str] = set()
    ports: Set[int] = set()

    while True:
        if len(profiles) < len(DEFAULT_PORTS):
            default_port = DEFAULT_PORTS[len(profiles)]
        else:
            default_port = DEFAULT_PORTS[-1] + len(profiles) - 2
        name = ask_profile_name(names)
        port = ask_port(default_port, ports)
        source = ask_source()
        selector = ask_selector()

        profile: Dict[str, Any] = {
            "name": name,
            "port": port,
            **source,
            "select": selector,
        }
        profiles.append(profile)
        names.add(name)
        ports.add(port)

        if not ask_yes_no("Add another profile?", default=False):
            return profiles


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a local ignored profiles.json interactively.")
    parser.add_argument("--profiles", default="profiles.json", help="Output profiles file; default: profiles.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    profiles_path = Path(args.profiles)

    print("netcraze-xray-routerkit local profiles wizard")
    print("This helper only writes a local profiles file. It does not connect to a router or modify /opt.")
    print()

    if profiles_path.exists() and not ask_yes_no(f"{profiles_path} exists. Overwrite?", default=False):
        print("Cancelled.")
        return 1

    profiles = build_profiles()
    print_summary(profiles)

    if not ask_yes_no(f"Write {profiles_path}?", default=True):
        print("Cancelled before writing.")
        return 1

    write_profiles(profiles_path, profiles)
    print(f"Wrote {profiles_path}.")
    print("profiles.json may contain secrets. Keep it private and out of git.")

    print()
    print("Next local step:")
    print(f"  python3 scripts/generate-xray-profiles.py --profiles {profiles_path} --out generated")

    if ask_yes_no("Run the local generator now?", default=False):
        return run_generator(profiles_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
