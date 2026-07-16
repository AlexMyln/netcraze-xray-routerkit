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
SAFE_MANIFEST_LABELS = {
    1: "primary",
    2: "fallback-1",
    3: "fallback-2",
}

READINESS_PLAN = "plan_ready"
READINESS_HARDWARE = "hardware_contract_pending"
READINESS_BLOCKED = "blocked"
SENSITIVITY_LOCAL = "local_sensitive"
SENSITIVITY_PUBLIC = "public_evidence_redacted"

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
class SnapshotConsistency:
    valid: bool
    default_identity_proven: bool
    diagnostic_category: str


@dataclass(frozen=True)
class ObjectReference:
    kind: str
    value: Optional[str] = None
    profile_slot: Optional[int] = None


@dataclass(frozen=True)
class DefaultPolicyProjection:
    status: str
    default_policy_ref: Optional[str]
    policy: Optional[Tuple[Any, ...]]
    connection: Optional[Tuple[Any, ...]]


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
    dependencies: Tuple[ObjectReference, ...]
    preconditions: Tuple[PlanPrecondition, ...]
    future_adapter_requirement: str
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
    default_policy_not_targeted: bool
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
        if label != SAFE_MANIFEST_LABELS[slot]:
            raise ManifestSchemaError("Local endpoint profile label is not code-owned.")
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


def validate_snapshot_consistency(snapshot: RouterStateSnapshot) -> SnapshotConsistency:
    """Validate every cross-object invariant used by planning and simulation."""

    for label, items in (("connection", snapshot.connections), ("policy", snapshot.policies)):
        ids = [item.object_id for item in items]
        names = [item.name.casefold() for item in items]
        if len(ids) != len(set(ids)) or len(names) != len(set(names)):
            raise SnapshotSchemaError("Router snapshot contains duplicate %s IDs or names." % label)

    connection_by_id = {item.object_id: item for item in snapshot.connections}
    policy_by_id = {item.object_id: item for item in snapshot.policies}
    for policy in snapshot.policies:
        if policy.connection_ref not in connection_by_id:
            raise SnapshotSchemaError("Router policy references an unknown connection.")

    assignment_pairs = [(item.device_mac, item.policy_ref) for item in snapshot.assignments]
    if len(assignment_pairs) != len(set(assignment_pairs)):
        raise SnapshotSchemaError("Router snapshot contains a duplicate assignment.")
    assignment_macs = [item.device_mac for item in snapshot.assignments]
    if len(assignment_macs) != len(set(assignment_macs)):
        raise SnapshotSchemaError("One device cannot be assigned to multiple policies.")
    for assignment in snapshot.assignments:
        if assignment.device_mac != _normalize_mac(assignment.device_mac):
            raise SnapshotSchemaError("Router assignment MAC identity is not normalized.")
        if assignment.policy_ref not in policy_by_id:
            raise SnapshotSchemaError("Router assignment references an unknown policy.")

    observed_defaults = [item for item in snapshot.policies if item.is_default_observed]
    default_identity_proven = False
    if snapshot.default_policy_status == "known":
        default_policy = policy_by_id.get(snapshot.default_policy_ref or "")
        if default_policy is None or observed_defaults != [default_policy]:
            raise SnapshotSchemaError("Known default-policy evidence is inconsistent.")
        default_connection = connection_by_id[default_policy.connection_ref]
        if not default_policy.semantic_complete or not default_connection.semantic_complete:
            raise SnapshotSchemaError("Known default-policy semantics are incomplete.")
        default_identity_proven = True
    elif snapshot.default_policy_status == "unknown":
        if snapshot.default_policy_ref is not None or observed_defaults:
            raise SnapshotSchemaError("Unknown default-policy evidence is inconsistent.")
    elif snapshot.default_policy_status == "ambiguous":
        if snapshot.default_policy_ref is not None or len(observed_defaults) < 2:
            raise SnapshotSchemaError("Ambiguous default-policy evidence is inconsistent.")
    else:
        raise SnapshotSchemaError("Default-policy status is unsupported.")

    capabilities = dict(snapshot.capabilities)
    default_capability = capabilities.get("default_policy_identity")
    allowed_default_capabilities = {
        "known": {None, "documented", "inferred"},
        "unknown": {None, "unknown", "hardware_confirmation_required"},
        "ambiguous": {None, "unknown", "hardware_confirmation_required"},
    }
    if default_capability not in allowed_default_capabilities[snapshot.default_policy_status]:
        raise SnapshotSchemaError("Default-policy capability contradicts the observation.")
    if snapshot.state == "supported" and any(
        capabilities.get(name) == "unknown"
        for name in ("connection_inventory", "policy_inventory")
    ):
        raise SnapshotSchemaError("Supported snapshot capabilities are internally inconsistent.")
    if capabilities.get("write_contract") == "documented":
        raise SnapshotSchemaError("Fixture snapshot cannot claim a completed write contract.")

    category = (
        "consistent_known_default"
        if default_identity_proven
        else "consistent_diagnostic_default"
    )
    return SnapshotConsistency(True, default_identity_proven, category)


