#!/usr/bin/env python3
"""Narrow live NDM CLI adapter for RouterKit per-device proxy policies.

The first supported contract is the hardware-observed Netcraze Hopper SE
NC-3812 / NetcrazeOS 5.1.5 path from 2026-09-09.  Planning remains owned by
RouterKit's existing manifest/planner semantics; this module only bridges the
validated local SOCKS endpoints to native Proxy/Policy objects and an optional
explicit MAC assignment.

This adapter deliberately does not modify the router's Default policy, does
not create firewall rules, and never invokes xkeen/TPROXY/REDIRECT.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from routerkit_devices import DeviceDiscoveryError, normalize_trusted_device_mac
from routerkit_netcraze_plan import (
    LocalEndpointManifest,
    LocalProxyProfile,
    connection_name,
    load_local_endpoint_manifest,
    policy_name,
)


LIVE_SCHEMA = "routerkit.netcraze.ndm-live.v1"
SUPPORTED_CONTRACT = "nc3812-netcrazeos-5.1.5"
MAX_NDM_OUTPUT_BYTES = 1024 * 1024
NDM_TIMEOUT_SECONDS = 15.0
MAX_PROXY_INDEX = 15
MAX_POLICY_INDEX = 15
BACKUP_ROOT = Path("/opt/var/lib/routerkit/netcraze-backups")
ROLLBACK_UNPROVEN = 3

_PROXY_ID_RE = re.compile(r"^Proxy([0-9]+)$")
_POLICY_ID_RE = re.compile(r"^Policy([0-9]+)$")
_INTERFACE_RE = re.compile(r"^interface\s+(Proxy[0-9]+)$")
_POLICY_RE = re.compile(r"^ip\s+policy\s+(Policy[0-9]+)$")
_DESCRIPTION_RE = re.compile(r'^description\s+"?(.*?)"?$')
_PROTOCOL_RE = re.compile(r"^proxy\s+protocol\s+(\S+)$")
_UPSTREAM_RE = re.compile(r"^proxy\s+upstream\s+(\S+)(?:\s+([0-9]+))?$")
_PERMIT_GLOBAL_RE = re.compile(r"^permit\s+global\s+(\S+)(?:\s+([0-9]+))?$")
_ASSIGNMENT_RE = re.compile(
    r"^(?:ip\s+hotspot\s+)?host\s+([0-9A-Fa-f:]{17})\s+policy\s+(Policy[0-9]+)$"
)
_AUTH_RE = re.compile(r"^authentication\s+(?:identity|password)\b")


class LiveAdapterError(Exception):
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class ProxyState:
    object_id: str
    description: str
    protocol: str
    host: str
    port: int
    enabled: bool
    authentication_configured: bool


@dataclass(frozen=True)
class PolicyState:
    object_id: str
    description: str
    global_interfaces: Tuple[str, ...]


@dataclass(frozen=True)
class LiveState:
    proxies: Tuple[ProxyState, ...]
    policies: Tuple[PolicyState, ...]
    assignments: Tuple[Tuple[str, str], ...]
    default_guard: Tuple[str, ...]

    @property
    def assignment_map(self) -> Dict[str, str]:
        return dict(self.assignments)


@dataclass(frozen=True)
class BindingPlan:
    slot: int
    port: int
    proxy_id: str
    policy_id: str
    proxy_action: str
    policy_action: str


@dataclass(frozen=True)
class LivePlan:
    bindings: Tuple[BindingPlan, ...]
    selected_device_present: bool
    selected_slot: Optional[int]
    assignment_action: str
    default_policy_targeted: bool = False
    schema: str = LIVE_SCHEMA

    def to_public_dict(self) -> Dict[str, object]:
        return {
            "schema": self.schema,
            "bindings": [asdict(item) for item in self.bindings],
            "selected_device_present": self.selected_device_present,
            "selected_slot": self.selected_slot,
            "assignment_action": self.assignment_action,
            "default_policy_targeted": self.default_policy_targeted,
        }


@dataclass
class ApplyResult:
    created_proxies: List[str]
    created_policies: List[str]
    assignment_changed: bool
    saved: bool
    verified: bool
    rollback_attempted: bool = False
    rollback_verified: bool = False

    def to_public_dict(self) -> Dict[str, object]:
        return {
            "schema": LIVE_SCHEMA,
            "created_proxy_count": len(self.created_proxies),
            "created_policy_count": len(self.created_policies),
            "assignment_changed": self.assignment_changed,
            "saved": self.saved,
            "verified": self.verified,
            "rollback_attempted": self.rollback_attempted,
            "rollback_verified": self.rollback_verified,
            "default_policy_targeted": False,
        }


class NdmcTransport:
    """Shell-free local NDM command transport through Entware ``ndmc``."""

    def __init__(self, path: Path) -> None:
        self.path = self._validate_path(Path(path))

    @staticmethod
    def _validate_path(path: Path) -> Path:
        try:
            resolved = path.resolve(strict=True)
            metadata = resolved.stat()
        except OSError:
            raise LiveAdapterError("A usable ndmc executable was not found.") from None
        if not stat.S_ISREG(metadata.st_mode) or not os.access(str(resolved), os.X_OK):
            raise LiveAdapterError("The selected ndmc path is not an executable regular file.")
        return resolved

    def command(self, command: str) -> str:
        if not isinstance(command, str) or not command or "\n" in command or "\r" in command:
            raise LiveAdapterError("An invalid NDM command was rejected.")
        try:
            completed = subprocess.run(
                [str(self.path), "-c", command],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=NDM_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise LiveAdapterError("NDM command execution failed.") from None
        if len(completed.stdout) + len(completed.stderr) > MAX_NDM_OUTPUT_BYTES:
            raise LiveAdapterError("NDM command output exceeded the bounded limit.")
        if completed.returncode != 0:
            raise LiveAdapterError("NDM command returned a failure status.")
        try:
            return completed.stdout.decode("utf-8", "strict")
        except UnicodeDecodeError:
            raise LiveAdapterError("NDM command output is not valid UTF-8.") from None


class MemoryTransport:
    """Test transport with deterministic command responses."""

    def __init__(self, responses: Optional[Mapping[str, Sequence[str]]] = None) -> None:
        self.responses = {key: list(value) for key, value in (responses or {}).items()}
        self.commands: List[str] = []

    def command(self, command: str) -> str:
        self.commands.append(command)
        values = self.responses.get(command)
        if values:
            return values.pop(0)
        return ""


def default_ndmc_path() -> Optional[Path]:
    for candidate in (Path("/opt/bin/ndmc"), Path("/opt/sbin/ndmc")):
        if candidate.exists():
            return candidate
    found = shutil.which("ndmc")
    return None if found is None else Path(found)


def _clean_description(value: str) -> str:
    return value.strip().strip('"')


def parse_running_config(text: str) -> LiveState:
    proxies: Dict[str, Dict[str, object]] = {}
    policies: Dict[str, Dict[str, object]] = {}
    assignments: Dict[str, str] = {}
    default_guard: List[str] = []
    current_kind: Optional[str] = None
    current_id: Optional[str] = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "!":
            current_kind = None
            current_id = None
            continue

        match = _INTERFACE_RE.fullmatch(line)
        if match:
            current_kind = "proxy"
            current_id = match.group(1)
            proxies.setdefault(
                current_id,
                {
                    "description": "",
                    "protocol": "",
                    "host": "",
                    "port": 0,
                    "enabled": False,
                    "authentication_configured": False,
                },
            )
            continue

        match = _POLICY_RE.fullmatch(line)
        if match:
            current_kind = "policy"
            current_id = match.group(1)
            policies.setdefault(current_id, {"description": "", "global_interfaces": []})
            continue

        assignment = _ASSIGNMENT_RE.fullmatch(line)
        if assignment:
            try:
                mac = normalize_trusted_device_mac(assignment.group(1))
            except DeviceDiscoveryError:
                raise LiveAdapterError("Running configuration contains an invalid host policy identity.") from None
            assignments[mac] = assignment.group(2)
            continue

        if "default-policy" in line:
            default_guard.append(line)

        if current_kind == "proxy" and current_id is not None:
            target = proxies[current_id]
            description = _DESCRIPTION_RE.fullmatch(line)
            if description:
                target["description"] = _clean_description(description.group(1))
                continue
            protocol = _PROTOCOL_RE.fullmatch(line)
            if protocol:
                target["protocol"] = protocol.group(1).casefold()
                continue
            upstream = _UPSTREAM_RE.fullmatch(line)
            if upstream:
                target["host"] = upstream.group(1)
                target["port"] = int(upstream.group(2) or "0")
                continue
            if line == "up":
                target["enabled"] = True
                continue
            if _AUTH_RE.match(line):
                target["authentication_configured"] = True
                continue

        if current_kind == "policy" and current_id is not None:
            target = policies[current_id]
            description = _DESCRIPTION_RE.fullmatch(line)
            if description:
                target["description"] = _clean_description(description.group(1))
                continue
            permitted = _PERMIT_GLOBAL_RE.fullmatch(line)
            if permitted:
                target["global_interfaces"].append(permitted.group(1))
                continue

    proxy_values = tuple(
        ProxyState(
            object_id=object_id,
            description=str(value["description"]),
            protocol=str(value["protocol"]),
            host=str(value["host"]),
            port=int(value["port"]),
            enabled=bool(value["enabled"]),
            authentication_configured=bool(value["authentication_configured"]),
        )
        for object_id, value in sorted(proxies.items())
    )
    policy_values = tuple(
        PolicyState(
            object_id=object_id,
            description=str(value["description"]),
            global_interfaces=tuple(str(item) for item in value["global_interfaces"]),
        )
        for object_id, value in sorted(policies.items())
    )
    return LiveState(
        proxies=proxy_values,
        policies=policy_values,
        assignments=tuple(sorted(assignments.items())),
        default_guard=tuple(sorted(default_guard)),
    )


def read_state(transport) -> Tuple[str, LiveState]:
    raw = transport.command("show running-config")
    return raw, parse_running_config(raw)


def _numeric_id(value: str, pattern: re.Pattern, maximum: int) -> int:
    match = pattern.fullmatch(value)
    if not match:
        raise LiveAdapterError("An unexpected native object identifier was observed.")
    number = int(match.group(1))
    if number > maximum:
        raise LiveAdapterError("A native object identifier is outside the supported range.")
    return number


def _next_free(existing: Iterable[str], prefix: str, maximum: int) -> str:
    pattern = _PROXY_ID_RE if prefix == "Proxy" else _POLICY_ID_RE
    used = {_numeric_id(item, pattern, maximum) for item in existing}
    for number in range(maximum + 1):
        if number not in used:
            return "%s%d" % (prefix, number)
    raise LiveAdapterError("No free native %s slots remain." % prefix)


def _proxy_exact(proxy: ProxyState, profile: LocalProxyProfile) -> bool:
    return (
        proxy.description == connection_name(profile)
        and proxy.protocol == "socks5"
        and proxy.host == profile.host
        and proxy.port == profile.port
        and proxy.enabled == profile.enabled
        and not proxy.authentication_configured
    )


def _policy_exact(policy: PolicyState, profile: LocalProxyProfile, proxy_id: str) -> bool:
    return (
        policy.description == policy_name(profile)
        and policy.global_interfaces == (proxy_id,)
    )


def _unique_by_description(items, description: str):
    matches = [item for item in items if item.description == description]
    if len(matches) > 1:
        raise LiveAdapterError("Duplicate RouterKit-owned native object descriptions were found.")
    return None if not matches else matches[0]


def build_live_plan(
    manifest: LocalEndpointManifest,
    state: LiveState,
    *,
    device_mac: Optional[str] = None,
    profile_slot: Optional[int] = None,
    allow_move: bool = False,
) -> LivePlan:
    if (device_mac is None) != (profile_slot is None):
        raise LiveAdapterError("Device assignment requires both a MAC and a profile slot.")
    normalized_mac = None
    if device_mac is not None:
        try:
            normalized_mac = normalize_trusted_device_mac(device_mac)
        except DeviceDiscoveryError:
            raise LiveAdapterError("Selected device MAC is invalid or unsafe.") from None
        if profile_slot not in [item.slot for item in manifest.profiles]:
            raise LiveAdapterError("Selected profile slot is not present in the endpoint manifest.")

    proxy_ids = [item.object_id for item in state.proxies]
    policy_ids = [item.object_id for item in state.policies]
    reserved_proxy_ids = set(proxy_ids)
    reserved_policy_ids = set(policy_ids)
    bindings: List[BindingPlan] = []

    for profile in manifest.profiles:
        desired_proxy_name = connection_name(profile)
        existing_proxy = _unique_by_description(state.proxies, desired_proxy_name)
        if existing_proxy is not None:
            if not _proxy_exact(existing_proxy, profile):
                raise LiveAdapterError("A RouterKit proxy name exists with conflicting semantics.")
            proxy_id = existing_proxy.object_id
            proxy_action = "reuse"
        else:
            proxy_id = _next_free(reserved_proxy_ids, "Proxy", MAX_PROXY_INDEX)
            reserved_proxy_ids.add(proxy_id)
            proxy_action = "create"

        desired_policy_name = policy_name(profile)
        existing_policy = _unique_by_description(state.policies, desired_policy_name)
        if existing_policy is not None:
            if not _policy_exact(existing_policy, profile, proxy_id):
                raise LiveAdapterError("A RouterKit policy name exists with conflicting semantics.")
            policy_id = existing_policy.object_id
            policy_action = "reuse"
        else:
            policy_id = _next_free(reserved_policy_ids, "Policy", MAX_POLICY_INDEX)
            reserved_policy_ids.add(policy_id)
            policy_action = "create"

        bindings.append(
            BindingPlan(
                slot=profile.slot,
                port=profile.port,
                proxy_id=proxy_id,
                policy_id=policy_id,
                proxy_action=proxy_action,
                policy_action=policy_action,
            )
        )

    assignment_action = "none"
    if normalized_mac is not None and profile_slot is not None:
        target_policy = next(item.policy_id for item in bindings if item.slot == profile_slot)
        current = state.assignment_map.get(normalized_mac)
        if current == target_policy:
            assignment_action = "reuse"
        elif current is None:
            assignment_action = "assign"
        elif allow_move:
            assignment_action = "move"
        else:
            raise LiveAdapterError("Selected device already has another policy; explicit move authorization is required.")

    return LivePlan(
        bindings=tuple(bindings),
        selected_device_present=normalized_mac is not None,
        selected_slot=profile_slot,
        assignment_action=assignment_action,
    )


def proxy_create_commands(binding: BindingPlan, profile: LocalProxyProfile) -> Tuple[str, ...]:
    return (
        "interface %s description %s" % (binding.proxy_id, connection_name(profile)),
        "interface %s security-level public" % binding.proxy_id,
        "interface %s proxy protocol socks5" % binding.proxy_id,
        "interface %s proxy upstream %s %d" % (binding.proxy_id, profile.host, profile.port),
        "interface %s up" % binding.proxy_id,
    )


def policy_create_commands(binding: BindingPlan, profile: LocalProxyProfile) -> Tuple[str, ...]:
    return (
        "ip policy %s description %s" % (binding.policy_id, policy_name(profile)),
        "ip policy %s permit global %s" % (binding.policy_id, binding.proxy_id),
    )


def _backup_running_config(raw: str, backup_root: Path = BACKUP_ROOT) -> Path:
    root = Path(backup_root)
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            os.chmod(root, 0o700)
        timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        path = root / ("running-config-pre-netcraze-live-%s.txt" % timestamp)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, raw.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        return path
    except OSError:
        raise LiveAdapterError("Private running-config backup could not be created.") from None


def _verify_plan_applied(
    manifest: LocalEndpointManifest,
    plan: LivePlan,
    state: LiveState,
    *,
    device_mac: Optional[str],
) -> None:
    proxies = {item.object_id: item for item in state.proxies}
    policies = {item.object_id: item for item in state.policies}
    profiles = {item.slot: item for item in manifest.profiles}
    for binding in plan.bindings:
        profile = profiles[binding.slot]
        proxy = proxies.get(binding.proxy_id)
        policy = policies.get(binding.policy_id)
        if proxy is None or not _proxy_exact(proxy, profile):
            raise LiveAdapterError("Post-apply proxy verification failed.")
        if policy is None or not _policy_exact(policy, profile, binding.proxy_id):
            raise LiveAdapterError("Post-apply policy verification failed.")
    if device_mac is not None and plan.selected_slot is not None:
        mac = normalize_trusted_device_mac(device_mac)
        target = next(item.policy_id for item in plan.bindings if item.slot == plan.selected_slot)
        if state.assignment_map.get(mac) != target:
            raise LiveAdapterError("Post-apply device assignment verification failed.")


def apply_live_plan(
    transport,
    manifest: LocalEndpointManifest,
    plan: LivePlan,
    *,
    device_mac: Optional[str] = None,
    backup_root: Path = BACKUP_ROOT,
) -> ApplyResult:
    raw_before, state_before = read_state(transport)
    if state_before.default_guard != state_before.default_guard:
        raise AssertionError("unreachable")
    _backup_running_config(raw_before, backup_root=backup_root)

    result = ApplyResult([], [], False, False, False)
    rollback: List[str] = []
    profiles = {item.slot: item for item in manifest.profiles}
    normalized_mac = None if device_mac is None else normalize_trusted_device_mac(device_mac)
    old_assignment = None if normalized_mac is None else state_before.assignment_map.get(normalized_mac)

    try:
        for binding in plan.bindings:
            profile = profiles[binding.slot]
            if binding.proxy_action == "create":
                commands = proxy_create_commands(binding, profile)
                transport.command(commands[0])
                rollback.append("no interface %s" % binding.proxy_id)
                for command in commands[1:]:
                    transport.command(command)
                result.created_proxies.append(binding.proxy_id)

        for binding in plan.bindings:
            profile = profiles[binding.slot]
            if binding.policy_action == "create":
                commands = policy_create_commands(binding, profile)
                transport.command(commands[0])
                rollback.append("no ip policy %s" % binding.policy_id)
                for command in commands[1:]:
                    transport.command(command)
                result.created_policies.append(binding.policy_id)

        if normalized_mac is not None and plan.selected_slot is not None and plan.assignment_action in ("assign", "move"):
            target_policy = next(item.policy_id for item in plan.bindings if item.slot == plan.selected_slot)
            transport.command("ip hotspot host %s policy %s" % (normalized_mac, target_policy))
            if old_assignment is None:
                rollback.append("no ip hotspot host %s policy" % normalized_mac)
            else:
                rollback.append("ip hotspot host %s policy %s" % (normalized_mac, old_assignment))
            result.assignment_changed = True

        transport.command("system configuration save")
        result.saved = True
        _raw_after, state_after = read_state(transport)
        if state_after.default_guard != state_before.default_guard:
            raise LiveAdapterError("Default-policy guard changed during RouterKit live apply.")
        _verify_plan_applied(manifest, plan, state_after, device_mac=normalized_mac)
        result.verified = True
        return result
    except (LiveAdapterError, DeviceDiscoveryError):
        result.rollback_attempted = True
        rollback_ok = True
        for command in reversed(rollback):
            try:
                transport.command(command)
            except LiveAdapterError:
                rollback_ok = False
                break
        if rollback_ok:
            try:
                transport.command("system configuration save")
                _raw_rolled, state_rolled = read_state(transport)
                rollback_ok = state_rolled.default_guard == state_before.default_guard
            except LiveAdapterError:
                rollback_ok = False
        result.rollback_verified = rollback_ok
        if not rollback_ok:
            raise LiveAdapterError(
                "Netcraze live apply failed and rollback could not be proven.",
                ROLLBACK_UNPROVEN,
            ) from None
        raise


def _summary_state(state: LiveState) -> Dict[str, object]:
    routerkit_proxies = [item for item in state.proxies if item.description.startswith("RouterKit-SOCKS-")]
    routerkit_policies = [item for item in state.policies if item.description.startswith("RouterKit-Policy-")]
    return {
        "schema": LIVE_SCHEMA,
        "proxy_count": len(state.proxies),
        "policy_count": len(state.policies),
        "assignment_count": len(state.assignments),
        "routerkit_proxy_count": len(routerkit_proxies),
        "routerkit_policy_count": len(routerkit_policies),
        "default_guard_entries": len(state.default_guard),
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply RouterKit native Proxy/Policy objects through local ndmc.")
    parser.add_argument("mode", choices=("status", "plan", "apply"))
    parser.add_argument("--manifest-file")
    parser.add_argument("--device-mac")
    parser.add_argument("--profile-slot", type=int)
    parser.add_argument("--move-device", action="store_true")
    parser.add_argument("--ndmc-path")
    parser.add_argument("--contract", default=SUPPORTED_CONTRACT)
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.mode in ("plan", "apply") and not args.manifest_file:
        parser.error("plan/apply require --manifest-file")
    if (args.device_mac is None) != (args.profile_slot is None):
        parser.error("--device-mac and --profile-slot must be supplied together")
    if args.mode != "apply" and args.yes:
        parser.error("--yes is valid only with apply")
    if args.mode == "apply" and args.contract != SUPPORTED_CONTRACT:
        parser.error("live apply currently supports only the NC-3812 / NetcrazeOS 5.1.5 contract")
    return args


def main(argv: Optional[Sequence[str]] = None, *, transport=None) -> int:
    args = parse_args(argv)
    try:
        live_transport = transport
        if live_transport is None:
            ndmc_path = Path(args.ndmc_path) if args.ndmc_path else default_ndmc_path()
            if ndmc_path is None:
                raise LiveAdapterError("ndmc is unavailable; use a supported Entware/NDM transport.")
            live_transport = NdmcTransport(ndmc_path)

        _raw, state = read_state(live_transport)
        if args.mode == "status":
            payload = _summary_state(state)
            print(json.dumps(payload, sort_keys=True) if args.json else payload)
            return 0

        manifest = load_local_endpoint_manifest(Path(args.manifest_file))
        plan = build_live_plan(
            manifest,
            state,
            device_mac=args.device_mac,
            profile_slot=args.profile_slot,
            allow_move=args.move_device,
        )
        if args.mode == "plan":
            payload = plan.to_public_dict()
            print(json.dumps(payload, sort_keys=True) if args.json else payload)
            return 0

        if not args.yes:
            print("Apply requires explicit --yes after reviewing plan mode.", file=sys.stderr)
            return 2
        result = apply_live_plan(
            live_transport,
            manifest,
            plan,
            device_mac=args.device_mac,
        )
        payload = result.to_public_dict()
        print(json.dumps(payload, sort_keys=True) if args.json else payload)
        return 0
    except LiveAdapterError as exc:
        print("routerkit-netcraze-live: %s" % exc, file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
