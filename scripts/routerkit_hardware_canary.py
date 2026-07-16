#!/usr/bin/env python3
"""Pure offline validator and renderer for the Netcraze hardware-canary packet."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


PACKET_SCHEMA = "routerkit.netcraze.hardware-canary.v1"
PACKET_VERSION = 1
RELEASED_BASELINE = "v0.2.0-alpha.16"
EXPECTED_MAIN = "c8f697635c93584e85e76a1d734f8fa797a76b51"
STATUS = "HARDWARE_CANARY_PACKET_CONTRACT"

READY = "READY_FOR_HARDWARE_CANARY"
CHANGES_REQUIRED = "CHANGES_REQUIRED"
BLOCKED = "BLOCKED_BY_OFFLINE_EVIDENCE"

PHASE_IDS = (
    "P0_OPERATOR_PREFLIGHT",
    "P1_READ_ONLY_PLATFORM_INVENTORY",
    "P2_READ_ONLY_DEVICE_DISCOVERY_CONTRACT",
    "P3_READ_ONLY_POLICY_CONTRACT",
    "P4_OFF_DEVICE_COMPATIBILITY_DECISION",
    "P5_DISPOSABLE_CONNECTION_CANARY",
    "P6_DISPOSABLE_POLICY_CANARY",
    "P7_OPTIONAL_DISPOSABLE_ASSIGNMENT_CANARY",
    "P8_FULL_ROUTERKIT_INSTALL_CANARY",
    "P9_IDEMPOTENT_RERUN",
    "P10_FAILURE_AND_ROLLBACK",
    "P11_REBOOT_AND_RECOVERY",
    "P12_FINAL_INVARIANT_AUDIT",
    "P13_CLEANUP_AND_DEVICE_RETURN",
)

CATEGORIES = {
    "offline",
    "read_only_hardware",
    "authorized_disposable_write",
    "full_canary",
    "cleanup",
}

AUTHORIZATION_GATES = {
    "none",
    "read_only_session",
    "explicit_disposable_write",
    "explicit_full_canary",
    "cleanup_authority",
}

RISK_CLASSES = {"low", "medium", "high", "critical"}
OUTCOME_CATEGORIES = {"pass", "partial", "fail", "skip"}

TOP_KEYS = {
    "schema",
    "packet_version",
    "released_baseline",
    "expected_main",
    "target_scope",
    "global_invariants",
    "session_budget",
    "phases",
    "stop_conditions",
    "evidence_contract",
    "cleanup_contract",
    "readiness_requirements",
    "references",
}

TARGET_SCOPE_KEYS = {
    "planned_model",
    "planned_firmware",
    "planned_architecture",
    "planned_storage",
    "observed_target_policy",
}

PLANNED_VALUE_KEYS = {"value", "status"}
OBSERVED_POLICY_KEYS = {"mismatch_action", "support_expansion"}
INVARIANT_KEYS = {"id", "description"}
SESSION_BUDGET_KEYS = {
    "hard_session_ceiling_minutes",
    "cleanup_reserve_minutes",
    "patch_reentry_minimum_reserve_minutes",
}

PHASE_KEYS = {
    "id",
    "category",
    "dependencies",
    "estimated_minutes",
    "hard_timeout_minutes",
    "optional",
    "required_operator_authorization",
    "required_inputs",
    "checks",
    "stop_condition_ids",
    "private_evidence_categories",
    "public_evidence_fields",
    "rollback_check_ids",
    "completion_criteria",
}

CHECK_KEYS = {
    "id",
    "description",
    "outcome_categories",
    "risk_class",
    "evidence_category",
    "public_field",
}

STOP_KEYS = {"id", "description", "route_to_phase"}
EVIDENCE_KEYS = {
    "private_manifest_schema",
    "public_templates",
    "sensitivity_classes",
    "raw_evidence_rules",
    "required_categories",
}
CLEANUP_KEYS = {
    "cleanup_phase_id",
    "minimum_reserve_minutes",
    "required_check_ids",
    "final_outcomes",
}
READINESS_KEYS = {"id", "description", "required_reference"}

REQUIRED_STOP_IDS = {
    "S_BASELINE_MISMATCH",
    "S_AUTHORIZATION_MISSING",
    "S_UNEXPECTED_TARGET",
    "S_PRIVATE_EVIDENCE_UNSAFE",
    "S_BACKUP_UNAVAILABLE",
    "S_INTERFACE_AMBIGUOUS",
    "S_DEFAULT_POLICY_AMBIGUOUS",
    "S_REVISION_OR_PRECONDITION_MISSING",
    "S_SENSITIVE_SPILLOVER",
    "S_VERIFICATION_MISMATCH",
    "S_DEFAULT_POLICY_DELTA",
    "S_UNRELATED_STATE_DELTA",
    "S_ROLLBACK_UNCERTAIN",
    "S_CLEANUP_RESERVE_REACHED",
    "S_UNSUPPORTED_OR_AMBIGUOUS",
}

REQUIRED_READINESS_IDS = {
    "R_EXACT_BASELINE",
    "R_PHASE_GRAPH",
    "R_READ_CONTRACT",
    "R_DISPOSABLE_WRITE",
    "R_FULL_MATRIX",
    "R_EVIDENCE",
    "R_PATCH_BRANCH",
    "R_CLEANUP",
    "R_STATIC_GUARDS",
    "R_REVIEW_ZERO_FINDINGS",
}

REQUIRED_FINAL_OUTCOMES = {
    "PASS_CONTRACT_CAPTURE_ONLY",
    "PASS_DISPOSABLE_WRITE_CONTRACT",
    "PASS_FULL_CANARY",
    "PARTIAL_NEEDS_OFF_DEVICE_PATCH",
    "FAILED_ROLLBACK_COMPLETE",
    "FAILED_MANUAL_RECOVERY_REQUIRED",
    "STOP_UNSUPPORTED",
}

PROHIBITED_KEY_PARTS = (
    "password",
    "token",
    "credential",
    "uuid",
    "private_key",
    "subscription_url",
    "router_backup_content",
    "raw_output",
    "execute_command",
    "endpoint_with_auth",
    "shell",
)

SUSPICIOUS_VALUE_PATTERNS = (
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"(?:^|\s)--?[A-Za-z][A-Za-z0-9-]*(?:\s|$)"),
    re.compile(r"(?:&&|\|\||\$\(|`)"),
)


class PacketError(ValueError):
    pass


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_packet_path() -> Path:
    return repository_root() / "hardware" / "netcraze-canary-packet.v1.json"


def _append(errors: List[str], path: str, message: str) -> None:
    errors.append("{}: {}".format(path, message))


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_string_list(value: Any, *, nonempty: bool = True) -> bool:
    if not isinstance(value, list):
        return False
    if nonempty and not value:
        return False
    return all(_is_nonempty_string(item) for item in value)


def _check_exact_keys(
    value: Any,
    expected: Set[str],
    path: str,
    errors: List[str],
) -> bool:
    if not isinstance(value, dict):
        _append(errors, path, "must be an object")
        return False
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        _append(errors, path, "missing fields: {}".format(", ".join(missing)))
    if unknown:
        _append(errors, path, "unknown fields: {}".format(", ".join(unknown)))
    return not missing and not unknown


def _duplicates(values: Iterable[str]) -> Set[str]:
    seen: Set[str] = set()
    duplicates: Set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def _scan_prohibited_keys_and_values(value: Any, path: str, errors: List[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = key.casefold()
            for part in PROHIBITED_KEY_PARTS:
                if part in lowered:
                    _append(errors, "{}.{}".format(path, key), "prohibited machine-readable field")
            _scan_prohibited_keys_and_values(child, "{}.{}".format(path, key), errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_prohibited_keys_and_values(child, "{}[{}]".format(path, index), errors)
    elif isinstance(value, str):
        for pattern in SUSPICIOUS_VALUE_PATTERNS:
            if pattern.search(value):
                _append(errors, path, "contains a command-like or live-endpoint-like value")
                break


def load_packet(path: Path) -> Dict[str, Any]:
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise PacketError("could not inspect packet") from exc
    if size > 1024 * 1024:
        raise PacketError("packet exceeds the 1 MiB limit")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PacketError("could not read packet") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PacketError("packet is not valid UTF-8") from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PacketError("packet is not valid JSON") from exc
    if not isinstance(value, dict):
        raise PacketError("packet root must be an object")
    return value


def _validate_target_scope(packet: Mapping[str, Any], errors: List[str]) -> None:
    target = packet.get("target_scope")
    if not _check_exact_keys(target, TARGET_SCOPE_KEYS, "target_scope", errors):
        return
    assert isinstance(target, dict)
    for key in (
        "planned_model",
        "planned_firmware",
        "planned_architecture",
        "planned_storage",
    ):
        item = target[key]
        path = "target_scope.{}".format(key)
        if not _check_exact_keys(item, PLANNED_VALUE_KEYS, path, errors):
            continue
        if not _is_nonempty_string(item["value"]):
            _append(errors, "{}.value".format(path), "must be a non-empty string")
        if item["status"] != "expected_unverified":
            _append(errors, "{}.status".format(path), "must be expected_unverified")
    policy = target["observed_target_policy"]
    path = "target_scope.observed_target_policy"
    if _check_exact_keys(policy, OBSERVED_POLICY_KEYS, path, errors):
        if policy["mismatch_action"] != "stop_and_record_private_category":
            _append(errors, "{}.mismatch_action".format(path), "must stop and record privately")
        if policy["support_expansion"] != "prohibited_without_separate_review":
            _append(errors, "{}.support_expansion".format(path), "must prohibit automatic expansion")


def _validate_invariants(packet: Mapping[str, Any], errors: List[str]) -> None:
    invariants = packet.get("global_invariants")
    if not isinstance(invariants, list) or not invariants:
        _append(errors, "global_invariants", "must be a non-empty array")
        return
    ids: List[str] = []
    for index, item in enumerate(invariants):
        path = "global_invariants[{}]".format(index)
        if not _check_exact_keys(item, INVARIANT_KEYS, path, errors):
            continue
        if not _is_nonempty_string(item["id"]):
            _append(errors, "{}.id".format(path), "must be a non-empty string")
        else:
            ids.append(item["id"])
        if not _is_nonempty_string(item["description"]):
            _append(errors, "{}.description".format(path), "must be a non-empty string")
    duplicate_ids = _duplicates(ids)
    if duplicate_ids:
        _append(errors, "global_invariants", "duplicate IDs: {}".format(", ".join(sorted(duplicate_ids))))
    required = {
        "I_DEFAULT_POLICY_IMMUTABLE",
        "I_UNRELATED_STATE_IMMUTABLE",
        "I_EXPLICIT_DEVICE_SELECTION",
        "I_LOOPBACK_LISTENERS",
        "I_EXPECTED_RUNTIME",
        "I_LEGACY_INIT_DISABLED",
        "I_WRITE_GATES",
        "I_STOP_ON_FIRST_FAILURE",
        "I_NO_HARDWARE_CLAIM",
    }
    missing = sorted(required - set(ids))
    if missing:
        _append(errors, "global_invariants", "missing required IDs: {}".format(", ".join(missing)))


def _validate_session_budget(packet: Mapping[str, Any], errors: List[str]) -> Optional[Mapping[str, Any]]:
    budget = packet.get("session_budget")
    if not _check_exact_keys(budget, SESSION_BUDGET_KEYS, "session_budget", errors):
        return None
    assert isinstance(budget, dict)
    ceiling = budget["hard_session_ceiling_minutes"]
    reserve = budget["cleanup_reserve_minutes"]
    patch_reserve = budget["patch_reentry_minimum_reserve_minutes"]
    if not isinstance(ceiling, int) or isinstance(ceiling, bool) or not 60 <= ceiling <= 180:
        _append(errors, "session_budget.hard_session_ceiling_minutes", "must be an integer from 60 to 180")
    if not isinstance(reserve, int) or isinstance(reserve, bool) or reserve < 15:
        _append(errors, "session_budget.cleanup_reserve_minutes", "must be at least 15")
    if (
        not isinstance(patch_reserve, int)
        or isinstance(patch_reserve, bool)
        or not isinstance(reserve, int)
        or patch_reserve < reserve
    ):
        _append(
            errors,
            "session_budget.patch_reentry_minimum_reserve_minutes",
            "must be an integer no smaller than the cleanup reserve",
        )
    return budget


def _validate_phase_shape(
    phase: Mapping[str, Any],
    index: int,
    stop_ids: Set[str],
    errors: List[str],
) -> Tuple[List[str], List[str]]:
    path = "phases[{}]".format(index)
    check_ids: List[str] = []
    rollback_ids: List[str] = []
    if not _check_exact_keys(phase, PHASE_KEYS, path, errors):
        return check_ids, rollback_ids

    if phase["id"] != PHASE_IDS[index]:
        _append(errors, "{}.id".format(path), "expected {}".format(PHASE_IDS[index]))
    if phase["category"] not in CATEGORIES:
        _append(errors, "{}.category".format(path), "invalid category")
    if not _is_string_list(phase["dependencies"], nonempty=False):
        _append(errors, "{}.dependencies".format(path), "must be an array of strings")
    estimate = phase["estimated_minutes"]
    timeout = phase["hard_timeout_minutes"]
    if not isinstance(estimate, int) or isinstance(estimate, bool) or not 1 <= estimate <= 60:
        _append(errors, "{}.estimated_minutes".format(path), "must be an integer from 1 to 60")
    if (
        not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or not isinstance(estimate, int)
        or timeout < estimate
        or timeout > 60
    ):
        _append(errors, "{}.hard_timeout_minutes".format(path), "must be from estimate to 60")
    if not isinstance(phase["optional"], bool):
        _append(errors, "{}.optional".format(path), "must be boolean")
    if phase["required_operator_authorization"] not in AUTHORIZATION_GATES:
        _append(errors, "{}.required_operator_authorization".format(path), "invalid authorization gate")
    if not _is_string_list(phase["required_inputs"]):
        _append(errors, "{}.required_inputs".format(path), "must be a non-empty string array")
    if not _is_string_list(phase["stop_condition_ids"]):
        _append(errors, "{}.stop_condition_ids".format(path), "must be a non-empty string array")
    else:
        unknown_stops = sorted(set(phase["stop_condition_ids"]) - stop_ids)
        if unknown_stops:
            _append(errors, "{}.stop_condition_ids".format(path), "unknown IDs: {}".format(", ".join(unknown_stops)))
    if not _is_string_list(phase["private_evidence_categories"]):
        _append(errors, "{}.private_evidence_categories".format(path), "must be a non-empty string array")
    if not _is_string_list(phase["public_evidence_fields"]):
        _append(errors, "{}.public_evidence_fields".format(path), "must be a non-empty string array")
    if not _is_string_list(phase["rollback_check_ids"], nonempty=False):
        _append(errors, "{}.rollback_check_ids".format(path), "must be an array of strings")
    else:
        rollback_ids.extend(phase["rollback_check_ids"])
    if not _is_string_list(phase["completion_criteria"]):
        _append(errors, "{}.completion_criteria".format(path), "must be a non-empty string array")

    checks = phase["checks"]
    if not isinstance(checks, list) or not checks:
        _append(errors, "{}.checks".format(path), "must be a non-empty array")
        return check_ids, rollback_ids
    private_categories = set(phase["private_evidence_categories"]) if isinstance(
        phase["private_evidence_categories"], list
    ) else set()
    public_fields = set(phase["public_evidence_fields"]) if isinstance(
        phase["public_evidence_fields"], list
    ) else set()
    for check_index, check in enumerate(checks):
        check_path = "{}.checks[{}]".format(path, check_index)
        if not _check_exact_keys(check, CHECK_KEYS, check_path, errors):
            continue
        if not _is_nonempty_string(check["id"]):
            _append(errors, "{}.id".format(check_path), "must be a non-empty string")
        else:
            check_ids.append(check["id"])
            if not check["id"].startswith(phase["id"].split("_", 1)[0] + "_"):
                _append(errors, "{}.id".format(check_path), "must use the phase prefix")
        if not _is_nonempty_string(check["description"]):
            _append(errors, "{}.description".format(check_path), "must be a non-empty string")
        outcomes = check["outcome_categories"]
        if not _is_string_list(outcomes) or not set(outcomes).issubset(OUTCOME_CATEGORIES):
            _append(errors, "{}.outcome_categories".format(check_path), "contains an invalid outcome")
        if check["risk_class"] not in RISK_CLASSES:
            _append(errors, "{}.risk_class".format(check_path), "invalid risk class")
        if check["evidence_category"] not in private_categories:
            _append(errors, "{}.evidence_category".format(check_path), "not declared by the phase")
        if check["public_field"] not in public_fields:
            _append(errors, "{}.public_field".format(check_path), "not declared by the phase")
    duplicate_checks = _duplicates(check_ids)
    if duplicate_checks:
        _append(errors, "{}.checks".format(path), "duplicate check IDs: {}".format(", ".join(sorted(duplicate_checks))))
    return check_ids, rollback_ids


def _has_cycle(dependencies: Mapping[str, Sequence[str]]) -> bool:
    visiting: Set[str] = set()
    visited: Set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for dependency in dependencies.get(node, ()):
            if dependency in dependencies and visit(dependency):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in dependencies)


def _validate_phases(
    packet: Mapping[str, Any],
    stop_ids: Set[str],
    budget: Optional[Mapping[str, Any]],
    errors: List[str],
) -> None:
    phases = packet.get("phases")
    if not isinstance(phases, list):
        _append(errors, "phases", "must be an array")
        return
    if len(phases) != len(PHASE_IDS):
        _append(errors, "phases", "must contain exactly {} phases".format(len(PHASE_IDS)))
        return

    all_check_ids: List[str] = []
    rollback_ids: List[str] = []
    dependencies: Dict[str, Sequence[str]] = {}
    estimates: List[int] = []
    for index, phase in enumerate(phases):
        if not isinstance(phase, dict):
            _append(errors, "phases[{}]".format(index), "must be an object")
            continue
        check_ids, phase_rollback_ids = _validate_phase_shape(phase, index, stop_ids, errors)
        all_check_ids.extend(check_ids)
        rollback_ids.extend(phase_rollback_ids)
        if _is_nonempty_string(phase.get("id")) and isinstance(phase.get("dependencies"), list):
            dependencies[phase["id"]] = tuple(phase["dependencies"])
        if isinstance(phase.get("estimated_minutes"), int) and not isinstance(
            phase.get("estimated_minutes"), bool
        ):
            estimates.append(phase["estimated_minutes"])

    duplicate_global_checks = _duplicates(all_check_ids)
    if duplicate_global_checks:
        _append(errors, "phases", "duplicate check IDs across phases: {}".format(", ".join(sorted(duplicate_global_checks))))
    unknown_rollback = sorted(set(rollback_ids) - set(all_check_ids))
    if unknown_rollback:
        _append(errors, "phases", "rollback references unknown checks: {}".format(", ".join(unknown_rollback)))

    known_phases = set(PHASE_IDS)
    for phase_id, phase_dependencies in dependencies.items():
        unknown = sorted(set(phase_dependencies) - known_phases)
        if unknown:
            _append(errors, "phase {} dependencies".format(phase_id), "unknown phases: {}".format(", ".join(unknown)))
        if phase_id in phase_dependencies:
            _append(errors, "phase {} dependencies".format(phase_id), "self-dependency is forbidden")
    if _has_cycle(dependencies):
        _append(errors, "phases", "dependency graph contains a cycle")

    if dependencies.get("P5_DISPOSABLE_CONNECTION_CANARY") != (
        "P4_OFF_DEVICE_COMPATIBILITY_DECISION",
    ):
        _append(errors, "phases", "compatibility decision must directly precede the first write phase")
    if "P6_DISPOSABLE_POLICY_CANARY" not in dependencies.get(
        "P8_FULL_ROUTERKIT_INSTALL_CANARY", ()
    ):
        _append(errors, "phases", "full canary must depend on the disposable policy contract")
    if "P10_FAILURE_AND_ROLLBACK" not in dependencies.get(
        "P11_REBOOT_AND_RECOVERY", ()
    ):
        _append(errors, "phases", "reboot must follow failure and rollback checks")
    if dependencies.get("P13_CLEANUP_AND_DEVICE_RETURN") != (
        "P0_OPERATOR_PREFLIGHT",
    ):
        _append(errors, "phases", "cleanup must remain reachable directly after preflight")

    for phase_id in (
        "P5_DISPOSABLE_CONNECTION_CANARY",
        "P6_DISPOSABLE_POLICY_CANARY",
        "P7_OPTIONAL_DISPOSABLE_ASSIGNMENT_CANARY",
    ):
        phase = phases[PHASE_IDS.index(phase_id)]
        if phase.get("required_operator_authorization") != "explicit_disposable_write":
            _append(errors, phase_id, "requires explicit disposable-write authorization")
        if not phase.get("rollback_check_ids"):
            _append(errors, phase_id, "requires rollback checks")
        if not any("DEFAULT_POLICY_AUDIT" in check.get("id", "") for check in phase.get("checks", [])):
            _append(errors, phase_id, "requires a default-policy audit")

    for phase_id in (
        "P8_FULL_ROUTERKIT_INSTALL_CANARY",
        "P9_IDEMPOTENT_RERUN",
        "P10_FAILURE_AND_ROLLBACK",
        "P11_REBOOT_AND_RECOVERY",
        "P12_FINAL_INVARIANT_AUDIT",
    ):
        phase = phases[PHASE_IDS.index(phase_id)]
        if not any("DEFAULT_POLICY_AUDIT" in check.get("id", "") for check in phase.get("checks", [])):
            _append(errors, phase_id, "requires a default-policy audit")

    if budget is not None and len(estimates) == len(PHASE_IDS):
        ceiling = budget["hard_session_ceiling_minutes"]
        reserve = budget["cleanup_reserve_minutes"]
        if sum(estimates) != ceiling:
            _append(errors, "phases", "estimated minutes must exactly fill the hard session ceiling")
        cleanup_estimate = phases[-1].get("estimated_minutes")
        if cleanup_estimate < reserve:
            _append(errors, "P13_CLEANUP_AND_DEVICE_RETURN", "estimate must cover the cleanup reserve")
        if sum(estimates[:-1]) > ceiling - reserve:
            _append(errors, "phases", "pre-cleanup phases consume the protected cleanup reserve")


def _validate_stop_conditions(packet: Mapping[str, Any], errors: List[str]) -> Set[str]:
    stops = packet.get("stop_conditions")
    if not isinstance(stops, list) or not stops:
        _append(errors, "stop_conditions", "must be a non-empty array")
        return set()
    ids: List[str] = []
    for index, stop in enumerate(stops):
        path = "stop_conditions[{}]".format(index)
        if not _check_exact_keys(stop, STOP_KEYS, path, errors):
            continue
        if _is_nonempty_string(stop["id"]):
            ids.append(stop["id"])
        else:
            _append(errors, "{}.id".format(path), "must be a non-empty string")
        if not _is_nonempty_string(stop["description"]):
            _append(errors, "{}.description".format(path), "must be a non-empty string")
        if stop["route_to_phase"] != "P13_CLEANUP_AND_DEVICE_RETURN":
            _append(errors, "{}.route_to_phase".format(path), "must route to cleanup")
    duplicates = _duplicates(ids)
    if duplicates:
        _append(errors, "stop_conditions", "duplicate IDs: {}".format(", ".join(sorted(duplicates))))
    missing = sorted(REQUIRED_STOP_IDS - set(ids))
    if missing:
        _append(errors, "stop_conditions", "missing required IDs: {}".format(", ".join(missing)))
    return set(ids)


def _validate_evidence_contract(packet: Mapping[str, Any], errors: List[str]) -> None:
    evidence = packet.get("evidence_contract")
    if not _check_exact_keys(evidence, EVIDENCE_KEYS, "evidence_contract", errors):
        return
    assert isinstance(evidence, dict)
    if evidence["private_manifest_schema"] != "hardware/netcraze-canary-evidence.v1.schema.json":
        _append(errors, "evidence_contract.private_manifest_schema", "unexpected schema path")
    for key in (
        "public_templates",
        "sensitivity_classes",
        "raw_evidence_rules",
        "required_categories",
    ):
        if not _is_string_list(evidence[key]):
            _append(errors, "evidence_contract.{}".format(key), "must be a non-empty string array")
    required_classes = {
        "public_safe",
        "local_sensitive",
        "secret_bearing",
        "router_backup",
        "device_inventory",
        "credential_adjacent",
    }
    if isinstance(evidence["sensitivity_classes"], list):
        missing = sorted(required_classes - set(evidence["sensitivity_classes"]))
        if missing:
            _append(errors, "evidence_contract.sensitivity_classes", "missing: {}".format(", ".join(missing)))


def _validate_cleanup_contract(packet: Mapping[str, Any], errors: List[str]) -> None:
    cleanup = packet.get("cleanup_contract")
    if not _check_exact_keys(cleanup, CLEANUP_KEYS, "cleanup_contract", errors):
        return
    assert isinstance(cleanup, dict)
    if cleanup["cleanup_phase_id"] != "P13_CLEANUP_AND_DEVICE_RETURN":
        _append(errors, "cleanup_contract.cleanup_phase_id", "must identify the cleanup phase")
    if (
        not isinstance(cleanup["minimum_reserve_minutes"], int)
        or isinstance(cleanup["minimum_reserve_minutes"], bool)
        or cleanup["minimum_reserve_minutes"] < 15
    ):
        _append(errors, "cleanup_contract.minimum_reserve_minutes", "must be at least 15")
    if not _is_string_list(cleanup["required_check_ids"]):
        _append(errors, "cleanup_contract.required_check_ids", "must be a non-empty string array")
    else:
        known_check_ids = {
            check.get("id")
            for phase in packet.get("phases", [])
            if isinstance(phase, dict)
            for check in phase.get("checks", [])
            if isinstance(check, dict)
        }
        unknown = sorted(set(cleanup["required_check_ids"]) - known_check_ids)
        if unknown:
            _append(
                errors,
                "cleanup_contract.required_check_ids",
                "unknown check IDs: {}".format(", ".join(unknown)),
            )
    if not _is_string_list(cleanup["final_outcomes"]):
        _append(errors, "cleanup_contract.final_outcomes", "must be a non-empty string array")
    elif set(cleanup["final_outcomes"]) != REQUIRED_FINAL_OUTCOMES:
        _append(errors, "cleanup_contract.final_outcomes", "must contain the exact hardware-session outcomes")


def _validate_readiness(packet: Mapping[str, Any], errors: List[str]) -> None:
    requirements = packet.get("readiness_requirements")
    if not isinstance(requirements, list) or not requirements:
        _append(errors, "readiness_requirements", "must be a non-empty array")
        return
    ids: List[str] = []
    for index, requirement in enumerate(requirements):
        path = "readiness_requirements[{}]".format(index)
        if not _check_exact_keys(requirement, READINESS_KEYS, path, errors):
            continue
        if _is_nonempty_string(requirement["id"]):
            ids.append(requirement["id"])
        else:
            _append(errors, "{}.id".format(path), "must be a non-empty string")
        if not _is_nonempty_string(requirement["description"]):
            _append(errors, "{}.description".format(path), "must be a non-empty string")
        if not _is_nonempty_string(requirement["required_reference"]):
            _append(errors, "{}.required_reference".format(path), "must be a repository path")
    duplicates = _duplicates(ids)
    if duplicates:
        _append(errors, "readiness_requirements", "duplicate IDs: {}".format(", ".join(sorted(duplicates))))
    missing = sorted(REQUIRED_READINESS_IDS - set(ids))
    if missing:
        _append(errors, "readiness_requirements", "missing required IDs: {}".format(", ".join(missing)))


def _validate_references(
    packet: Mapping[str, Any],
    repo_root: Optional[Path],
    errors: List[str],
) -> None:
    references = packet.get("references")
    if not _is_string_list(references):
        _append(errors, "references", "must be a non-empty string array")
        return
    duplicates = _duplicates(references)
    if duplicates:
        _append(errors, "references", "duplicate paths: {}".format(", ".join(sorted(duplicates))))
    for index, reference in enumerate(references):
        path = Path(reference)
        if path.is_absolute() or ".." in path.parts:
            _append(errors, "references[{}]".format(index), "must be a relative repository path")
        if repo_root is not None and not (repo_root / path).is_file():
            _append(errors, "references[{}]".format(index), "referenced path is missing")


def validate_packet(
    packet: Mapping[str, Any],
    repo_root: Optional[Path] = None,
) -> List[str]:
    errors: List[str] = []
    if not _check_exact_keys(packet, TOP_KEYS, "packet", errors):
        _scan_prohibited_keys_and_values(packet, "packet", errors)
        return sorted(set(errors))
    if packet["schema"] != PACKET_SCHEMA:
        _append(errors, "schema", "must be {}".format(PACKET_SCHEMA))
    if packet["packet_version"] != PACKET_VERSION:
        _append(errors, "packet_version", "must be 1")
    if packet["released_baseline"] != RELEASED_BASELINE:
        _append(errors, "released_baseline", "must match the exact released baseline")
    if packet["expected_main"] != EXPECTED_MAIN:
        _append(errors, "expected_main", "must match the exact released commit")

    _scan_prohibited_keys_and_values(packet, "packet", errors)
    _validate_target_scope(packet, errors)
    _validate_invariants(packet, errors)
    budget = _validate_session_budget(packet, errors)
    stop_ids = _validate_stop_conditions(packet, errors)
    _validate_phases(packet, stop_ids, budget, errors)
    _validate_evidence_contract(packet, errors)
    _validate_cleanup_contract(packet, errors)
    _validate_readiness(packet, errors)
    _validate_references(packet, repo_root, errors)
    return sorted(set(errors))


def validate_private_manifest_schema(schema_path: Path) -> List[str]:
    errors: List[str] = []
    try:
        value = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ["private evidence schema: could not read valid UTF-8 JSON"]
    if not isinstance(value, dict):
        return ["private evidence schema: root must be an object"]
    if value.get("additionalProperties") is not False:
        errors.append("private evidence schema: root must reject unknown fields")
    properties = value.get("properties")
    if not isinstance(properties, dict):
        errors.append("private evidence schema: properties must be an object")
    elif properties.get("schema", {}).get("const") != "routerkit.netcraze.hardware-evidence.v1":
        errors.append("private evidence schema: unexpected schema identifier")
    text = json.dumps(value, sort_keys=True)
    for prohibited in ("raw_contents", "raw_payload", "artifact_contents"):
        if prohibited in text:
            errors.append("private evidence schema: raw content field is forbidden")
    artifact = value.get("$defs", {}).get("artifact", {})
    if artifact.get("additionalProperties") is not False:
        errors.append("private evidence schema: artifact entries must reject unknown fields")
    return sorted(set(errors))


def validate_private_manifest(
    manifest: Mapping[str, Any],
    packet: Mapping[str, Any],
) -> List[str]:
    errors: List[str] = []
    root_keys = {
        "schema",
        "session_id",
        "packet_version",
        "release",
        "commit",
        "started_at",
        "ended_at",
        "expected_target",
        "observed_target",
        "phases",
        "artifacts",
        "cleanup_status",
        "retention_decision",
    }
    if not _check_exact_keys(manifest, root_keys, "manifest", errors):
        return sorted(set(errors))
    if manifest["schema"] != "routerkit.netcraze.hardware-evidence.v1":
        _append(errors, "manifest.schema", "unexpected schema identifier")
    if manifest["packet_version"] != PACKET_VERSION:
        _append(errors, "manifest.packet_version", "must be 1")
    if manifest["release"] != RELEASED_BASELINE:
        _append(errors, "manifest.release", "must match the packet release")
    if not isinstance(manifest["commit"], str) or re.fullmatch(
        r"[0-9a-f]{40}", manifest["commit"]
    ) is None:
        _append(errors, "manifest.commit", "must be a lowercase 40-hex commit")
    if not isinstance(manifest["session_id"], str) or re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", manifest["session_id"]
    ) is None:
        _append(errors, "manifest.session_id", "has an invalid format")
    for field in ("started_at",):
        if not _is_nonempty_string(manifest[field]):
            _append(errors, "manifest.{}".format(field), "must be a date-time string")
    if manifest["ended_at"] is not None and not _is_nonempty_string(manifest["ended_at"]):
        _append(errors, "manifest.ended_at", "must be null or a date-time string")

    target_keys = {"model", "firmware", "architecture", "storage_state", "comparison"}
    for field in ("expected_target", "observed_target"):
        target = manifest[field]
        path = "manifest.{}".format(field)
        if not _check_exact_keys(target, target_keys, path, errors):
            continue
        for text_field in ("model", "firmware", "architecture", "storage_state"):
            if not _is_nonempty_string(target[text_field]) or len(target[text_field]) > 160:
                _append(errors, "{}.{}".format(path, text_field), "must be a bounded non-empty string")
        if target["comparison"] not in {
            "expected_unverified",
            "match",
            "mismatch",
            "unknown",
        }:
            _append(errors, "{}.comparison".format(path), "invalid comparison")

    packet_checks = {
        phase["id"]: {check["id"] for check in phase["checks"]}
        for phase in packet.get("phases", [])
        if isinstance(phase, dict) and isinstance(phase.get("checks"), list)
    }
    phase_keys = {
        "phase_id",
        "started_at",
        "ended_at",
        "outcome",
        "check_ids",
        "notes_category",
    }
    phases = manifest["phases"]
    phase_ids: List[str] = []
    if not isinstance(phases, list) or len(phases) > len(PHASE_IDS):
        _append(errors, "manifest.phases", "must be an array of at most 14 entries")
    else:
        for index, phase in enumerate(phases):
            path = "manifest.phases[{}]".format(index)
            if not _check_exact_keys(phase, phase_keys, path, errors):
                continue
            phase_id = phase["phase_id"]
            if phase_id not in packet_checks:
                _append(errors, "{}.phase_id".format(path), "unknown phase")
            else:
                phase_ids.append(phase_id)
                if not _is_string_list(phase["check_ids"], nonempty=False):
                    _append(errors, "{}.check_ids".format(path), "must be an array of strings")
                else:
                    duplicate_checks = _duplicates(phase["check_ids"])
                    if duplicate_checks:
                        _append(errors, "{}.check_ids".format(path), "contains duplicate IDs")
                    unknown_checks = sorted(set(phase["check_ids"]) - packet_checks[phase_id])
                    if unknown_checks:
                        _append(
                            errors,
                            "{}.check_ids".format(path),
                            "unknown IDs: {}".format(", ".join(unknown_checks)),
                        )
            if phase["outcome"] not in {"not_started", "pass", "partial", "fail", "skip"}:
                _append(errors, "{}.outcome".format(path), "invalid outcome")
            if phase["notes_category"] not in {
                "none",
                "expected",
                "limitation",
                "stop_reason",
                "rollback",
                "cleanup",
            }:
                _append(errors, "{}.notes_category".format(path), "invalid notes category")
        duplicate_phases = _duplicates(phase_ids)
        if duplicate_phases:
            _append(errors, "manifest.phases", "duplicate phase IDs: {}".format(", ".join(sorted(duplicate_phases))))

    artifact_keys = {
        "artifact_id",
        "phase_id",
        "check_id",
        "reference_kind",
        "reference",
        "byte_size",
        "sha256",
        "sensitivity_class",
        "retention_decision",
        "redaction_status",
        "notes_category",
    }
    artifacts = manifest["artifacts"]
    artifact_ids: List[str] = []
    if not isinstance(artifacts, list) or len(artifacts) > 512:
        _append(errors, "manifest.artifacts", "must be an array of at most 512 entries")
    else:
        for index, artifact in enumerate(artifacts):
            path = "manifest.artifacts[{}]".format(index)
            if not _check_exact_keys(artifact, artifact_keys, path, errors):
                continue
            artifact_id = artifact["artifact_id"]
            if not isinstance(artifact_id, str) or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", artifact_id
            ) is None:
                _append(errors, "{}.artifact_id".format(path), "has an invalid format")
            else:
                artifact_ids.append(artifact_id)
            phase_id = artifact["phase_id"]
            check_id = artifact["check_id"]
            if phase_id not in packet_checks:
                _append(errors, "{}.phase_id".format(path), "unknown phase")
            elif check_id not in packet_checks[phase_id]:
                _append(errors, "{}.check_id".format(path), "does not belong to the phase")
            if artifact["reference_kind"] not in {"local_filename", "opaque_reference"}:
                _append(errors, "{}.reference_kind".format(path), "invalid reference kind")
            reference = artifact["reference"]
            if not isinstance(reference, str) or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}", reference
            ) is None:
                _append(errors, "{}.reference".format(path), "must be an opaque basename without traversal")
            byte_size = artifact["byte_size"]
            if (
                not isinstance(byte_size, int)
                or isinstance(byte_size, bool)
                or not 0 <= byte_size <= 1073741824
            ):
                _append(errors, "{}.byte_size".format(path), "is outside the allowed range")
            if not isinstance(artifact["sha256"], str) or re.fullmatch(
                r"[0-9a-f]{64}", artifact["sha256"]
            ) is None:
                _append(errors, "{}.sha256".format(path), "must be lowercase 64-hex")
            if artifact["sensitivity_class"] not in {
                "public_safe",
                "local_sensitive",
                "secret_bearing",
                "router_backup",
                "device_inventory",
                "credential_adjacent",
            }:
                _append(errors, "{}.sensitivity_class".format(path), "invalid sensitivity class")
        duplicate_artifacts = _duplicates(artifact_ids)
        if duplicate_artifacts:
            _append(
                errors,
                "manifest.artifacts",
                "duplicate artifact IDs: {}".format(", ".join(sorted(duplicate_artifacts))),
            )

    if manifest["cleanup_status"] not in {
        "not_started",
        "in_progress",
        "complete",
        "manual_recovery_required",
    }:
        _append(errors, "manifest.cleanup_status", "invalid cleanup status")
    if manifest["retention_decision"] not in {
        "retain_private",
        "retain_sanitized_only",
        "secure_disposal_pending",
        "securely_disposed",
    }:
        _append(errors, "manifest.retention_decision", "invalid retention decision")
    return sorted(set(errors))


def _structural_repository_contract(
    packet: Mapping[str, Any],
    repo_root: Path,
) -> Dict[str, Any]:
    references = packet.get("references", [])
    referenced_paths_present = isinstance(references, list) and all(
        isinstance(item, str) and (repo_root / item).is_file() for item in references
    )
    readiness_ids = {
        item.get("id")
        for item in packet.get("readiness_requirements", [])
        if isinstance(item, dict)
    }
    test_path = repo_root / "tests" / "test_routerkit_hardware_canary.py"
    return {
        "baseline_release": packet.get("released_baseline"),
        "baseline_commit": packet.get("expected_main"),
        "referenced_paths_present": referenced_paths_present,
        "static_guard_contract_present": test_path.is_file(),
        "test_contract_present": test_path.is_file(),
        "review_gate_present": "R_REVIEW_ZERO_FINDINGS" in readiness_ids,
    }


def evaluate_offline_hardware_readiness(
    packet: Mapping[str, Any],
    repository_contract: Mapping[str, Any],
) -> str:
    packet_errors = validate_packet(packet)
    if packet_errors:
        return CHANGES_REQUIRED
    required_keys = {
        "baseline_release",
        "baseline_commit",
        "referenced_paths_present",
        "static_guard_contract_present",
        "test_contract_present",
        "review_gate_present",
    }
    if set(repository_contract) != required_keys:
        return CHANGES_REQUIRED
    if not repository_contract["referenced_paths_present"]:
        return BLOCKED
    if repository_contract["baseline_release"] != RELEASED_BASELINE:
        return CHANGES_REQUIRED
    if repository_contract["baseline_commit"] != EXPECTED_MAIN:
        return CHANGES_REQUIRED
    for key in (
        "static_guard_contract_present",
        "test_contract_present",
        "review_gate_present",
    ):
        if repository_contract[key] is not True:
            return CHANGES_REQUIRED
    return READY


def render_checklist(packet: Mapping[str, Any]) -> str:
    budget = packet["session_budget"]
    lines = [
        "# RouterKit Netcraze hardware-canary checklist",
        "",
        "Offline packet verdict: {}".format(READY),
        "Hardware validated: false",
        "Live contract confirmed: false",
        "Session ceiling: {} minutes".format(budget["hard_session_ceiling_minutes"]),
        "Cleanup reserve: {} minutes".format(budget["cleanup_reserve_minutes"]),
        "",
    ]
    for phase in packet["phases"]:
        optional = " (optional)" if phase["optional"] else ""
        lines.append(
            "## {}{} — estimate {} min / timeout {} min".format(
                phase["id"],
                optional,
                phase["estimated_minutes"],
                phase["hard_timeout_minutes"],
            )
        )
        lines.append("")
        lines.append("- Authorization: `{}`".format(phase["required_operator_authorization"]))
        for check in phase["checks"]:
            lines.append("- [ ] `{}` — {}".format(check["id"], check["description"]))
        lines.append("")
        lines.append("- Stop routes: {}".format(", ".join(phase["stop_condition_ids"])))
        if phase["rollback_check_ids"]:
            lines.append("- Rollback checks: {}".format(", ".join(phase["rollback_check_ids"])))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def phase_matrix(packet: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [
        {
            "phase_id": phase["id"],
            "category": phase["category"],
            "dependencies": phase["dependencies"],
            "estimated_minutes": phase["estimated_minutes"],
            "hard_timeout_minutes": phase["hard_timeout_minutes"],
            "optional": phase["optional"],
            "authorization": phase["required_operator_authorization"],
            "check_ids": [check["id"] for check in phase["checks"]],
            "rollback_check_ids": phase["rollback_check_ids"],
        }
        for phase in packet["phases"]
    ]


def render_matrix_text(packet: Mapping[str, Any]) -> str:
    lines = []
    for row in phase_matrix(packet):
        dependencies = ",".join(row["dependencies"]) if row["dependencies"] else "-"
        lines.append(
            "{phase_id} | {category} | deps={dependencies} | estimate={estimated_minutes} | "
            "timeout={hard_timeout_minutes} | optional={optional} | checks={checks}".format(
                dependencies=dependencies,
                checks=",".join(row["check_ids"]),
                **row
            )
        )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pure offline Netcraze hardware-canary packet validator."
    )
    parser.add_argument("mode", choices=("status", "validate", "render", "matrix"))
    parser.add_argument("--packet", metavar="PATH")
    parser.add_argument("--json", action="store_true")
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if args.mode == "status" and args.packet:
        raise PacketError("status reads no packet and does not accept --packet")
    if args.mode == "render" and args.json:
        raise PacketError("render produces the deterministic human checklist and does not accept --json")


def _print_status(as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                {
                    "status": STATUS,
                    "hardware_validated": False,
                    "live_contract_confirmed": False,
                },
                sort_keys=True,
            )
        )
    else:
        print(STATUS)
        print("hardware_validated=false")
        print("live_contract_confirmed=false")


def run_cli(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        _validate_args(args)
        if args.mode == "status":
            _print_status(args.json)
            return 0

        root = repository_root()
        packet_path = Path(args.packet) if args.packet else default_packet_path()
        packet = load_packet(packet_path)
        errors = validate_packet(packet, root)
        evidence_path = root / packet.get("evidence_contract", {}).get(
            "private_manifest_schema", "missing"
        )
        errors.extend(validate_private_manifest_schema(evidence_path))
        errors = sorted(set(errors))
        repository_contract = _structural_repository_contract(packet, root)
        verdict = evaluate_offline_hardware_readiness(packet, repository_contract)
        if any("referenced path is missing" in error for error in errors):
            verdict = BLOCKED
        elif errors:
            verdict = CHANGES_REQUIRED

        if args.mode == "validate":
            payload = {
                "status": STATUS,
                "verdict": verdict,
                "hardware_validated": False,
                "live_contract_confirmed": False,
                "errors": errors,
            }
            if args.json:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print("status={}".format(STATUS))
                print("verdict={}".format(verdict))
                print("hardware_validated=false")
                print("live_contract_confirmed=false")
                for error in errors:
                    print("error={}".format(error))
            if verdict == READY:
                return 0
            return 3 if verdict == BLOCKED else 2

        if errors:
            raise PacketError("packet validation failed before {} output".format(args.mode))
        if args.mode == "render":
            sys.stdout.write(render_checklist(packet))
            return 0
        if args.mode == "matrix":
            if args.json:
                print(json.dumps(phase_matrix(packet), indent=2, sort_keys=True))
            else:
                sys.stdout.write(render_matrix_text(packet))
            return 0
        raise AssertionError("unhandled mode")
    except PacketError as exc:
        print("routerkit hardware-canary: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run_cli())