def default_policy_projection(snapshot: RouterStateSnapshot) -> DefaultPolicyProjection:
    """Return the canonical default-policy semantics used by plan/simulation proofs."""

    validate_snapshot_consistency(snapshot)
    if snapshot.default_policy_status != "known" or snapshot.default_policy_ref is None:
        return DefaultPolicyProjection(
            snapshot.default_policy_status,
            snapshot.default_policy_ref,
            None,
            None,
        )
    policy = next(
        item for item in snapshot.policies if item.object_id == snapshot.default_policy_ref
    )
    connection = next(
        item for item in snapshot.connections if item.object_id == policy.connection_ref
    )
    return DefaultPolicyProjection(
        snapshot.default_policy_status,
        snapshot.default_policy_ref,
        (
            policy.object_id,
            policy.name,
            policy.connection_ref,
            policy.mode,
            policy.semantic_complete,
            policy.unrelated_rules,
            policy.is_default_observed,
        ),
        (
            connection.object_id,
            connection.name,
            connection.protocol,
            connection.host,
            connection.port,
            connection.auth_mode,
            connection.enabled,
            connection.semantic_complete,
        ),
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

    snapshot = RouterStateSnapshot(
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
    validate_snapshot_consistency(snapshot)
    return snapshot


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
    dependencies: Sequence[ObjectReference] = (),
    preconditions: Tuple[PlanPrecondition, ...] = (),
    future_adapter_requirement: str = "none",
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
        dependencies=tuple(dependencies),
        preconditions=preconditions,
        future_adapter_requirement=future_adapter_requirement,
        backup_required=backup_required,
        verification_checks=tuple(VerificationCheck(name, expected) for name, expected in checks),
        rollback_intent=rollback_intent,
        readiness=readiness,
        reason=reason,
    )


def _rollback_for(action: PlanAction) -> Optional[RollbackAction]:
    operations = {
        "create_connection": "remove_created_connection",
        "create_policy": "remove_created_policy",
        "assign_device": "remove_created_assignment",
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


def _simulation_object_id(object_type: str, slot: int) -> str:
    return "simulation:%s:slot-%d" % (object_type, slot)


def _existing_reference(object_type: str, object_id: str) -> ObjectReference:
    return ObjectReference("existing_%s" % object_type, value=object_id)


def _planned_reference(object_type: str, slot: int) -> ObjectReference:
    return ObjectReference("planned_%s" % object_type, profile_slot=slot)


def _reference_identity(reference: ObjectReference) -> Dict[str, Any]:
    return {
        "kind": reference.kind,
        "profile_slot": reference.profile_slot,
        "existing_reference": reference.value is not None,
    }


def _prove_default_policy_not_targeted(
    snapshot: RouterStateSnapshot, actions: Sequence[PlanAction]
) -> bool:
    consistency = validate_snapshot_consistency(snapshot)
    if not consistency.default_identity_proven or snapshot.default_policy_ref is None:
        return False
    default_policy = next(
        item for item in snapshot.policies if item.object_id == snapshot.default_policy_ref
    )
    default_connection = next(
        item for item in snapshot.connections if item.object_id == default_policy.connection_ref
    )
    mutating_operations = {"create_connection", "create_policy", "assign_device"}
    for action in actions:
        if action.operation not in mutating_operations:
            continue
        if action.observed_id in (default_policy.object_id, default_connection.object_id):
            return False
        if action.object_type == "policy" and action.target_name.casefold() == default_policy.name.casefold():
            return False
        if (
            action.object_type == "connection"
            and action.target_name.casefold() == default_connection.name.casefold()
        ):
            return False
        for dependency in action.dependencies:
            if dependency.value in (default_policy.object_id, default_connection.object_id):
                return False
    return True


def build_change_plan(
    manifest: LocalEndpointManifest,
    snapshot: RouterStateSnapshot,
    selected_device: Optional[SelectedDeviceRef] = None,
) -> ChangePlan:
    consistency = validate_snapshot_consistency(snapshot)
    actions: List[PlanAction] = []
    connection_refs: Dict[int, ObjectReference] = {}
    policy_refs: Dict[int, ObjectReference] = {}
    blocked = (
        snapshot.state != "supported"
        or snapshot.stale
        or not consistency.default_identity_proven
    )
    base_preconditions = _preconditions(snapshot)
    all_object_ids = {
        item.object_id for item in snapshot.connections
    } | {item.object_id for item in snapshot.policies}

    for profile in manifest.profiles:
        target = connection_name(profile)
        exact = sorted(
            (item for item in snapshot.connections if connection_equivalence(item, profile).equivalent),
            key=lambda item: item.object_id,
        )
        same_name = next((item for item in snapshot.connections if item.name.casefold() == target.casefold()), None)
        action_id = "%02d:connection" % profile.slot
        if len(exact) > 1:
            blocked = True
            actions.append(
                _action(
                    action_id,
                    "conflict",
                    "connection",
                    target,
                    profile,
                    proposed={"semantic_match": "ambiguous"},
                    preconditions=base_preconditions,
                    future_adapter_requirement="hardware_object_identity_contract",
                    readiness=READINESS_BLOCKED,
                    reason="multiple equivalent connections are ambiguous",
                )
            )
        elif exact:
            existing = exact[0]
            connection_refs[profile.slot] = _existing_reference(
                "connection", existing.object_id
            )
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
                    future_adapter_requirement="hardware_update_contract",
                    readiness=READINESS_BLOCKED,
                    reason=(
                        "same-name connection is semantically incomplete"
                        if not same_name.semantic_complete
                        else "same-name connection is not exactly equivalent"
                    ),
                )
            )
        else:
            simulation_id = _simulation_object_id("connection", profile.slot)
            if simulation_id in all_object_ids:
                blocked = True
                actions.append(
                    _action(
                        action_id,
                        "conflict",
                        "connection",
                        target,
                        profile,
                        preconditions=base_preconditions,
                        future_adapter_requirement="collision_free_object_identity",
                        readiness=READINESS_BLOCKED,
                        reason="generated connection identity collides with observed state",
                    )
                )
            else:
                connection_refs[profile.slot] = _planned_reference(
                    "connection", profile.slot
                )
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
        desired_ref = connection_refs.get(profile.slot)
        exact: List[ExistingPolicy] = []
        if desired_ref is not None and desired_ref.kind == "existing_connection":
            exact = sorted(
                (
                    item
                    for item in snapshot.policies
                    if policy_equivalence(item, desired_ref.value or "").equivalent
                ),
                key=lambda item: item.object_id,
            )
        same_name = next((item for item in snapshot.policies if item.name.casefold() == target.casefold()), None)
        action_id = "%02d:policy" % profile.slot
        if desired_ref is None:
            blocked = True
            actions.append(
                _action(
                    action_id,
                    "blocked",
                    "policy",
                    target,
                    profile,
                    preconditions=base_preconditions,
                    future_adapter_requirement="resolved_connection_dependency",
                    readiness=READINESS_BLOCKED,
                    reason="policy connection dependency is unavailable",
                )
            )
        elif len(exact) > 1:
            blocked = True
            actions.append(
                _action(
                    action_id,
                    "conflict",
                    "policy",
                    target,
                    profile,
                    dependencies=(desired_ref,),
                    preconditions=base_preconditions,
                    future_adapter_requirement="hardware_object_identity_contract",
                    readiness=READINESS_BLOCKED,
                    reason="multiple equivalent policies are ambiguous",
                )
            )
        elif exact:
            existing = exact[0]
            policy_refs[profile.slot] = _existing_reference("policy", existing.object_id)
            actions.append(
                _action(
                    action_id,
                    "reuse_policy",
                    "policy",
                    target,
                    profile,
                    observed_id=existing.object_id,
                    observed_name=existing.name,
                    proposed={"semantic_match": "exact"},
                    dependencies=(desired_ref,),
                    backup_required=False,
                    checks=(("policy_equivalent", "true"),),
                    rollback_intent="none",
                    readiness=READINESS_PLAN,
                )
            )
        elif same_name is not None:
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
                    proposed={"semantic_match": "false"},
                    dependencies=(desired_ref,),
                    preconditions=base_preconditions,
                    future_adapter_requirement="hardware_update_contract",
                    readiness=READINESS_BLOCKED,
                    reason=(
                        "default policy is immutable"
                        if same_name.object_id == snapshot.default_policy_ref
                        or same_name.is_default_observed
                        else (
                            "same-name policy is semantically incomplete"
                            if not same_name.semantic_complete
                            else "same-name policy is not exactly equivalent"
                        )
                    ),
                )
            )
        else:
            simulation_id = _simulation_object_id("policy", profile.slot)
            if simulation_id in all_object_ids:
                blocked = True
                actions.append(
                    _action(
                        action_id,
                        "conflict",
                        "policy",
                        target,
                        profile,
                        dependencies=(desired_ref,),
                        preconditions=base_preconditions,
                        future_adapter_requirement="collision_free_object_identity",
                        readiness=READINESS_BLOCKED,
                        reason="generated policy identity collides with observed state",
                    )
                )
            else:
                policy_refs[profile.slot] = _planned_reference("policy", profile.slot)
                actions.append(
                    _action(
                        action_id,
                        "create_policy",
                        "policy",
                        target,
                        profile,
                        proposed={"mode": "proxy_only", "default": False},
                        dependencies=(desired_ref,),
                        preconditions=base_preconditions,
                        checks=(("policy_exists", "true"), ("policy_equivalent", "true")),
                        rollback_intent="remove only the transaction-created policy",
                    )
                )

    if selected_device is not None:
        primary = manifest.profiles[0]
        desired_policy_ref = policy_refs.get(primary.slot)
        existing_assignment = next(
            (item for item in snapshot.assignments if item.device_mac == selected_device.mac), None
        )
        if desired_policy_ref is None:
            blocked = True
            actions.append(
                _action(
                    "80:assignment",
                    "blocked",
                    "assignment",
                    "selected-device",
                    primary,
                    preconditions=base_preconditions,
                    future_adapter_requirement="resolved_policy_dependency",
                    readiness=READINESS_BLOCKED,
                    reason="assignment policy dependency is unavailable",
                )
            )
        elif existing_assignment is None:
            actions.append(
                _action(
                    "80:assignment",
                    "assign_device",
                    "assignment",
                    "selected-device",
                    primary,
                    dependencies=(desired_policy_ref,),
                    preconditions=base_preconditions,
                    checks=(("assignment_matches", "true"),),
                    rollback_intent="remove only the transaction-created assignment",
                )
            )
        elif (
            desired_policy_ref.kind == "existing_policy"
            and existing_assignment.policy_ref == desired_policy_ref.value
        ):
            actions.append(
                _action(
                    "80:assignment",
                    "reuse_assignment",
                    "assignment",
                    "selected-device",
                    primary,
                    observed_id=selected_device.mac,
                    dependencies=(desired_policy_ref,),
                    backup_required=False,
                    checks=(("assignment_matches", "true"),),
                    rollback_intent="none",
                    readiness=READINESS_PLAN,
                )
            )
        else:
            blocked = True
            actions.append(
                _action(
                    "80:assignment",
                    "blocked",
                    "assignment",
                    "selected-device",
                    primary,
                    observed_id=selected_device.mac,
                    dependencies=(desired_policy_ref,),
                    preconditions=base_preconditions,
                    future_adapter_requirement="hardware_assignment_move_contract",
                    readiness=READINESS_BLOCKED,
                    reason=(
                        "existing assignment on default policy cannot be moved"
                        if existing_assignment.policy_ref == snapshot.default_policy_ref
                        else "existing assignment move is outside the fixture-first contract"
                    ),
                )
            )

    default_policy_not_targeted = _prove_default_policy_not_targeted(snapshot, actions)
    if not default_policy_not_targeted:
        blocked = True

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
            checks=(("default_policy_not_targeted", "true"),),
            readiness=READINESS_HARDWARE if default_policy_not_targeted else READINESS_BLOCKED,
            reason=None if default_policy_not_targeted else "default policy identity is not proven",
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
                "dependencies": [
                    _reference_identity(reference) for reference in item.dependencies
                ],
            }
            for item in actions
        ],
        "selected_device": selected_device is not None,
        "default_policy_status": snapshot.default_policy_status,
        "default_policy_not_targeted": default_policy_not_targeted,
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
        default_policy_not_targeted=default_policy_not_targeted,
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
            "dependencies": [asdict(item) for item in action.dependencies],
            "preconditions": [asdict(item) for item in action.preconditions],
            "future_adapter_requirement": action.future_adapter_requirement,
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
            "default_policy_not_targeted": plan.default_policy_not_targeted,
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
            "default_policy_not_targeted": plan.default_policy_not_targeted,
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
            (
                "Static plan does not target the observed default policy."
                if plan.default_policy_not_targeted
                else "Static default-policy non-targeting invariant is not proven."
            ),
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


