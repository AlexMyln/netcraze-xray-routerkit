#!/usr/bin/env python3
"""Pure fixture-first Netcraze connection and policy planning.

This module deliberately contains no transport, process, thread, or live-device
adapter.  Protected fixtures are observations only: they cannot grant ownership,
backup success, revision trust, or permission to change router state.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from routerkit_private_io import PrivateFileError, read_owner_only_text_file


SOFTWARE_VERDICT = "SOFTWARE_PLAN_CORE_READY_HARDWARE_WRITE_CONTRACT_PENDING"
MANIFEST_SCHEMA = "routerkit.local-endpoints.v1"
SNAPSHOT_SCHEMA = "routerkit.netcraze.state.fixture.v1"
PLAN_SCHEMA = "routerkit.netcraze.change-plan.v1"
PUBLIC_EVIDENCE_SCHEMA = "routerkit.netcraze.public-evidence.v1"
SIMULATION_SCHEMA = "routerkit.netcraze.simulation.v1"

MAX_MANIFEST_BYTES = 32 * 1024
MAX_SNAPSHOT_BYTES = 256 * 1024
MAX_PROFILES = 3
MAX_CONNECTIONS = 128
MAX_POLICIES = 128
MAX_ASSIGNMENTS = 512
MAX_TEXT = 128
EXPECTED_PORTS = (1082, 1083, 1084)
LOOPBACK_HOSTS = ("127.0.0.1", "::1")

READINESS_PLAN = "plan_ready"
READINESS_HARDWARE = "hardware_contract_pending"
READINESS_BLOCKED = "blocked"
SENSITIVITY_LOCAL = "local_sensitive"
SENSITIVITY_PUBLIC = "public_evidence_redacted"

_PROOF_SENTINEL = object()
_FORBIDDEN_FIXTURE_KEYS = {
    "adapter_ownership_proof",
    "backup_complete",
    "backup_success",
    "delete_authorized",
    "live_adapter",
    "live_apply_capability",
    "owned",
    "ownership",
    "permission_to_delete",
    "permission_to_update",
    "trusted_revision",
    "update_authorized",
    "write_authority",
}


class NetcrazePlanError(Exception):
    """Secret-safe input or planning error."""


class ManifestSchemaError(NetcrazePlanError):
    pass


class SnapshotSchemaError(NetcrazePlanError):
    pass


class CliUsageError(NetcrazePlanError):
    pass


@dataclass(frozen=True)
class LocalProxyProfile:
    slot: int
    label: str
    host: str
    port: int
    enabled: bool
    protocol: str = "socks5"
    auth_mode: str = "none"


@dataclass(frozen=True)
class LocalEndpointManifest:
    profiles: Tuple[LocalProxyProfile, ...]
    schema: str = MANIFEST_SCHEMA


@dataclass(frozen=True)
class ExistingProxyConnection:
    object_id: str
    name: str
    protocol: str
    host: str
    port: int
    auth_mode: str
    enabled: bool
    semantic_complete: bool = True


@dataclass(frozen=True)
class ExistingPolicy:
    object_id: str
    name: str
    connection_ref: str
    mode: str
    is_default_observed: bool = False
    semantic_complete: bool = True
    unrelated_rules: bool = False


@dataclass(frozen=True)
class ExistingDeviceAssignment:
    device_mac: str
    policy_ref: str


@dataclass(frozen=True)
class RouterStateSnapshot:
    snapshot_id: str
    state: str
    stale: bool
    default_policy_status: str
    default_policy_ref: Optional[str]
    connections: Tuple[ExistingProxyConnection, ...]
    policies: Tuple[ExistingPolicy, ...]
    assignments: Tuple[ExistingDeviceAssignment, ...]
    capabilities: Tuple[Tuple[str, str], ...] = ()
    schema: str = SNAPSHOT_SCHEMA


@dataclass(frozen=True)
class SelectedDeviceRef:
    display_name: str
    mac: str


@dataclass(frozen=True)
class ObjectEquivalence:
    equivalent: bool
    complete: bool
    reason: str


@dataclass(frozen=True)
class AdapterOwnershipProof:
    object_type: str
    object_id: str
    exact_rollback: Tuple[Tuple[str, str], ...]
    _sentinel: object

    def __post_init__(self) -> None:
        if self._sentinel is not _PROOF_SENTINEL:
            raise ValueError("Ownership proof must come from reviewed adapter code.")

    @classmethod
    def from_reviewed_adapter(
        cls,
        object_type: str,
        object_id: str,
        exact_rollback: Mapping[str, str],
    ) -> "AdapterOwnershipProof":
        if object_type not in ("connection", "policy", "assignment"):
            raise ValueError("Unsupported ownership proof object type.")
        if not object_id or not exact_rollback:
            raise ValueError("Ownership proof requires an object ID and exact rollback data.")
        return cls(
            object_type=object_type,
            object_id=object_id,
            exact_rollback=tuple(sorted((str(k), str(v)) for k, v in exact_rollback.items())),
            _sentinel=_PROOF_SENTINEL,
        )


@dataclass(frozen=True)
class PlanPrecondition:
    name: str
    required: bool
    satisfied: bool
    evidence: str


@dataclass(frozen=True)
class VerificationCheck:
    name: str
    expected: str


@dataclass(frozen=True)
class RollbackAction:
    action_id: str
    operation: str
    object_type: str
    target: str
    intent: str


@dataclass(frozen=True)
class RollbackPlan:
    actions: Tuple[RollbackAction, ...]


@dataclass(frozen=True)
class PlanAction:
    action_id: str
    operation: str
    object_type: str
    target_name: str
    profile_slot: Optional[int]
    endpoint: Optional[str]
    observed_id: Optional[str]
    observed_name: Optional[str]
    proposed: Tuple[Tuple[str, str], ...]
    preconditions: Tuple[PlanPrecondition, ...]
    required_ownership_proof: str
    backup_required: bool
    verification_checks: Tuple[VerificationCheck, ...]
    rollback_intent: str
    readiness: str
    sensitivity: str = SENSITIVITY_LOCAL
    reason: Optional[str] = None


@dataclass(frozen=True)
class ChangePlan:
    actions: Tuple[PlanAction, ...]
    rollback: RollbackPlan
    fingerprint: str
    plan_status: str
    write_readiness: str
    default_policy_unchanged: bool
    selected_device: Optional[SelectedDeviceRef]
    software_verdict: str = SOFTWARE_VERDICT
    schema: str = PLAN_SCHEMA

    @property
    def blocked(self) -> bool:
        return self.plan_status == READINESS_BLOCKED


@dataclass(frozen=True)
class SimulationResult:
    success: bool
    stopped_after: Optional[str]
    completed_actions: Tuple[str, ...]
    rollback_actions: Tuple[RollbackAction, ...]
    rollback_succeeded: bool
    restored_initial_state: bool
    default_policy_unchanged: bool
    unrelated_objects_unchanged: bool
    final_state: RouterStateSnapshot
    error_category: Optional[str] = None
    schema: str = SIMULATION_SCHEMA


def _strict_keys(value: Mapping[str, Any], allowed: Iterable[str], description: str) -> None:
    unknown = set(value) - set(allowed)
    if unknown:
        raise SnapshotSchemaError("%s contains unsupported fields." % description)


def _clean_text(value: Any, description: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SnapshotSchemaError("%s must be text." % description)
    if re.search(r"[\x00-\x1f\x7f]", value):
        raise SnapshotSchemaError("%s contains control characters." % description)
    cleaned = value.strip()
    if (not cleaned and not allow_empty) or len(cleaned) > MAX_TEXT:
        raise SnapshotSchemaError("%s is invalid." % description)
    return cleaned


def _bool(value: Any, description: str) -> bool:
    if not isinstance(value, bool):
        raise SnapshotSchemaError("%s must be true or false." % description)
    return value


def _reject_fixture_trust(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in _FORBIDDEN_FIXTURE_KEYS:
                raise SnapshotSchemaError("Fixture input cannot grant ownership or write authority.")
            _reject_fixture_trust(child)
    elif isinstance(value, list):
        for child in value:
            _reject_fixture_trust(child)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _parse_json_object(text: str, description: str) -> Dict[str, Any]:
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        raise SnapshotSchemaError("%s is not valid JSON." % description) from None
    if not isinstance(value, dict):
        raise SnapshotSchemaError("%s must be a JSON object." % description)
    return value


def parse_local_endpoint_manifest(text: str) -> LocalEndpointManifest:
    value = _parse_json_object(text, "Local endpoint manifest")
    _strict_keys(value, ("schema", "profiles"), "Local endpoint manifest")
    if value.get("schema") != MANIFEST_SCHEMA:
        raise ManifestSchemaError("Unsupported local endpoint manifest schema.")
    profiles_value = value.get("profiles")
    if not isinstance(profiles_value, list) or not 1 <= len(profiles_value) <= MAX_PROFILES:
        raise ManifestSchemaError("Local endpoint manifest must contain one to three profiles.")

    profiles: List[LocalProxyProfile] = []
    seen_slots = set()
    seen_ports = set()
    for raw in profiles_value:
        if not isinstance(raw, dict):
            raise ManifestSchemaError("Local endpoint manifest contains a malformed profile.")
        unknown = set(raw) - {"slot", "label", "listen", "port", "enabled", "protocol"}
        if unknown:
            raise ManifestSchemaError("Local endpoint manifest contains unsupported fields.")
        slot = raw.get("slot")
        port = raw.get("port")
        if not isinstance(slot, int) or isinstance(slot, bool) or slot not in (1, 2, 3):
            raise ManifestSchemaError("Local endpoint profile slot is invalid.")
        if not isinstance(port, int) or isinstance(port, bool) or port not in EXPECTED_PORTS:
            raise ManifestSchemaError("Local endpoint profile port is outside the supported scope.")
        if slot in seen_slots or port in seen_ports:
            raise ManifestSchemaError("Local endpoint manifest contains duplicate slots or ports.")
        label = _clean_text(raw.get("label"), "Profile label")
        host = _clean_text(raw.get("listen"), "Local endpoint address")
        try:
            parsed_host = ipaddress.ip_address(host)
        except ValueError:
            raise ManifestSchemaError("Local endpoint address is invalid.") from None
        if str(parsed_host) not in LOOPBACK_HOSTS:
            raise ManifestSchemaError("Local endpoint must use an exact loopback address.")
        protocol = _clean_text(raw.get("protocol"), "Local endpoint protocol").casefold()
        if protocol != "socks5":
            raise ManifestSchemaError("Local endpoint protocol is unsupported.")
        enabled = raw.get("enabled")
        if not isinstance(enabled, bool):
            raise ManifestSchemaError("Local endpoint enabled state must be true or false.")
        profiles.append(LocalProxyProfile(slot, label, str(parsed_host), port, enabled))
        seen_slots.add(slot)
        seen_ports.add(port)
    profiles.sort(key=lambda item: item.slot)
    if [item.slot for item in profiles] != list(range(1, len(profiles) + 1)):
        raise ManifestSchemaError("Local endpoint profile slots must be contiguous from one.")
    return LocalEndpointManifest(tuple(profiles))


def load_local_endpoint_manifest(path: Path) -> LocalEndpointManifest:
    try:
        text = read_owner_only_text_file(
            Path(path), maximum_bytes=MAX_MANIFEST_BYTES, description="Local endpoint manifest"
        )
    except PrivateFileError as exc:
        raise ManifestSchemaError(str(exc)) from None
    return parse_local_endpoint_manifest(text)


def _parse_connection(raw: Any) -> ExistingProxyConnection:
    if not isinstance(raw, dict):
        raise SnapshotSchemaError("Router snapshot contains a malformed connection.")
    _strict_keys(
        raw,
        ("id", "name", "protocol", "host", "port", "auth_mode", "enabled", "semantic_complete"),
        "Router connection",
    )
    port = raw.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise SnapshotSchemaError("Router connection port is invalid.")
    host = _clean_text(raw.get("host"), "Router connection host")
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        raise SnapshotSchemaError("Router connection host is invalid.") from None
    return ExistingProxyConnection(
        object_id=_clean_text(raw.get("id"), "Router connection ID"),
        name=_clean_text(raw.get("name"), "Router connection name"),
        protocol=_clean_text(raw.get("protocol"), "Router connection protocol").casefold(),
        host=host,
        port=port,
        auth_mode=_clean_text(raw.get("auth_mode"), "Router connection authentication mode").casefold(),
        enabled=_bool(raw.get("enabled"), "Router connection enabled state"),
        semantic_complete=_bool(raw.get("semantic_complete", True), "Connection completeness"),
    )


def _parse_policy(raw: Any) -> ExistingPolicy:
    if not isinstance(raw, dict):
        raise SnapshotSchemaError("Router snapshot contains a malformed policy.")
    _strict_keys(
        raw,
        ("id", "name", "connection_ref", "mode", "is_default", "semantic_complete", "unrelated_rules"),
        "Router policy",
    )
    return ExistingPolicy(
        object_id=_clean_text(raw.get("id"), "Router policy ID"),
        name=_clean_text(raw.get("name"), "Router policy name"),
        connection_ref=_clean_text(raw.get("connection_ref"), "Router policy connection reference"),
        mode=_clean_text(raw.get("mode"), "Router policy mode").casefold(),
        is_default_observed=_bool(raw.get("is_default", False), "Observed default-policy label"),
        semantic_complete=_bool(raw.get("semantic_complete", True), "Policy completeness"),
        unrelated_rules=_bool(raw.get("unrelated_rules", False), "Policy unrelated-rules state"),
    )


def _normalize_mac(value: Any) -> str:
    text = _clean_text(value, "Device assignment MAC")
    compact = re.sub(r"[^0-9a-fA-F]", "", text)
    if len(compact) != 12 or not re.fullmatch(r"[0-9a-fA-F]{12}", compact):
        raise SnapshotSchemaError("Device assignment requires a valid MAC identity.")
    raw = bytes.fromhex(compact)
    if raw in (b"\x00" * 6, b"\xff" * 6) or raw[0] & 1:
        raise SnapshotSchemaError("Device assignment requires a unicast MAC identity.")
    return ":".join(compact[index : index + 2].lower() for index in range(0, 12, 2))


def _parse_assignment(raw: Any) -> ExistingDeviceAssignment:
    if not isinstance(raw, dict):
        raise SnapshotSchemaError("Router snapshot contains a malformed assignment.")
    _strict_keys(raw, ("device_mac", "policy_ref"), "Router device assignment")
    return ExistingDeviceAssignment(
        device_mac=_normalize_mac(raw.get("device_mac")),
        policy_ref=_clean_text(raw.get("policy_ref"), "Assigned policy reference"),
    )


def parse_router_state_snapshot(text: str) -> RouterStateSnapshot:
    value = _parse_json_object(text, "Router state fixture")
    _reject_fixture_trust(value)
    _strict_keys(
        value,
        ("schema", "snapshot_id", "state", "stale", "default_policy", "connections", "policies", "assignments", "capabilities"),
        "Router state fixture",
    )
    if value.get("schema") != SNAPSHOT_SCHEMA:
        raise SnapshotSchemaError("Unsupported router state fixture schema.")
    state = _clean_text(value.get("state"), "Router snapshot state").casefold()
    if state not in ("supported", "degraded", "contract_unverified"):
        raise SnapshotSchemaError("Router snapshot state is unsupported.")
    default = value.get("default_policy")
    if not isinstance(default, dict):
        raise SnapshotSchemaError("Router snapshot default-policy observation is required.")
    _strict_keys(default, ("status", "observed_ref"), "Default-policy observation")
    default_status = _clean_text(default.get("status"), "Default-policy status").casefold()
    if default_status not in ("known", "unknown", "ambiguous"):
        raise SnapshotSchemaError("Default-policy status is unsupported.")
    default_ref_raw = default.get("observed_ref")
    default_ref = None if default_ref_raw is None else _clean_text(default_ref_raw, "Default-policy reference")
    if default_status == "known" and not default_ref:
        raise SnapshotSchemaError("Known default policy requires an observed reference.")
    if default_status != "known" and default_ref is not None:
        raise SnapshotSchemaError("Unknown or ambiguous default policy cannot name one trusted reference.")

    raw_connections = value.get("connections")
    raw_policies = value.get("policies")
    raw_assignments = value.get("assignments")
    if not isinstance(raw_connections, list) or len(raw_connections) > MAX_CONNECTIONS:
        raise SnapshotSchemaError("Router snapshot connection inventory is invalid.")
    if not isinstance(raw_policies, list) or len(raw_policies) > MAX_POLICIES:
        raise SnapshotSchemaError("Router snapshot policy inventory is invalid.")
    if not isinstance(raw_assignments, list) or len(raw_assignments) > MAX_ASSIGNMENTS:
        raise SnapshotSchemaError("Router snapshot assignment inventory is invalid.")
    connections = tuple(_parse_connection(item) for item in raw_connections)
    policies = tuple(_parse_policy(item) for item in raw_policies)
    assignments = tuple(_parse_assignment(item) for item in raw_assignments)

    for label, items in (("connection", connections), ("policy", policies)):
        ids = [item.object_id for item in items]
        names = [item.name.casefold() for item in items]
        if len(ids) != len(set(ids)) or len(names) != len(set(names)):
            raise SnapshotSchemaError("Router snapshot contains duplicate %s IDs or names." % label)
    macs = [item.device_mac for item in assignments]
    if len(macs) != len(set(macs)):
        raise SnapshotSchemaError("Router snapshot contains duplicate device assignments.")

    policy_ids = {item.object_id for item in policies}
    if any(item.policy_ref not in policy_ids for item in assignments):
        raise SnapshotSchemaError("Router assignment references an unknown policy.")
    observed_defaults = [item.object_id for item in policies if item.is_default_observed]
    if default_status == "known":
        if default_ref not in policy_ids or observed_defaults != [default_ref]:
            raise SnapshotSchemaError("Known default-policy evidence is inconsistent.")
    elif default_status == "unknown" and observed_defaults:
        raise SnapshotSchemaError("Unknown default-policy evidence is inconsistent.")
    elif default_status == "ambiguous" and len(observed_defaults) < 2:
        raise SnapshotSchemaError("Ambiguous default-policy evidence requires multiple observations.")

    raw_capabilities = value.get("capabilities", {})
    if not isinstance(raw_capabilities, dict) or len(raw_capabilities) > 32:
        raise SnapshotSchemaError("Router snapshot capabilities are invalid.")
    capabilities = []
    for key, capability_state in sorted(raw_capabilities.items()):
        clean_key = _clean_text(key, "Capability name")
        clean_state = _clean_text(capability_state, "Capability state").casefold()
        if clean_state not in ("documented", "inferred", "unknown", "hardware_confirmation_required"):
            raise SnapshotSchemaError("Router snapshot capability state is unsupported.")
        capabilities.append((clean_key, clean_state))

    return RouterStateSnapshot(
        snapshot_id=_clean_text(value.get("snapshot_id"), "Router snapshot ID"),
        state=state,
        stale=_bool(value.get("stale"), "Router snapshot stale state"),
        default_policy_status=default_status,
        default_policy_ref=default_ref,
        connections=connections,
        policies=policies,
        assignments=assignments,
        capabilities=tuple(capabilities),
    )


def load_router_state_snapshot(path: Path) -> RouterStateSnapshot:
    try:
        text = read_owner_only_text_file(
            Path(path), maximum_bytes=MAX_SNAPSHOT_BYTES, description="Router state fixture"
        )
    except PrivateFileError as exc:
        raise SnapshotSchemaError(str(exc)) from None
    return parse_router_state_snapshot(text)


def selected_device_from_device_selection(
    selection: Any,
) -> Optional[SelectedDeviceRef]:
    if selection is None or not selection.selected:
        return None
    device = selection.device
    if device is None or not device.selectable:
        raise NetcrazePlanError("Selected device lacks a trusted selectable identity.")
    if device.stable_identifier_type != "mac":
        raise NetcrazePlanError("Selected device requires a trusted MAC identity.")
    return SelectedDeviceRef(
        display_name=_clean_text(device.display_name, "Selected device name"),
        mac=_normalize_mac(device.stable_identifier),
    )


def connection_equivalence(
    existing: ExistingProxyConnection, desired: LocalProxyProfile
) -> ObjectEquivalence:
    if not existing.semantic_complete:
        return ObjectEquivalence(False, False, "required connection fields are unknown")
    equivalent = (
        existing.protocol == desired.protocol
        and existing.host == desired.host
        and existing.port == desired.port
        and existing.auth_mode == desired.auth_mode
        and existing.enabled == desired.enabled
    )
    return ObjectEquivalence(equivalent, True, "exact semantic match" if equivalent else "semantic mismatch")


def policy_equivalence(existing: ExistingPolicy, connection_ref: str) -> ObjectEquivalence:
    if not existing.semantic_complete:
        return ObjectEquivalence(False, False, "required policy fields are unknown")
    equivalent = (
        existing.connection_ref == connection_ref
        and existing.mode == "proxy_only"
        and not existing.is_default_observed
        and not existing.unrelated_rules
    )
    return ObjectEquivalence(equivalent, True, "exact semantic match" if equivalent else "semantic mismatch")


def connection_name(profile: LocalProxyProfile) -> str:
    return "RouterKit-SOCKS-%d" % profile.port


def policy_name(profile: LocalProxyProfile) -> str:
    return "RouterKit-Policy-%d" % profile.port


def _proof_for(
    proofs: Sequence[AdapterOwnershipProof], object_type: str, object_id: str
) -> Optional[AdapterOwnershipProof]:
    matches = [item for item in proofs if item.object_type == object_type and item.object_id == object_id]
    if len(matches) > 1:
        raise NetcrazePlanError("Duplicate code-owned ownership proofs were supplied.")
    return matches[0] if matches else None


def _preconditions(snapshot: RouterStateSnapshot) -> Tuple[PlanPrecondition, ...]:
    return (
        PlanPrecondition("fresh_snapshot", True, not snapshot.stale, "synthetic fixture observation"),
        PlanPrecondition("backup_export", True, False, "future adapter contract pending"),
        PlanPrecondition("exact_revision", True, False, "fixture input cannot grant revision trust"),
    )


def _action(
    action_id: str,
    operation: str,
    object_type: str,
    target_name: str,
    profile: Optional[LocalProxyProfile],
    *,
    observed_id: Optional[str] = None,
    observed_name: Optional[str] = None,
    proposed: Optional[Mapping[str, Any]] = None,
    preconditions: Tuple[PlanPrecondition, ...] = (),
    ownership: str = "none",
    backup_required: bool = True,
    checks: Sequence[Tuple[str, str]] = (),
    rollback_intent: str = "none",
    readiness: str = READINESS_HARDWARE,
    reason: Optional[str] = None,
) -> PlanAction:
    endpoint = None if profile is None else "%s:%d" % (profile.host, profile.port)
    return PlanAction(
        action_id=action_id,
        operation=operation,
        object_type=object_type,
        target_name=target_name,
        profile_slot=None if profile is None else profile.slot,
        endpoint=endpoint,
        observed_id=observed_id,
        observed_name=observed_name,
        proposed=tuple(sorted((str(k), str(v)) for k, v in (proposed or {}).items())),
        preconditions=preconditions,
        required_ownership_proof=ownership,
        backup_required=backup_required,
        verification_checks=tuple(VerificationCheck(name, expected) for name, expected in checks),
        rollback_intent=rollback_intent,
        readiness=readiness,
        reason=reason,
    )


def _rollback_for(action: PlanAction) -> Optional[RollbackAction]:
    operations = {
        "create_connection": "remove_created_connection",
        "update_owned_connection": "restore_owned_connection",
        "create_policy": "remove_created_policy",
        "update_owned_policy": "restore_owned_policy",
        "assign_device": "remove_created_assignment",
        "move_owned_assignment": "restore_owned_assignment",
    }
    operation = operations.get(action.operation)
    if operation is None:
        return None
    return RollbackAction(
        action_id="rollback:%s" % action.action_id,
        operation=operation,
        object_type=action.object_type,
        target=action.target_name,
        intent=action.rollback_intent,
    )


def build_change_plan(
    manifest: LocalEndpointManifest,
    snapshot: RouterStateSnapshot,
    selected_device: Optional[SelectedDeviceRef] = None,
    ownership_proofs: Sequence[AdapterOwnershipProof] = (),
) -> ChangePlan:
    actions: List[PlanAction] = []
    connection_refs: Dict[int, str] = {}
    policy_refs: Dict[int, str] = {}
    blocked = snapshot.state != "supported" or snapshot.stale
    base_preconditions = _preconditions(snapshot)

    if snapshot.default_policy_status != "known":
        blocked = True

    for profile in manifest.profiles:
        target = connection_name(profile)
        exact = sorted(
            (item for item in snapshot.connections if connection_equivalence(item, profile).equivalent),
            key=lambda item: item.object_id,
        )
        same_name = next((item for item in snapshot.connections if item.name.casefold() == target.casefold()), None)
        action_id = "%02d:connection" % profile.slot
        if exact:
            existing = exact[0]
            connection_refs[profile.slot] = existing.object_id
            actions.append(
                _action(
                    action_id,
                    "reuse_connection",
                    "connection",
                    target,
                    profile,
                    observed_id=existing.object_id,
                    observed_name=existing.name,
                    proposed={"semantic_match": "exact"},
                    backup_required=False,
                    checks=(("connection_equivalent", "true"),),
                    rollback_intent="none",
                    readiness=READINESS_PLAN,
                )
            )
        elif same_name is not None:
            proof = _proof_for(ownership_proofs, "connection", same_name.object_id)
            if proof is None:
                blocked = True
                actions.append(
                    _action(
                        action_id,
                        "conflict",
                        "connection",
                        target,
                        profile,
                        observed_id=same_name.object_id,
                        observed_name=same_name.name,
                        proposed={"semantic_match": "false"},
                        preconditions=base_preconditions,
                        ownership="code_owned_required",
                        readiness=READINESS_BLOCKED,
                        reason="same-name connection is not exactly equivalent",
                    )
                )
            else:
                connection_refs[profile.slot] = same_name.object_id
                actions.append(
                    _action(
                        action_id,
                        "update_owned_connection",
                        "connection",
                        target,
                        profile,
                        observed_id=same_name.object_id,
                        observed_name=same_name.name,
                        proposed={"protocol": profile.protocol, "host": profile.host, "port": profile.port},
                        preconditions=base_preconditions,
                        ownership="code_owned_exact",
                        checks=(("connection_equivalent", "true"),),
                        rollback_intent="restore exact adapter-provided connection state",
                    )
                )
        else:
            connection_refs[profile.slot] = "planned-connection:%d" % profile.slot
            actions.append(
                _action(
                    action_id,
                    "create_connection",
                    "connection",
                    target,
                    profile,
                    proposed={
                        "protocol": profile.protocol,
                        "host": profile.host,
                        "port": profile.port,
                        "auth_mode": profile.auth_mode,
                        "enabled": profile.enabled,
                    },
                    preconditions=base_preconditions,
                    checks=(("connection_exists", "true"), ("connection_equivalent", "true")),
                    rollback_intent="remove only the transaction-created connection",
                )
            )

    for profile in manifest.profiles:
        target = policy_name(profile)
        desired_ref = connection_refs.get(profile.slot, "blocked-connection:%d" % profile.slot)
        exact = sorted(
            (item for item in snapshot.policies if policy_equivalence(item, desired_ref).equivalent),
            key=lambda item: item.object_id,
        )
        same_name = next((item for item in snapshot.policies if item.name.casefold() == target.casefold()), None)
        action_id = "%02d:policy" % profile.slot
        if exact:
            existing = exact[0]
            policy_refs[profile.slot] = existing.object_id
            actions.append(
                _action(
                    action_id,
                    "reuse_policy",
                    "policy",
                    target,
                    profile,
                    observed_id=existing.object_id,
                    observed_name=existing.name,
                    proposed={"semantic_match": "exact", "connection_ref": desired_ref},
                    backup_required=False,
                    checks=(("policy_equivalent", "true"),),
                    rollback_intent="none",
                    readiness=READINESS_PLAN,
                )
            )
        elif same_name is not None:
            if same_name.object_id == snapshot.default_policy_ref or same_name.is_default_observed:
                proof = None
                reason = "default policy is immutable"
            else:
                proof = _proof_for(ownership_proofs, "policy", same_name.object_id)
                reason = "same-name policy is not exactly equivalent"
            if proof is None:
                blocked = True
                actions.append(
                    _action(
                        action_id,
                        "conflict",
                        "policy",
                        target,
                        profile,
                        observed_id=same_name.object_id,
                        observed_name=same_name.name,
                        proposed={"semantic_match": "false", "connection_ref": desired_ref},
                        preconditions=base_preconditions,
                        ownership="code_owned_required",
                        readiness=READINESS_BLOCKED,
                        reason=reason,
                    )
                )
            else:
                policy_refs[profile.slot] = same_name.object_id
                actions.append(
                    _action(
                        action_id,
                        "update_owned_policy",
                        "policy",
                        target,
                        profile,
                        observed_id=same_name.object_id,
                        observed_name=same_name.name,
                        proposed={"mode": "proxy_only", "connection_ref": desired_ref},
                        preconditions=base_preconditions,
                        ownership="code_owned_exact",
                        checks=(("policy_equivalent", "true"),),
                        rollback_intent="restore exact adapter-provided policy state",
                    )
                )
        else:
            policy_refs[profile.slot] = "planned-policy:%d" % profile.slot
            actions.append(
                _action(
                    action_id,
                    "create_policy",
                    "policy",
                    target,
                    profile,
                    proposed={"mode": "proxy_only", "connection_ref": desired_ref, "default": False},
                    preconditions=base_preconditions,
                    checks=(("policy_exists", "true"), ("policy_equivalent", "true")),
                    rollback_intent="remove only the transaction-created policy",
                )
            )

    if selected_device is not None:
        primary = manifest.profiles[0]
        desired_policy_ref = policy_refs.get(primary.slot, "blocked-policy:%d" % primary.slot)
        existing_assignment = next(
            (item for item in snapshot.assignments if item.device_mac == selected_device.mac), None
        )
        if existing_assignment is None:
            actions.append(
                _action(
                    "80:assignment",
                    "assign_device",
                    "assignment",
                    "selected-device",
                    primary,
                    proposed={"policy_ref": desired_policy_ref},
                    preconditions=base_preconditions,
                    checks=(("assignment_matches", "true"),),
                    rollback_intent="remove only the transaction-created assignment",
                )
            )
        elif existing_assignment.policy_ref == desired_policy_ref:
            actions.append(
                _action(
                    "80:assignment",
                    "reuse_assignment",
                    "assignment",
                    "selected-device",
                    primary,
                    observed_id=selected_device.mac,
                    proposed={"policy_ref": desired_policy_ref},
                    backup_required=False,
                    checks=(("assignment_matches", "true"),),
                    rollback_intent="none",
                    readiness=READINESS_PLAN,
                )
            )
        else:
            proof = _proof_for(ownership_proofs, "assignment", selected_device.mac)
            if proof is None:
                blocked = True
                actions.append(
                    _action(
                        "80:assignment",
                        "blocked",
                        "assignment",
                        "selected-device",
                        primary,
                        observed_id=selected_device.mac,
                        proposed={"policy_ref": desired_policy_ref},
                        preconditions=base_preconditions,
                        ownership="code_owned_exact_rollback_required",
                        readiness=READINESS_BLOCKED,
                        reason="existing assignment cannot be moved without adapter-owned proof",
                    )
                )
            else:
                actions.append(
                    _action(
                        "80:assignment",
                        "move_owned_assignment",
                        "assignment",
                        "selected-device",
                        primary,
                        observed_id=selected_device.mac,
                        proposed={"policy_ref": desired_policy_ref},
                        preconditions=base_preconditions,
                        ownership="code_owned_exact",
                        checks=(("assignment_matches", "true"),),
                        rollback_intent="restore exact adapter-provided assignment",
                    )
                )

    actions.extend(
        (
            _action(
                "90:verify-connections",
                "verify",
                "connection",
                "all-planned-connections",
                None,
                backup_required=False,
                checks=(("all_connections_equivalent", "true"),),
                readiness=READINESS_HARDWARE,
            ),
            _action(
                "91:verify-policies",
                "verify",
                "policy",
                "all-planned-policies",
                None,
                backup_required=False,
                checks=(("all_policies_equivalent", "true"),),
                readiness=READINESS_HARDWARE,
            ),
        )
    )
    if selected_device is not None:
        actions.append(
            _action(
                "92:verify-assignment",
                "verify",
                "assignment",
                "selected-device",
                None,
                backup_required=False,
                checks=(("selected_assignment_verified", "true"),),
                readiness=READINESS_HARDWARE,
            )
        )
    actions.append(
        _action(
            "99:verify-default",
            "verify",
            "default_policy",
            "observed-default-policy",
            None,
            observed_id=snapshot.default_policy_ref,
            backup_required=False,
            checks=(("default_policy_unchanged", "true"),),
            readiness=READINESS_HARDWARE if snapshot.default_policy_status == "known" else READINESS_BLOCKED,
            reason=None if snapshot.default_policy_status == "known" else "default policy identity is not unambiguous",
        )
    )

    if blocked:
        actions = [replace(item, readiness=READINESS_BLOCKED) for item in actions]

    semantic_identity = {
        "profiles": [
            {
                "slot": item.slot,
                "host": item.host,
                "port": item.port,
                "enabled": item.enabled,
                "protocol": item.protocol,
            }
            for item in manifest.profiles
        ],
        "actions": [
            {
                "id": item.action_id,
                "operation": item.operation,
                "object_type": item.object_type,
                "target": item.target_name,
                "readiness": item.readiness,
                "reason_category": bool(item.reason),
            }
            for item in actions
        ],
        "selected_device": selected_device is not None,
        "default_policy_status": snapshot.default_policy_status,
    }
    fingerprint = hashlib.sha256(_canonical_json(semantic_identity).encode("utf-8")).hexdigest()
    rollback_actions = tuple(
        item for item in (_rollback_for(action) for action in reversed(actions)) if item is not None
    )
    return ChangePlan(
        actions=tuple(actions),
        rollback=RollbackPlan(rollback_actions),
        fingerprint=fingerprint,
        plan_status=READINESS_BLOCKED if blocked else READINESS_HARDWARE,
        write_readiness=READINESS_BLOCKED,
        default_policy_unchanged=True,
        selected_device=selected_device,
    )


def _action_json(action: PlanAction, *, public: bool) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "action_id": action.action_id,
        "operation": action.operation,
        "object_type": action.object_type,
        "profile_slot": action.profile_slot,
        "readiness": action.readiness,
        "backup_required": action.backup_required,
    }
    if action.reason:
        base["reason_category"] = "conflict_or_missing_precondition"
    if public:
        return base
    base.update(
        {
            "target_name": action.target_name,
            "target_name_sensitivity": SENSITIVITY_LOCAL,
            "endpoint": action.endpoint,
            "observed_id": action.observed_id,
            "observed_id_sensitivity": SENSITIVITY_LOCAL,
            "observed_name": action.observed_name,
            "observed_name_sensitivity": SENSITIVITY_LOCAL,
            "proposed": dict(action.proposed),
            "preconditions": [asdict(item) for item in action.preconditions],
            "required_ownership_proof": action.required_ownership_proof,
            "verification_checks": [asdict(item) for item in action.verification_checks],
            "rollback_intent": action.rollback_intent,
            "reason": action.reason,
            "sensitivity": action.sensitivity,
        }
    )
    return base


def render_plan_json(plan: ChangePlan, *, public_evidence: bool = False) -> str:
    if public_evidence:
        counts: Dict[str, int] = {}
        for action in plan.actions:
            counts[action.operation] = counts.get(action.operation, 0) + 1
        value = {
            "schema": PUBLIC_EVIDENCE_SCHEMA,
            "software_verdict": plan.software_verdict,
            "plan_status": plan.plan_status,
            "write_readiness": plan.write_readiness,
            "plan_fingerprint": plan.fingerprint,
            "default_policy_unchanged": plan.default_policy_unchanged,
            "selected_device_present": plan.selected_device is not None,
            "counts": counts,
            "actions": [_action_json(item, public=True) for item in plan.actions],
            "sensitivity": SENSITIVITY_PUBLIC,
            "redaction_notice": "Public evidence is minimized but is not an anonymity guarantee.",
        }
    else:
        selected = None
        if plan.selected_device is not None:
            selected = {
                "display_name": plan.selected_device.display_name,
                "display_name_sensitivity": SENSITIVITY_LOCAL,
                "mac": plan.selected_device.mac,
                "mac_sensitivity": SENSITIVITY_LOCAL,
            }
        value = {
            "schema": plan.schema,
            "software_verdict": plan.software_verdict,
            "plan_status": plan.plan_status,
            "write_readiness": plan.write_readiness,
            "plan_fingerprint": plan.fingerprint,
            "default_policy_unchanged": plan.default_policy_unchanged,
            "selected_device": selected,
            "actions": [_action_json(item, public=False) for item in plan.actions],
            "rollback": [asdict(item) for item in plan.rollback.actions],
            "sensitivity": SENSITIVITY_LOCAL,
        }
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_plan_text(plan: ChangePlan) -> str:
    lines = [
        "Netcraze offline change plan",
        "Status: %s" % plan.software_verdict,
        "Plan: %s; future write readiness: %s" % (plan.plan_status, plan.write_readiness),
        "Fingerprint: %s" % plan.fingerprint,
    ]
    if plan.selected_device is None:
        lines.append("Selected device: none (no assignment requested)")
    else:
        lines.append("Selected device: %s — %s" % (plan.selected_device.display_name, plan.selected_device.mac))
    for action in plan.actions:
        detail = "%s %s" % (action.operation, action.target_name)
        if action.endpoint:
            detail += " (%s)" % action.endpoint
        if action.reason:
            detail += " — %s" % action.reason
        lines.append("- [%s] %s" % (action.readiness, detail))
    lines.extend(
        (
            "Default policy unchanged proof: explicit verification required; no action targets it.",
            "No router connection, policy, device assignment, or default-policy write occurred.",
            "Live write contract remains hardware-confirmation pending.",
        )
    )
    return "\n".join(lines) + "\n"


def _replace_snapshot(
    snapshot: RouterStateSnapshot,
    *,
    connections: Optional[Sequence[ExistingProxyConnection]] = None,
    policies: Optional[Sequence[ExistingPolicy]] = None,
    assignments: Optional[Sequence[ExistingDeviceAssignment]] = None,
) -> RouterStateSnapshot:
    return replace(
        snapshot,
        connections=tuple(snapshot.connections if connections is None else connections),
        policies=tuple(snapshot.policies if policies is None else policies),
        assignments=tuple(snapshot.assignments if assignments is None else assignments),
    )


def _unrelated_identity(snapshot: RouterStateSnapshot, plan: ChangePlan) -> Tuple[Any, ...]:
    touched_names = {item.target_name for item in plan.actions}
    connections = tuple(
        item for item in snapshot.connections if item.name not in touched_names
    )
    policies = tuple(item for item in snapshot.policies if item.name not in touched_names)
    selected_mac = None if plan.selected_device is None else plan.selected_device.mac
    assignments = tuple(item for item in snapshot.assignments if item.device_mac != selected_mac)
    return connections, policies, assignments


def _simulated_connection_for_slot(
    snapshot: RouterStateSnapshot, plan: ChangePlan, slot: int
) -> ExistingProxyConnection:
    action = next(
        item
        for item in plan.actions
        if item.object_type == "connection" and item.profile_slot == slot and item.operation != "verify"
    )
    if action.observed_id is not None:
        match = next(
            (item for item in snapshot.connections if item.object_id == action.observed_id), None
        )
        if match is not None:
            return match
    return next(item for item in snapshot.connections if item.name == action.target_name)


def _simulated_policy_for_slot(
    snapshot: RouterStateSnapshot, plan: ChangePlan, slot: int
) -> ExistingPolicy:
    action = next(
        item
        for item in plan.actions
        if item.object_type == "policy" and item.profile_slot == slot and item.operation != "verify"
    )
    if action.observed_id is not None:
        match = next((item for item in snapshot.policies if item.object_id == action.observed_id), None)
        if match is not None:
            return match
    return next(item for item in snapshot.policies if item.name == action.target_name)


def simulate_change_plan(
    plan: ChangePlan,
    snapshot: RouterStateSnapshot,
    manifest: LocalEndpointManifest,
    *,
    fail_after: Optional[str] = None,
    rollback_failure: bool = False,
) -> SimulationResult:
    if plan.blocked:
        return SimulationResult(
            success=False,
            stopped_after=None,
            completed_actions=(),
            rollback_actions=(),
            rollback_succeeded=True,
            restored_initial_state=True,
            default_policy_unchanged=True,
            unrelated_objects_unchanged=True,
            final_state=snapshot,
            error_category="plan_blocked",
        )
    initial = snapshot
    current = snapshot
    completed: List[str] = []
    rollback_stack: List[Tuple[RollbackAction, RouterStateSnapshot]] = []
    profiles = {item.slot: item for item in manifest.profiles}
    default_before = (snapshot.default_policy_status, snapshot.default_policy_ref)
    unrelated_before = _unrelated_identity(snapshot, plan)
    stopped_after = None
    error_category = None

    for action in plan.actions:
        before = current
        profile = profiles.get(action.profile_slot) if action.profile_slot is not None else None
        if action.operation == "create_connection" and profile is not None:
            current = _replace_snapshot(
                current,
                connections=current.connections
                + (
                    ExistingProxyConnection(
                        "sim-connection-%d" % profile.slot,
                        action.target_name,
                        profile.protocol,
                        profile.host,
                        profile.port,
                        profile.auth_mode,
                        profile.enabled,
                    ),
                ),
            )
        elif action.operation == "update_owned_connection" and profile is not None:
            current = _replace_snapshot(
                current,
                connections=tuple(
                    ExistingProxyConnection(
                        item.object_id,
                        action.target_name,
                        profile.protocol,
                        profile.host,
                        profile.port,
                        profile.auth_mode,
                        profile.enabled,
                    )
                    if item.object_id == action.observed_id
                    else item
                    for item in current.connections
                ),
            )
        elif action.operation == "create_policy" and profile is not None:
            connection = _simulated_connection_for_slot(current, plan, profile.slot)
            current = _replace_snapshot(
                current,
                policies=current.policies
                + (
                    ExistingPolicy(
                        "sim-policy-%d" % profile.slot,
                        action.target_name,
                        connection.object_id,
                        "proxy_only",
                    ),
                ),
            )
        elif action.operation == "update_owned_policy" and profile is not None:
            connection = _simulated_connection_for_slot(current, plan, profile.slot)
            current = _replace_snapshot(
                current,
                policies=tuple(
                    ExistingPolicy(
                        item.object_id,
                        action.target_name,
                        connection.object_id,
                        "proxy_only",
                    )
                    if item.object_id == action.observed_id
                    else item
                    for item in current.policies
                ),
            )
        elif action.operation in ("assign_device", "move_owned_assignment") and plan.selected_device is not None:
            primary = manifest.profiles[0]
            policy = _simulated_policy_for_slot(current, plan, primary.slot)
            retained = tuple(
                item for item in current.assignments if item.device_mac != plan.selected_device.mac
            )
            current = _replace_snapshot(
                current,
                assignments=retained + (ExistingDeviceAssignment(plan.selected_device.mac, policy.object_id),),
            )

        rollback = _rollback_for(action)
        if rollback is not None and current != before:
            rollback_stack.append((rollback, before))
        completed.append(action.action_id)
        if fail_after == action.action_id:
            stopped_after = action.action_id
            error_category = "injected_failure"
            break

    success = stopped_after is None
    performed_rollback: List[RollbackAction] = []
    rollback_succeeded = True
    if not success:
        for rollback, before in reversed(rollback_stack):
            performed_rollback.append(rollback)
            if rollback_failure:
                rollback_succeeded = False
                error_category = "rollback_failure"
                break
            current = before

    default_unchanged = (current.default_policy_status, current.default_policy_ref) == default_before
    unrelated_unchanged = _unrelated_identity(current, plan) == unrelated_before
    return SimulationResult(
        success=success,
        stopped_after=stopped_after,
        completed_actions=tuple(completed),
        rollback_actions=tuple(performed_rollback),
        rollback_succeeded=rollback_succeeded,
        restored_initial_state=current == initial,
        default_policy_unchanged=default_unchanged,
        unrelated_objects_unchanged=unrelated_unchanged,
        final_state=current,
        error_category=error_category,
    )


def render_simulation_json(result: SimulationResult) -> str:
    value = {
        "schema": result.schema,
        "success": result.success,
        "stopped_after": result.stopped_after,
        "completed_actions": list(result.completed_actions),
        "rollback_actions": [asdict(item) for item in result.rollback_actions],
        "rollback_succeeded": result.rollback_succeeded,
        "restored_initial_state": result.restored_initial_state,
        "default_policy_unchanged": result.default_policy_unchanged,
        "unrelated_objects_unchanged": result.unrelated_objects_unchanged,
        "error_category": result.error_category,
        "hardware_proof": False,
    }
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline fixture-first Netcraze policy planner; no live adapter exists."
    )
    parser.add_argument("mode", nargs="?", default="status", choices=("status", "plan", "simulate"))
    parser.add_argument("--manifest-file", metavar="PATH")
    parser.add_argument("--state-file", metavar="PATH")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--public-evidence", action="store_true")
    parser.add_argument("--device-inventory-file", metavar="PATH")
    parser.add_argument("--device-choice", type=int)
    parser.add_argument("--fixture-simulation", action="store_true")
    parser.add_argument("--fail-after", metavar="ACTION_ID")
    parser.add_argument("--rollback-failure", action="store_true")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.mode == "status":
        if any(
            (
                args.manifest_file,
                args.state_file,
                args.public_evidence,
                args.device_inventory_file,
                args.device_choice is not None,
                args.fixture_simulation,
                args.fail_after,
                args.rollback_failure,
            )
        ):
            raise CliUsageError("status accepts only --json.")
        return
    if not args.manifest_file or not args.state_file:
        raise CliUsageError("plan and simulate require --manifest-file and --state-file.")
    if bool(args.device_inventory_file) != (args.device_choice is not None):
        raise CliUsageError(
            "Optional device assignment requires both --device-inventory-file and --device-choice."
        )
    if args.mode == "plan" and any((args.fixture_simulation, args.fail_after, args.rollback_failure)):
        raise CliUsageError("Simulation options are valid only in simulate mode.")
    if args.mode == "simulate" and not args.fixture_simulation:
        raise CliUsageError("simulate requires explicit --fixture-simulation.")
    if args.mode == "simulate" and args.public_evidence:
        raise CliUsageError("simulate does not support public-evidence output.")
    if args.mode == "simulate" and args.rollback_failure and not args.fail_after:
        raise CliUsageError("--rollback-failure requires --fail-after.")


def run_cli(
    argv: Optional[Sequence[str]] = None,
    device_selection_loader: Optional[Any] = None,
) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        validate_args(args)
        if args.mode == "status":
            if args.json:
                print(json.dumps({"status": SOFTWARE_VERDICT, "live_adapter": False}, sort_keys=True))
            else:
                print(SOFTWARE_VERDICT)
            return 0
        manifest = load_local_endpoint_manifest(Path(args.manifest_file))
        snapshot = load_router_state_snapshot(Path(args.state_file))
        selected_device = None
        if args.device_inventory_file:
            if device_selection_loader is None:
                raise CliUsageError("Device inventory support is unavailable in this entrypoint.")
            selection = device_selection_loader(
                Path(args.device_inventory_file), args.device_choice
            )
            selected_device = selected_device_from_device_selection(selection)
        plan = build_change_plan(manifest, snapshot, selected_device)
        if args.mode == "plan":
            if args.json or args.public_evidence:
                sys.stdout.write(render_plan_json(plan, public_evidence=args.public_evidence))
            else:
                sys.stdout.write(render_plan_text(plan))
            return 2 if plan.blocked else 0
        if args.fail_after and args.fail_after not in {item.action_id for item in plan.actions}:
            raise CliUsageError("--fail-after must name an action in the generated plan.")
        result = simulate_change_plan(
            plan,
            snapshot,
            manifest,
            fail_after=args.fail_after,
            rollback_failure=args.rollback_failure,
        )
        if args.json:
            sys.stdout.write(render_simulation_json(result))
        else:
            print("Fixture simulation: %s" % ("success" if result.success else "stopped"))
            print("Rollback restored initial state: %s" % result.restored_initial_state)
            print("Hardware behavior proven: false")
        return 0 if result.success or (result.rollback_succeeded and result.restored_initial_state) else 1
    except (CliUsageError, ManifestSchemaError, SnapshotSchemaError, NetcrazePlanError) as exc:
        print("routerkit netcraze-plan: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run_cli())
