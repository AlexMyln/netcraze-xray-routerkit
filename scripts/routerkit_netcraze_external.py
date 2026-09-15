#!/usr/bin/env python3
"""Transport-neutral transactions for native Netcraze Proxy/Policy state.

This module owns no router transport.  It consumes a protected running-config
snapshot, delegates planning and verification to the existing live semantic
compatibility layer, and emits an integrity-bound command packet for an
external operator such as the official Netcraze MCP/RMM agent.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import routerkit_netcraze_live as core
import routerkit_netcraze_live_compat as compat
from routerkit_devices import DeviceDiscoveryError, normalize_trusted_device_mac
from routerkit_netcraze_plan import (
    LocalEndpointManifest,
    LocalProxyProfile,
    NetcrazePlanError,
    load_local_endpoint_manifest,
)
from routerkit_private_io import (
    PrivateFileError,
    read_owner_only_text_file,
    write_private_text_exclusive,
)


EXTERNAL_TRANSACTION_SCHEMA = "routerkit.netcraze.external-transaction.v1"
EXTERNAL_VERIFICATION_SCHEMA = "routerkit.netcraze.external-verification.v1"
SNAPSHOT_MODE = "owner-only-running-config-file"
MAX_SNAPSHOT_BYTES = core.MAX_NDM_OUTPUT_BYTES
MAX_TRANSACTION_BYTES = 256 * 1024
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _state_payload(state: core.LiveState) -> Dict[str, object]:
    return {
        "schema": core.LIVE_SCHEMA,
        "proxies": [
            asdict(item) for item in sorted(state.proxies, key=lambda item: item.object_id)
        ],
        "policies": [
            asdict(item) for item in sorted(state.policies, key=lambda item: item.object_id)
        ],
        "assignments": [list(item) for item in sorted(state.assignments)],
        "default_guard": list(sorted(state.default_guard)),
    }


def state_fingerprint(state: core.LiveState) -> str:
    """Fingerprint all parser-visible native policy state without exposing it."""

    return _fingerprint(_state_payload(state))


def default_guard_fingerprint(state: core.LiveState) -> str:
    return _fingerprint(list(sorted(state.default_guard)))


def manifest_fingerprint(manifest: LocalEndpointManifest) -> str:
    return _fingerprint(
        {
            "schema": manifest.schema,
            "profiles": [asdict(item) for item in manifest.profiles],
        }
    )


def load_running_config_snapshot(path: Path) -> core.LiveState:
    """Read and parse one bounded private snapshot without returning raw text."""

    try:
        raw = read_owner_only_text_file(
            Path(path),
            maximum_bytes=MAX_SNAPSHOT_BYTES,
            description="Running-config snapshot",
        )
    except PrivateFileError as exc:
        raise core.LiveAdapterError(str(exc)) from None
    return core.parse_running_config(raw)


def _profile_by_slot(manifest: LocalEndpointManifest) -> Dict[int, LocalProxyProfile]:
    return {item.slot: item for item in manifest.profiles}


def _expected_post_state(
    manifest: LocalEndpointManifest,
    plan: core.LivePlan,
    state: core.LiveState,
    *,
    device_mac: Optional[str],
) -> core.LiveState:
    proxies = {item.object_id: item for item in state.proxies}
    policies = {item.object_id: item for item in state.policies}
    assignments = dict(state.assignments)
    profiles = _profile_by_slot(manifest)

    for binding in plan.bindings:
        profile = profiles[binding.slot]
        if binding.proxy_action == "create":
            if binding.proxy_id in proxies:
                raise core.LiveAdapterError("External plan selected an occupied native proxy slot.")
            proxies[binding.proxy_id] = core.ProxyState(
                binding.proxy_id,
                core.connection_name(profile),
                "socks5",
                profile.host,
                profile.port,
                profile.enabled,
                False,
            )

    for binding in plan.bindings:
        profile = profiles[binding.slot]
        if binding.policy_action == "create":
            if binding.policy_id in policies:
                raise core.LiveAdapterError("External plan selected an occupied native policy slot.")
            policies[binding.policy_id] = core.PolicyState(
                binding.policy_id,
                core.policy_name(profile),
                (binding.proxy_id,),
            )

    if device_mac is not None and plan.selected_slot is not None:
        target = next(
            item.policy_id for item in plan.bindings if item.slot == plan.selected_slot
        )
        if plan.assignment_action in ("assign", "move"):
            assignments[device_mac] = target

    return core.LiveState(
        proxies=tuple(sorted(proxies.values(), key=lambda item: item.object_id)),
        policies=tuple(sorted(policies.values(), key=lambda item: item.object_id)),
        assignments=tuple(sorted(assignments.items())),
        default_guard=state.default_guard,
    )


def _assignment_payload(
    plan: core.LivePlan,
    state: core.LiveState,
    *,
    device_mac: Optional[str],
) -> Dict[str, object]:
    if device_mac is None or plan.selected_slot is None:
        return {
            "action": "none",
            "device_mac": None,
            "selected_slot": None,
            "target_policy": None,
            "previous_policy": None,
        }
    target = next(item.policy_id for item in plan.bindings if item.slot == plan.selected_slot)
    return {
        "action": plan.assignment_action,
        "device_mac": device_mac,
        "selected_slot": plan.selected_slot,
        "target_policy": target,
        "previous_policy": state.assignment_map.get(device_mac),
    }


def _render_commands(
    manifest: LocalEndpointManifest,
    plan: core.LivePlan,
    assignment: Mapping[str, object],
) -> Tuple[List[str], List[str]]:
    profiles = _profile_by_slot(manifest)
    commands: List[str] = []
    proxy_rollbacks: List[str] = []
    policy_rollbacks: List[str] = []

    for binding in plan.bindings:
        if binding.proxy_action == "create":
            commands.extend(core.proxy_create_commands(binding, profiles[binding.slot]))
            proxy_rollbacks.append("no interface %s" % binding.proxy_id)
    for binding in plan.bindings:
        if binding.policy_action == "create":
            commands.extend(core.policy_create_commands(binding, profiles[binding.slot]))
            policy_rollbacks.append("no ip policy %s" % binding.policy_id)

    assignment_rollback: List[str] = []
    if assignment["action"] in ("assign", "move"):
        device_mac = str(assignment["device_mac"])
        target_policy = str(assignment["target_policy"])
        commands.append("ip hotspot host %s policy %s" % (device_mac, target_policy))
        previous = assignment["previous_policy"]
        if previous is None:
            assignment_rollback.append("no ip hotspot host %s policy" % device_mac)
        else:
            assignment_rollback.append(
                "ip hotspot host %s policy %s" % (device_mac, previous)
            )

    rollback_commands = (
        assignment_rollback
        + list(reversed(policy_rollbacks))
        + list(reversed(proxy_rollbacks))
    )
    return commands, rollback_commands


def build_external_transaction(
    manifest: LocalEndpointManifest,
    state: core.LiveState,
    *,
    device_mac: Optional[str] = None,
    profile_slot: Optional[int] = None,
    allow_move: bool = False,
    hardware_contract: str = core.SUPPORTED_CONTRACT,
) -> Dict[str, object]:
    if hardware_contract != core.SUPPORTED_CONTRACT:
        raise core.LiveAdapterError("External plan uses an unsupported hardware contract.")

    normalized_mac = None
    if device_mac is not None:
        try:
            normalized_mac = normalize_trusted_device_mac(device_mac)
        except DeviceDiscoveryError:
            raise core.LiveAdapterError("Selected device MAC is invalid or unsafe.") from None

    plan = compat.build_live_plan(
        manifest,
        state,
        device_mac=normalized_mac,
        profile_slot=profile_slot,
        allow_move=allow_move,
    )
    profiles = _profile_by_slot(manifest)
    bindings: List[Dict[str, object]] = []
    for binding in plan.bindings:
        profile = profiles[binding.slot]
        bindings.append(
            {
                "slot": binding.slot,
                "profile_label": profile.label,
                "endpoint": {
                    "protocol": profile.protocol,
                    "host": profile.host,
                    "port": profile.port,
                    "enabled": profile.enabled,
                    "auth_mode": profile.auth_mode,
                },
                "proxy_id": binding.proxy_id,
                "policy_id": binding.policy_id,
                "proxy_action": binding.proxy_action,
                "policy_action": binding.policy_action,
            }
        )

    assignment = _assignment_payload(plan, state, device_mac=normalized_mac)
    commands, rollback_commands = _render_commands(manifest, plan, assignment)
    write_required = bool(commands)
    expected_state = _expected_post_state(
        manifest,
        plan,
        state,
        device_mac=normalized_mac,
    )
    expected_post_fingerprint = state_fingerprint(expected_state)
    guard_fingerprint = default_guard_fingerprint(state)

    packet: Dict[str, object] = {
        "schema": EXTERNAL_TRANSACTION_SCHEMA,
        "hardware_contract": hardware_contract,
        "snapshot_mode": SNAPSHOT_MODE,
        "manifest_fingerprint": manifest_fingerprint(manifest),
        "pre_state_fingerprint": state_fingerprint(state),
        "expected_post_state_fingerprint": expected_post_fingerprint,
        "default_guard_fingerprint": guard_fingerprint,
        "bindings": bindings,
        "assignment": assignment,
        "default_policy_targeted": False,
        "backup_required": write_required,
        "write_required": write_required,
        "save_required": write_required,
        "commands": commands,
        "rollback_commands": rollback_commands,
        "pre_save_verification_required": write_required,
        "save_command": "system configuration save" if write_required else None,
        "saved_state_verification_required": write_required,
        "raw_running_config_included": False,
        "verification_expectations": {
            "expected_post_state_fingerprint": expected_post_fingerprint,
            "default_guard_fingerprint": guard_fingerprint,
            "semantic_match_count_per_binding": 1,
            "default_guard_unchanged": True,
            "fresh_snapshot_required": True,
            "transport_result_trusted": False,
        },
    }
    packet["transaction_fingerprint"] = _fingerprint(packet)
    validate_external_transaction(packet, manifest)
    return packet


def _strict_keys(value: Mapping[str, object], expected: Sequence[str], description: str) -> None:
    if set(value) != set(expected):
        raise core.LiveAdapterError("%s contains unsupported or missing fields." % description)


def _require_hash(value: object, description: str) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise core.LiveAdapterError("%s is invalid." % description)
    return value


def _plan_from_packet(packet: Mapping[str, object]) -> core.LivePlan:
    bindings = tuple(
        core.BindingPlan(
            slot=int(item["slot"]),
            port=int(item["endpoint"]["port"]),
            proxy_id=str(item["proxy_id"]),
            policy_id=str(item["policy_id"]),
            proxy_action=str(item["proxy_action"]),
            policy_action=str(item["policy_action"]),
        )
        for item in packet["bindings"]
    )
    assignment = packet["assignment"]
    return core.LivePlan(
        bindings=bindings,
        selected_device_present=assignment["device_mac"] is not None,
        selected_slot=assignment["selected_slot"],
        assignment_action=str(assignment["action"]),
    )


def validate_external_transaction(
    packet: Mapping[str, object], manifest: LocalEndpointManifest
) -> core.LivePlan:
    if not isinstance(packet, Mapping):
        raise core.LiveAdapterError("External transaction packet is malformed.")
    expected_top = (
        "schema",
        "hardware_contract",
        "snapshot_mode",
        "manifest_fingerprint",
        "pre_state_fingerprint",
        "expected_post_state_fingerprint",
        "default_guard_fingerprint",
        "bindings",
        "assignment",
        "default_policy_targeted",
        "backup_required",
        "write_required",
        "save_required",
        "commands",
        "rollback_commands",
        "pre_save_verification_required",
        "save_command",
        "saved_state_verification_required",
        "raw_running_config_included",
        "verification_expectations",
        "transaction_fingerprint",
    )
    _strict_keys(packet, expected_top, "External transaction packet")
    if packet["schema"] != EXTERNAL_TRANSACTION_SCHEMA:
        raise core.LiveAdapterError("External transaction schema is unsupported.")
    if packet["hardware_contract"] != core.SUPPORTED_CONTRACT:
        raise core.LiveAdapterError("External transaction hardware contract is unsupported.")
    if packet["snapshot_mode"] != SNAPSHOT_MODE:
        raise core.LiveAdapterError("External transaction snapshot mode is unsupported.")
    if packet["manifest_fingerprint"] != manifest_fingerprint(manifest):
        raise core.LiveAdapterError("External transaction does not match the endpoint manifest.")
    pre_fingerprint = _require_hash(packet["pre_state_fingerprint"], "Pre-state fingerprint")
    post_fingerprint = _require_hash(
        packet["expected_post_state_fingerprint"], "Expected post-state fingerprint"
    )
    guard_fingerprint = _require_hash(
        packet["default_guard_fingerprint"], "Default-guard fingerprint"
    )
    supplied_transaction_fingerprint = _require_hash(
        packet["transaction_fingerprint"], "Transaction fingerprint"
    )
    unsigned = dict(packet)
    del unsigned["transaction_fingerprint"]
    if not hmac.compare_digest(supplied_transaction_fingerprint, _fingerprint(unsigned)):
        raise core.LiveAdapterError("External transaction integrity verification failed.")

    if packet["default_policy_targeted"] is not False:
        raise core.LiveAdapterError("External transaction must not target the Default policy.")
    if packet["raw_running_config_included"] is not False:
        raise core.LiveAdapterError("External transaction must not contain raw running configuration.")

    raw_bindings = packet["bindings"]
    if not isinstance(raw_bindings, list) or len(raw_bindings) != len(manifest.profiles):
        raise core.LiveAdapterError("External transaction bindings are malformed.")
    expected_binding_keys = (
        "slot",
        "profile_label",
        "endpoint",
        "proxy_id",
        "policy_id",
        "proxy_action",
        "policy_action",
    )
    proxy_ids: List[str] = []
    policy_ids: List[str] = []
    for raw_binding, profile in zip(raw_bindings, manifest.profiles):
        if not isinstance(raw_binding, Mapping):
            raise core.LiveAdapterError("External transaction binding is malformed.")
        _strict_keys(raw_binding, expected_binding_keys, "External transaction binding")
        if type(raw_binding["slot"]) is not int or raw_binding["slot"] != profile.slot:
            raise core.LiveAdapterError("External transaction binding slot is invalid.")
        if raw_binding["profile_label"] != profile.label:
            raise core.LiveAdapterError("External transaction profile label is invalid.")
        endpoint = raw_binding["endpoint"]
        if not isinstance(endpoint, Mapping):
            raise core.LiveAdapterError("External transaction endpoint is malformed.")
        _strict_keys(
            endpoint,
            ("protocol", "host", "port", "enabled", "auth_mode"),
            "External transaction endpoint",
        )
        expected_endpoint = {
            "protocol": profile.protocol,
            "host": profile.host,
            "port": profile.port,
            "enabled": profile.enabled,
            "auth_mode": profile.auth_mode,
        }
        if _canonical_json(dict(endpoint)) != _canonical_json(expected_endpoint):
            raise core.LiveAdapterError("External transaction endpoint semantics are invalid.")
        proxy_id = raw_binding["proxy_id"]
        policy_id = raw_binding["policy_id"]
        if not isinstance(proxy_id, str) or not isinstance(policy_id, str):
            raise core.LiveAdapterError("External transaction native object identity is invalid.")
        core._numeric_id(proxy_id, core._PROXY_ID_RE, core.MAX_PROXY_INDEX)
        core._numeric_id(policy_id, core._POLICY_ID_RE, core.MAX_POLICY_INDEX)
        proxy_ids.append(proxy_id)
        policy_ids.append(policy_id)
        if raw_binding["proxy_action"] not in ("create", "reuse"):
            raise core.LiveAdapterError("External transaction proxy action is unsupported.")
        if raw_binding["policy_action"] not in ("create", "reuse"):
            raise core.LiveAdapterError("External transaction policy action is unsupported.")
    if len(proxy_ids) != len(set(proxy_ids)) or len(policy_ids) != len(set(policy_ids)):
        raise core.LiveAdapterError("External transaction reuses a native object across profiles.")

    assignment = packet["assignment"]
    if not isinstance(assignment, Mapping):
        raise core.LiveAdapterError("External transaction assignment is malformed.")
    _strict_keys(
        assignment,
        ("action", "device_mac", "selected_slot", "target_policy", "previous_policy"),
        "External transaction assignment",
    )
    action = assignment["action"]
    if action not in ("none", "reuse", "assign", "move"):
        raise core.LiveAdapterError("External transaction assignment action is unsupported.")
    if action == "none":
        if any(assignment[key] is not None for key in (
            "device_mac", "selected_slot", "target_policy", "previous_policy"
        )):
            raise core.LiveAdapterError("External transaction empty assignment is inconsistent.")
    else:
        try:
            normalized_mac = normalize_trusted_device_mac(assignment["device_mac"])
        except DeviceDiscoveryError:
            raise core.LiveAdapterError("External transaction assignment identity is invalid.") from None
        if normalized_mac != assignment["device_mac"]:
            raise core.LiveAdapterError("External transaction assignment identity is not normalized.")
        selected_slot = assignment["selected_slot"]
        if type(selected_slot) is not int:
            raise core.LiveAdapterError("External transaction assignment slot is invalid.")
        selected = next(
            (item for item in raw_bindings if item["slot"] == selected_slot), None
        )
        if selected is None or assignment["target_policy"] != selected["policy_id"]:
            raise core.LiveAdapterError("External transaction assignment target is invalid.")
        previous = assignment["previous_policy"]
        if previous is not None:
            if not isinstance(previous, str):
                raise core.LiveAdapterError("External transaction previous assignment is invalid.")
            core._numeric_id(previous, core._POLICY_ID_RE, core.MAX_POLICY_INDEX)
        if action == "assign" and previous is not None:
            raise core.LiveAdapterError("External transaction assignment pre-state is inconsistent.")
        if action == "reuse" and previous != assignment["target_policy"]:
            raise core.LiveAdapterError("External transaction reused assignment is inconsistent.")
        if action == "move" and (previous is None or previous == assignment["target_policy"]):
            raise core.LiveAdapterError("External transaction move pre-state is inconsistent.")

    plan = _plan_from_packet(packet)
    expected_commands, expected_rollbacks = _render_commands(manifest, plan, assignment)
    if packet["commands"] != expected_commands:
        raise core.LiveAdapterError("External transaction native command order is invalid.")
    if packet["rollback_commands"] != expected_rollbacks:
        raise core.LiveAdapterError("External transaction rollback command order is invalid.")
    write_required = bool(expected_commands)
    for field in (
        "backup_required",
        "write_required",
        "save_required",
        "pre_save_verification_required",
        "saved_state_verification_required",
    ):
        if packet[field] is not write_required:
            raise core.LiveAdapterError("External transaction write/save gates are inconsistent.")
    expected_save_command = "system configuration save" if write_required else None
    if packet["save_command"] != expected_save_command:
        raise core.LiveAdapterError("External transaction save command is inconsistent.")
    if not write_required and not hmac.compare_digest(pre_fingerprint, post_fingerprint):
        raise core.LiveAdapterError("External NOOP transaction changes expected state.")

    expectations = packet["verification_expectations"]
    if not isinstance(expectations, Mapping):
        raise core.LiveAdapterError("External transaction verification expectations are malformed.")
    expected_expectations = {
        "expected_post_state_fingerprint": post_fingerprint,
        "default_guard_fingerprint": guard_fingerprint,
        "semantic_match_count_per_binding": 1,
        "default_guard_unchanged": True,
        "fresh_snapshot_required": True,
        "transport_result_trusted": False,
    }
    if _canonical_json(dict(expectations)) != _canonical_json(expected_expectations):
        raise core.LiveAdapterError("External transaction verification expectations are invalid.")
    return plan


def _semantic_post_plan(
    manifest: LocalEndpointManifest,
    packet: Mapping[str, object],
    state: core.LiveState,
) -> core.LivePlan:
    assignment = packet["assignment"]
    post_plan = compat.build_live_plan(
        manifest,
        state,
        device_mac=assignment["device_mac"],
        profile_slot=assignment["selected_slot"],
        allow_move=False,
    )
    packet_bindings = packet["bindings"]
    for observed, expected in zip(post_plan.bindings, packet_bindings):
        if (
            observed.proxy_id != expected["proxy_id"]
            or observed.policy_id != expected["policy_id"]
            or observed.proxy_action != "reuse"
            or observed.policy_action != "reuse"
        ):
            raise core.LiveAdapterError("External verification found unexpected native semantic state.")
    if assignment["action"] != "none" and post_plan.assignment_action != "reuse":
        raise core.LiveAdapterError("External verification found an unexpected device assignment.")
    return post_plan


def verify_external_transaction(
    packet: Mapping[str, object],
    manifest: LocalEndpointManifest,
    state: core.LiveState,
    *,
    phase: str = "running",
) -> Dict[str, object]:
    if phase not in ("pre", "running", "saved"):
        raise core.LiveAdapterError("External verification phase is unsupported.")
    plan = validate_external_transaction(packet, manifest)
    actual_guard = default_guard_fingerprint(state)
    if not hmac.compare_digest(actual_guard, str(packet["default_guard_fingerprint"])):
        raise core.LiveAdapterError("Default-policy guard changed after external planning.")

    actual_state = state_fingerprint(state)
    if phase == "pre":
        if not hmac.compare_digest(actual_state, str(packet["pre_state_fingerprint"])):
            raise core.LiveAdapterError("External transaction pre-state is stale.")
        verified_post = False
    else:
        if not hmac.compare_digest(
            actual_state, str(packet["expected_post_state_fingerprint"])
        ):
            raise core.LiveAdapterError("External transaction post-state does not match the plan.")
        assignment = packet["assignment"]
        compat.verify_plan_applied(
            manifest,
            plan,
            state,
            device_mac=assignment["device_mac"],
        )
        _semantic_post_plan(manifest, packet, state)
        verified_post = True

    write_required = bool(packet["write_required"])
    return {
        "schema": EXTERNAL_VERIFICATION_SCHEMA,
        "transaction_fingerprint": packet["transaction_fingerprint"],
        "phase": phase,
        "verified": True,
        "pre_state_verified": phase == "pre",
        "running_state_verified": phase == "running" and verified_post,
        "saved_state_verified": phase == "saved" and verified_post,
        "commands_authorized": phase == "pre" and write_required,
        "save_authorized": phase == "running" and bool(packet["save_required"]),
        "backup_required": bool(packet["backup_required"]),
        "write_required": write_required,
        "save_required": bool(packet["save_required"]),
        "noop": not write_required,
        "default_policy_targeted": False,
        "transport_result_trusted": False,
    }


def _reject_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def load_external_transaction(path: Path) -> Dict[str, object]:
    try:
        text = read_owner_only_text_file(
            Path(path),
            maximum_bytes=MAX_TRANSACTION_BYTES,
            description="External transaction packet",
        )
        value = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except PrivateFileError as exc:
        raise core.LiveAdapterError(str(exc)) from None
    except (ValueError, TypeError):
        raise core.LiveAdapterError("External transaction packet is not valid JSON.") from None
    if not isinstance(value, dict):
        raise core.LiveAdapterError("External transaction packet is malformed.")
    return value


def write_external_transaction(path: Path, packet: Mapping[str, object]) -> None:
    encoded = json.dumps(packet, indent=2, sort_keys=True) + "\n"
    if len(encoded.encode("utf-8")) > MAX_TRANSACTION_BYTES:
        raise core.LiveAdapterError("External transaction packet exceeds its safety bound.")
    try:
        write_private_text_exclusive(Path(path), encoded)
    except PrivateFileError as exc:
        raise core.LiveAdapterError(str(exc)) from None


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan and verify RouterKit native state through protected snapshots."
    )
    parser.add_argument("mode", choices=("status", "plan", "verify"))
    parser.add_argument("--snapshot-file", required=True)
    parser.add_argument("--manifest-file")
    parser.add_argument("--transaction-file")
    parser.add_argument("--device-mac")
    parser.add_argument("--profile-slot", type=int)
    parser.add_argument("--move-device", action="store_true")
    parser.add_argument("--phase", choices=("pre", "running", "saved"), default="running")
    parser.add_argument("--contract", default=core.SUPPORTED_CONTRACT)
    args = parser.parse_args(argv)
    if args.mode in ("plan", "verify") and not args.manifest_file:
        parser.error("plan/verify require --manifest-file")
    if args.mode in ("plan", "verify") and not args.transaction_file:
        parser.error("plan/verify require --transaction-file")
    if (args.device_mac is None) != (args.profile_slot is None):
        parser.error("--device-mac and --profile-slot must be supplied together")
    if args.mode != "plan" and (args.device_mac is not None or args.move_device):
        parser.error("device selection is valid only with plan")
    if args.mode != "verify" and args.phase != "running":
        parser.error("--phase is valid only with verify")
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        state = load_running_config_snapshot(Path(args.snapshot_file))
        if args.mode == "status":
            payload = core._summary_state(state)
            payload["snapshot_mode"] = SNAPSHOT_MODE
            print(json.dumps(payload, sort_keys=True))
            return 0

        manifest = load_local_endpoint_manifest(Path(args.manifest_file))
        if args.mode == "plan":
            packet = build_external_transaction(
                manifest,
                state,
                device_mac=args.device_mac,
                profile_slot=args.profile_slot,
                allow_move=args.move_device,
                hardware_contract=args.contract,
            )
            write_external_transaction(Path(args.transaction_file), packet)
            print(
                json.dumps(
                    {
                        "schema": EXTERNAL_TRANSACTION_SCHEMA,
                        "transaction_fingerprint": packet["transaction_fingerprint"],
                        "write_required": packet["write_required"],
                        "save_required": packet["save_required"],
                        "backup_required": packet["backup_required"],
                        "command_count": len(packet["commands"]),
                        "transaction_file_written": True,
                    },
                    sort_keys=True,
                )
            )
            return 0

        packet = load_external_transaction(Path(args.transaction_file))
        result = verify_external_transaction(
            packet,
            manifest,
            state,
            phase=args.phase,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (core.LiveAdapterError, NetcrazePlanError, PrivateFileError) as exc:
        print("routerkit-netcraze-external: %s" % exc, file=sys.stderr)
        return getattr(exc, "exit_code", 1)


if __name__ == "__main__":
    raise SystemExit(main())