def _resolve_simulation_reference(
    snapshot: RouterStateSnapshot, reference: ObjectReference
) -> Any:
    if reference.kind in ("existing_connection", "planned_connection"):
        object_id = (
            reference.value
            if reference.kind == "existing_connection"
            else _simulation_object_id("connection", reference.profile_slot or 0)
        )
        match = next(
            (item for item in snapshot.connections if item.object_id == object_id), None
        )
    elif reference.kind in ("existing_policy", "planned_policy"):
        object_id = (
            reference.value
            if reference.kind == "existing_policy"
            else _simulation_object_id("policy", reference.profile_slot or 0)
        )
        match = next((item for item in snapshot.policies if item.object_id == object_id), None)
    else:
        match = None
    if match is None:
        raise NetcrazePlanError("Synthetic simulation dependency could not be resolved.")
    return match


def _apply_simulated_action(
    snapshot: RouterStateSnapshot,
    action: PlanAction,
    plan: ChangePlan,
    profiles: Mapping[int, LocalProxyProfile],
) -> RouterStateSnapshot:
    profile = profiles.get(action.profile_slot) if action.profile_slot is not None else None
    if action.operation == "create_connection" and profile is not None:
        return _replace_snapshot(
            snapshot,
            connections=snapshot.connections
            + (
                ExistingProxyConnection(
                    _simulation_object_id("connection", profile.slot),
                    action.target_name,
                    profile.protocol,
                    profile.host,
                    profile.port,
                    profile.auth_mode,
                    profile.enabled,
                ),
            ),
        )
    if action.operation == "create_policy" and profile is not None:
        if len(action.dependencies) != 1:
            raise NetcrazePlanError("Synthetic policy dependency is invalid.")
        connection = _resolve_simulation_reference(snapshot, action.dependencies[0])
        return _replace_snapshot(
            snapshot,
            policies=snapshot.policies
            + (
                ExistingPolicy(
                    _simulation_object_id("policy", profile.slot),
                    action.target_name,
                    connection.object_id,
                    "proxy_only",
                ),
            ),
        )
    if action.operation == "assign_device" and plan.selected_device is not None:
        if len(action.dependencies) != 1:
            raise NetcrazePlanError("Synthetic assignment dependency is invalid.")
        policy = _resolve_simulation_reference(snapshot, action.dependencies[0])
        return _replace_snapshot(
            snapshot,
            assignments=snapshot.assignments
            + (ExistingDeviceAssignment(plan.selected_device.mac, policy.object_id),),
        )
    return snapshot


def _restore_simulated_state(snapshot: RouterStateSnapshot) -> RouterStateSnapshot:
    return snapshot


def simulate_change_plan(
    plan: ChangePlan,
    snapshot: RouterStateSnapshot,
    manifest: LocalEndpointManifest,
    *,
    fail_after: Optional[str] = None,
    rollback_failure: bool = False,
) -> SimulationResult:
    validate_snapshot_consistency(snapshot)
    initial_default = default_policy_projection(snapshot)
    if plan.blocked:
        default_unchanged = default_policy_projection(snapshot) == initial_default
        return SimulationResult(
            success=False,
            stopped_after=None,
            completed_actions=(),
            rollback_actions=(),
            rollback_succeeded=True,
            restored_initial_state=True,
            default_policy_unchanged=default_unchanged,
            unrelated_objects_unchanged=True,
            final_state=snapshot,
            error_category="plan_blocked",
        )
    initial = snapshot
    current = snapshot
    completed: List[str] = []
    rollback_stack: List[Tuple[RollbackAction, RouterStateSnapshot]] = []
    profiles = {item.slot: item for item in manifest.profiles}
    unrelated_before = _unrelated_identity(snapshot, plan)
    stopped_after = None
    error_category = None

    for action in plan.actions:
        before = current
        try:
            current = _apply_simulated_action(current, action, plan, profiles)
        except NetcrazePlanError:
            stopped_after = action.action_id
            error_category = "simulation_dependency"

        rollback = _rollback_for(action)
        if rollback is not None and current != before:
            rollback_stack.append((rollback, before))
        completed.append(action.action_id)
        if current != before and stopped_after is None:
            try:
                validate_snapshot_consistency(current)
                if default_policy_projection(current) != initial_default:
                    stopped_after = action.action_id
                    error_category = "default_policy_invariant"
            except SnapshotSchemaError:
                stopped_after = action.action_id
                error_category = "snapshot_consistency"
        if fail_after == action.action_id and stopped_after is None:
            stopped_after = action.action_id
            error_category = "injected_failure"
        if stopped_after is not None:
            break

    success = stopped_after is None
    if success:
        try:
            validate_snapshot_consistency(current)
            if default_policy_projection(current) != initial_default:
                success = False
                stopped_after = completed[-1] if completed else None
                error_category = "default_policy_invariant"
        except SnapshotSchemaError:
            success = False
            stopped_after = completed[-1] if completed else None
            error_category = "snapshot_consistency"
    performed_rollback: List[RollbackAction] = []
    rollback_succeeded = True
    if not success:
        for rollback, before in reversed(rollback_stack):
            performed_rollback.append(rollback)
            if rollback_failure:
                rollback_succeeded = False
                error_category = "rollback_failure"
                break
            current = _restore_simulated_state(before)
            try:
                validate_snapshot_consistency(current)
                if default_policy_projection(current) != initial_default:
                    raise SnapshotSchemaError("Rollback changed the default-policy projection.")
            except SnapshotSchemaError:
                rollback_succeeded = False
                error_category = "rollback_validation_failure"
                break

    try:
        validate_snapshot_consistency(current)
        default_unchanged = default_policy_projection(current) == initial_default
    except SnapshotSchemaError:
        default_unchanged = False
    unrelated_unchanged = _unrelated_identity(current, plan) == unrelated_before
    return SimulationResult(
        success=success and default_unchanged,
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
