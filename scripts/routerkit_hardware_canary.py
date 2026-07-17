#!/usr/bin/env python3
"""Pure offline validator and renderer for the Netcraze hardware-canary packet."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


PACKET_SCHEMA = "routerkit.netcraze.hardware-canary.v1"
PACKET_VERSION = 1
RELEASED_BASELINE = "v0.2.0-alpha.16"
EXPECTED_MAIN = "c8f697635c93584e85e76a1d734f8fa797a76b51"
STATUS = "HARDWARE_CANARY_PACKET_CONTRACT"
MAX_PACKET_BYTES = 1024 * 1024

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

REQUIRED_FINAL_OUTCOME_ORDER = (
    "PASS_CONTRACT_CAPTURE_ONLY",
    "PASS_DISPOSABLE_WRITE_CONTRACT",
    "PASS_FULL_CANARY",
    "PARTIAL_NEEDS_OFF_DEVICE_PATCH",
    "FAILED_ROLLBACK_COMPLETE",
    "FAILED_MANUAL_RECOVERY_REQUIRED",
    "STOP_UNSUPPORTED",
)

PASS_FINAL_OUTCOMES = {
    "PASS_CONTRACT_CAPTURE_ONLY",
    "PASS_DISPOSABLE_WRITE_CONTRACT",
    "PASS_FULL_CANARY",
}

FAILURE_FINAL_OUTCOMES = {
    "FAILED_ROLLBACK_COMPLETE",
    "FAILED_MANUAL_RECOVERY_REQUIRED",
    "STOP_UNSUPPORTED",
    "PARTIAL_NEEDS_OFF_DEVICE_PATCH",
}

P4_DECISIONS = {
    "GO_WITH_EXISTING_ALPHA16_CONTRACT",
    "OFF_DEVICE_NARROW_PATCH_REQUIRED",
    "STOP_UNSUPPORTED_OR_AMBIGUOUS",
}

REQUIRED_CLEANUP_CHECK_IDS = (
    "P13_REMOVE_DISPOSABLE",
    "P13_RESTORE_ASSIGNMENT",
    "P13_FINAL_STATE",
    "P13_PRIVATE_EVIDENCE",
    "P13_DEVICE_RETURN",
)

CANONICAL_SESSION_ROUTES = (
    (
        "read-contract stop and cleanup",
        (
            "P0_OPERATOR_PREFLIGHT",
            "P1_READ_ONLY_PLATFORM_INVENTORY",
            "P2_READ_ONLY_DEVICE_DISCOVERY_CONTRACT",
            "P3_READ_ONLY_POLICY_CONTRACT",
            "P4_OFF_DEVICE_COMPATIBILITY_DECISION",
            "P13_CLEANUP_AND_DEVICE_RETURN",
        ),
    ),
    (
        "patch stop and cleanup",
        (
            "P0_OPERATOR_PREFLIGHT",
            "P1_READ_ONLY_PLATFORM_INVENTORY",
            "P2_READ_ONLY_DEVICE_DISCOVERY_CONTRACT",
            "P3_READ_ONLY_POLICY_CONTRACT",
            "P4_OFF_DEVICE_COMPATIBILITY_DECISION",
            "P13_CLEANUP_AND_DEVICE_RETURN",
        ),
    ),
    (
        "normal without optional assignment",
        (
            "P0_OPERATOR_PREFLIGHT",
            "P1_READ_ONLY_PLATFORM_INVENTORY",
            "P2_READ_ONLY_DEVICE_DISCOVERY_CONTRACT",
            "P3_READ_ONLY_POLICY_CONTRACT",
            "P4_OFF_DEVICE_COMPATIBILITY_DECISION",
            "P5_DISPOSABLE_CONNECTION_CANARY",
            "P6_DISPOSABLE_POLICY_CANARY",
            "P8_FULL_ROUTERKIT_INSTALL_CANARY",
            "P9_IDEMPOTENT_RERUN",
            "P10_FAILURE_AND_ROLLBACK",
            "P11_REBOOT_AND_RECOVERY",
            "P12_FINAL_INVARIANT_AUDIT",
            "P13_CLEANUP_AND_DEVICE_RETURN",
        ),
    ),
    ("normal with optional assignment", PHASE_IDS),
)

CANONICAL_PHASE_CONTRACTS = {
    "P0_OPERATOR_PREFLIGHT": ("offline", (), 5, 5, False, "none", (), ("P0_BASELINE", "P0_AUTHORITY", "P0_PRIVACY")),
    "P1_READ_ONLY_PLATFORM_INVENTORY": ("read_only_hardware", ("P0_OPERATOR_PREFLIGHT",), 5, 5, False, "read_only_session", (), ("P1_TARGET", "P1_PLATFORM", "P1_MANAGEMENT")),
    "P2_READ_ONLY_DEVICE_DISCOVERY_CONTRACT": ("read_only_hardware", ("P1_READ_ONLY_PLATFORM_INVENTORY",), 10, 10, False, "read_only_session", (), ("P2_SCHEMAS", "P2_JOIN", "P2_UI", "P2_ERRORS")),
    "P3_READ_ONLY_POLICY_CONTRACT": ("read_only_hardware", ("P2_READ_ONLY_DEVICE_DISCOVERY_CONTRACT",), 10, 10, False, "read_only_session", (), ("P3_INVENTORY", "P3_IDENTIFIERS", "P3_BACKUP", "P3_DEFAULT")),
    "P4_OFF_DEVICE_COMPATIBILITY_DECISION": ("offline", ("P3_READ_ONLY_POLICY_CONTRACT",), 5, 5, False, "none", (), ("P4_DECISION", "P4_PATCH_SCOPE", "P4_REENTRY")),
    "P5_DISPOSABLE_CONNECTION_CANARY": ("authorized_disposable_write", ("P4_OFF_DEVICE_COMPATIBILITY_DECISION",), 10, 10, False, "explicit_disposable_write", ("P5_REMOVE", "P5_DEFAULT_POLICY_AUDIT"), ("P5_CREATE", "P5_READBACK", "P5_DEFAULT_POLICY_AUDIT", "P5_REMOVE")),
    "P6_DISPOSABLE_POLICY_CANARY": ("authorized_disposable_write", ("P5_DISPOSABLE_CONNECTION_CANARY",), 10, 10, False, "explicit_disposable_write", ("P6_REMOVE", "P6_DEFAULT_POLICY_AUDIT"), ("P6_CREATE", "P6_READBACK", "P6_DEFAULT_POLICY_AUDIT", "P6_REMOVE")),
    "P7_OPTIONAL_DISPOSABLE_ASSIGNMENT_CANARY": ("authorized_disposable_write", ("P6_DISPOSABLE_POLICY_CANARY",), 5, 5, True, "explicit_disposable_write", ("P7_RESTORE", "P7_DEFAULT_POLICY_AUDIT"), ("P7_ASSIGN", "P7_READBACK", "P7_DEFAULT_POLICY_AUDIT", "P7_RESTORE")),
    "P8_FULL_ROUTERKIT_INSTALL_CANARY": ("full_canary", ("P6_DISPOSABLE_POLICY_CANARY",), 15, 15, False, "explicit_full_canary", ("P10_INSTALL_ROLLBACK", "P10_HEALTHCHECK_ROLLBACK"), ("P8_PREREQUISITES", "P8_CLEAN_PLAN", "P8_RUNTIME", "P8_LISTENERS", "P8_DISCOVERY_PLAN", "P8_EGRESS", "P8_DEFAULT_POLICY_AUDIT")),
    "P9_IDEMPOTENT_RERUN": ("full_canary", ("P8_FULL_ROUTERKIT_INSTALL_CANARY",), 5, 5, False, "explicit_full_canary", ("P10_INSTALL_ROLLBACK", "P10_HEALTHCHECK_ROLLBACK"), ("P9_RERUN", "P9_PROFILE_UPDATE", "P9_DEFAULT_POLICY_AUDIT")),
    "P10_FAILURE_AND_ROLLBACK": ("full_canary", ("P9_IDEMPOTENT_RERUN",), 10, 10, False, "explicit_full_canary", ("P10_INSTALL_ROLLBACK", "P10_HEALTHCHECK_ROLLBACK", "P10_DISPOSABLE_WRITE_FAILURES", "P10_DEFAULT_POLICY_AUDIT"), ("P10_PLAN_FAILURE", "P10_BOOTSTRAP_PRECONDITION_FAILURE", "P10_PREFLIGHT_FAILURE", "P10_BACKUP_GATE_FAILURE", "P10_INSTALL_ROLLBACK", "P10_AUTOSTART_FAILURE", "P10_HEALTHCHECK_ROLLBACK", "P10_DISPOSABLE_WRITE_FAILURES", "P10_DEFAULT_POLICY_AUDIT")),
    "P11_REBOOT_AND_RECOVERY": ("full_canary", ("P10_FAILURE_AND_ROLLBACK",), 10, 10, False, "explicit_full_canary", ("P11_RUNTIME_RECOVERY", "P11_USB_RECOVERY", "P11_DEFAULT_POLICY_AUDIT"), ("P11_REBOOT", "P11_RUNTIME_RECOVERY", "P11_RERUN", "P11_USB_RECOVERY", "P11_DEFAULT_POLICY_AUDIT")),
    "P12_FINAL_INVARIANT_AUDIT": ("full_canary", ("P11_REBOOT_AND_RECOVERY",), 5, 5, False, "explicit_full_canary", ("P13_REMOVE_DISPOSABLE", "P13_RESTORE_ASSIGNMENT", "P13_FINAL_STATE"), ("P12_DEFAULT_POLICY_AUDIT", "P12_UNRELATED_STATE", "P12_RUNTIME_INVARIANTS", "P12_PRIVACY", "P12_UNSUPPORTED_REJECTION")),
    "P13_CLEANUP_AND_DEVICE_RETURN": ("cleanup", ("P0_OPERATOR_PREFLIGHT",), 15, 15, False, "cleanup_authority", ("P13_REMOVE_DISPOSABLE", "P13_RESTORE_ASSIGNMENT", "P13_FINAL_STATE"), REQUIRED_CLEANUP_CHECK_IDS),
}

CANONICAL_PHASE_STOPS_AND_EVIDENCE = {
    "P0_OPERATOR_PREFLIGHT": (("S_BASELINE_MISMATCH", "S_AUTHORIZATION_MISSING", "S_PRIVATE_EVIDENCE_UNSAFE", "S_CLEANUP_RESERVE_REACHED"), ("baseline_metadata", "operator_decision"), ("baseline_verified", "phase_result")),
    "P1_READ_ONLY_PLATFORM_INVENTORY": (("S_UNEXPECTED_TARGET", "S_SENSITIVE_SPILLOVER", "S_AUTHORIZATION_MISSING", "S_CLEANUP_RESERVE_REACHED"), ("target_inventory", "interface_contract"), ("target_match", "phase_result", "interface_category")),
    "P2_READ_ONLY_DEVICE_DISCOVERY_CONTRACT": (("S_INTERFACE_AMBIGUOUS", "S_SENSITIVE_SPILLOVER", "S_VERIFICATION_MISMATCH", "S_CLEANUP_RESERVE_REACHED"), ("interface_contract",), ("interface_category", "phase_result", "limitations")),
    "P3_READ_ONLY_POLICY_CONTRACT": (("S_BACKUP_UNAVAILABLE", "S_DEFAULT_POLICY_AMBIGUOUS", "S_REVISION_OR_PRECONDITION_MISSING", "S_INTERFACE_AMBIGUOUS", "S_SENSITIVE_SPILLOVER", "S_CLEANUP_RESERVE_REACHED"), ("router_state_projection", "interface_contract", "backup_metadata"), ("counts", "interface_category", "rollback_result", "default_policy_unchanged")),
    "P4_OFF_DEVICE_COMPATIBILITY_DECISION": (("S_UNSUPPORTED_OR_AMBIGUOUS", "S_REVISION_OR_PRECONDITION_MISSING", "S_CLEANUP_RESERVE_REACHED"), ("operator_decision",), ("phase_result", "limitations")),
    "P5_DISPOSABLE_CONNECTION_CANARY": (("S_AUTHORIZATION_MISSING", "S_BACKUP_UNAVAILABLE", "S_REVISION_OR_PRECONDITION_MISSING", "S_VERIFICATION_MISMATCH", "S_DEFAULT_POLICY_DELTA", "S_UNRELATED_STATE_DELTA", "S_ROLLBACK_UNCERTAIN", "S_CLEANUP_RESERVE_REACHED"), ("disposable_object_state", "router_state_projection"), ("phase_result", "counts", "default_policy_unchanged", "unrelated_state_unchanged")),
    "P6_DISPOSABLE_POLICY_CANARY": (("S_AUTHORIZATION_MISSING", "S_BACKUP_UNAVAILABLE", "S_REVISION_OR_PRECONDITION_MISSING", "S_VERIFICATION_MISMATCH", "S_DEFAULT_POLICY_DELTA", "S_UNRELATED_STATE_DELTA", "S_ROLLBACK_UNCERTAIN", "S_CLEANUP_RESERVE_REACHED"), ("disposable_object_state", "router_state_projection"), ("phase_result", "counts", "default_policy_unchanged", "unrelated_state_unchanged")),
    "P7_OPTIONAL_DISPOSABLE_ASSIGNMENT_CANARY": (("S_AUTHORIZATION_MISSING", "S_REVISION_OR_PRECONDITION_MISSING", "S_VERIFICATION_MISMATCH", "S_DEFAULT_POLICY_DELTA", "S_UNRELATED_STATE_DELTA", "S_ROLLBACK_UNCERTAIN", "S_CLEANUP_RESERVE_REACHED"), ("disposable_object_state", "router_state_projection"), ("phase_result", "counts", "default_policy_unchanged", "unrelated_state_unchanged")),
    "P8_FULL_ROUTERKIT_INSTALL_CANARY": (("S_AUTHORIZATION_MISSING", "S_BACKUP_UNAVAILABLE", "S_VERIFICATION_MISMATCH", "S_DEFAULT_POLICY_DELTA", "S_UNRELATED_STATE_DELTA", "S_ROLLBACK_UNCERTAIN", "S_CLEANUP_RESERVE_REACHED"), ("setup_result", "router_state_projection"), ("phase_result", "loopback_only", "limitations", "egress_result", "default_policy_unchanged")),
    "P9_IDEMPOTENT_RERUN": (("S_VERIFICATION_MISMATCH", "S_DEFAULT_POLICY_DELTA", "S_UNRELATED_STATE_DELTA", "S_ROLLBACK_UNCERTAIN", "S_CLEANUP_RESERVE_REACHED"), ("setup_result", "router_state_projection"), ("phase_result", "default_policy_unchanged")),
    "P10_FAILURE_AND_ROLLBACK": (("S_VERIFICATION_MISMATCH", "S_DEFAULT_POLICY_DELTA", "S_UNRELATED_STATE_DELTA", "S_ROLLBACK_UNCERTAIN", "S_CLEANUP_RESERVE_REACHED"), ("failure_result", "router_state_projection"), ("phase_result", "rollback_result", "default_policy_unchanged")),
    "P11_REBOOT_AND_RECOVERY": (("S_AUTHORIZATION_MISSING", "S_VERIFICATION_MISMATCH", "S_DEFAULT_POLICY_DELTA", "S_UNRELATED_STATE_DELTA", "S_ROLLBACK_UNCERTAIN", "S_CLEANUP_RESERVE_REACHED"), ("reboot_state", "router_state_projection"), ("reboot_result", "loopback_only", "phase_result", "limitations", "default_policy_unchanged")),
    "P12_FINAL_INVARIANT_AUDIT": (("S_DEFAULT_POLICY_DELTA", "S_UNRELATED_STATE_DELTA", "S_VERIFICATION_MISMATCH", "S_ROLLBACK_UNCERTAIN", "S_CLEANUP_RESERVE_REACHED"), ("router_state_projection", "reboot_state", "cleanup_state", "failure_result"), ("default_policy_unchanged", "unrelated_state_unchanged", "loopback_only", "phase_result", "unsupported_rejected")),
    "P13_CLEANUP_AND_DEVICE_RETURN": (("S_ROLLBACK_UNCERTAIN",), ("cleanup_state",), ("cleanup_complete", "unrelated_state_unchanged", "device_returned")),
}

CANONICAL_CHECK_CONTRACTS = {
    "P0_BASELINE": ("P0_OPERATOR_PREFLIGHT", ("pass", "fail"), "high", "baseline_metadata", "baseline_verified"),
    "P0_AUTHORITY": ("P0_OPERATOR_PREFLIGHT", ("pass", "fail"), "critical", "operator_decision", "phase_result"),
    "P0_PRIVACY": ("P0_OPERATOR_PREFLIGHT", ("pass", "fail"), "high", "operator_decision", "phase_result"),
    "P1_TARGET": ("P1_READ_ONLY_PLATFORM_INVENTORY", ("pass", "partial", "fail"), "high", "target_inventory", "target_match"),
    "P1_PLATFORM": ("P1_READ_ONLY_PLATFORM_INVENTORY", ("pass", "partial", "fail"), "medium", "target_inventory", "phase_result"),
    "P1_MANAGEMENT": ("P1_READ_ONLY_PLATFORM_INVENTORY", ("pass", "partial", "fail"), "high", "interface_contract", "interface_category"),
    "P2_SCHEMAS": ("P2_READ_ONLY_DEVICE_DISCOVERY_CONTRACT", ("pass", "partial", "fail"), "high", "interface_contract", "interface_category"),
    "P2_JOIN": ("P2_READ_ONLY_DEVICE_DISCOVERY_CONTRACT", ("pass", "partial", "fail"), "high", "interface_contract", "phase_result"),
    "P2_UI": ("P2_READ_ONLY_DEVICE_DISCOVERY_CONTRACT", ("pass", "partial", "fail"), "medium", "interface_contract", "phase_result"),
    "P2_ERRORS": ("P2_READ_ONLY_DEVICE_DISCOVERY_CONTRACT", ("pass", "partial", "fail"), "medium", "interface_contract", "limitations"),
    "P3_INVENTORY": ("P3_READ_ONLY_POLICY_CONTRACT", ("pass", "partial", "fail"), "critical", "router_state_projection", "counts"),
    "P3_IDENTIFIERS": ("P3_READ_ONLY_POLICY_CONTRACT", ("pass", "partial", "fail"), "critical", "interface_contract", "interface_category"),
    "P3_BACKUP": ("P3_READ_ONLY_POLICY_CONTRACT", ("pass", "partial", "fail"), "critical", "backup_metadata", "rollback_result"),
    "P3_DEFAULT": ("P3_READ_ONLY_POLICY_CONTRACT", ("pass", "fail"), "critical", "router_state_projection", "default_policy_unchanged"),
    "P4_DECISION": ("P4_OFF_DEVICE_COMPATIBILITY_DECISION", ("pass", "partial", "fail"), "critical", "operator_decision", "phase_result"),
    "P4_PATCH_SCOPE": ("P4_OFF_DEVICE_COMPATIBILITY_DECISION", ("pass", "skip", "fail"), "high", "operator_decision", "limitations"),
    "P4_REENTRY": ("P4_OFF_DEVICE_COMPATIBILITY_DECISION", ("pass", "skip", "fail"), "critical", "operator_decision", "phase_result"),
    "P5_CREATE": ("P5_DISPOSABLE_CONNECTION_CANARY", ("pass", "fail"), "critical", "disposable_object_state", "phase_result"),
    "P5_READBACK": ("P5_DISPOSABLE_CONNECTION_CANARY", ("pass", "fail"), "critical", "disposable_object_state", "counts"),
    "P5_DEFAULT_POLICY_AUDIT": ("P5_DISPOSABLE_CONNECTION_CANARY", ("pass", "fail"), "critical", "router_state_projection", "default_policy_unchanged"),
    "P5_REMOVE": ("P5_DISPOSABLE_CONNECTION_CANARY", ("pass", "fail"), "critical", "disposable_object_state", "unrelated_state_unchanged"),
    "P6_CREATE": ("P6_DISPOSABLE_POLICY_CANARY", ("pass", "fail"), "critical", "disposable_object_state", "phase_result"),
    "P6_READBACK": ("P6_DISPOSABLE_POLICY_CANARY", ("pass", "fail"), "critical", "disposable_object_state", "counts"),
    "P6_DEFAULT_POLICY_AUDIT": ("P6_DISPOSABLE_POLICY_CANARY", ("pass", "fail"), "critical", "router_state_projection", "default_policy_unchanged"),
    "P6_REMOVE": ("P6_DISPOSABLE_POLICY_CANARY", ("pass", "fail"), "critical", "disposable_object_state", "unrelated_state_unchanged"),
    "P7_ASSIGN": ("P7_OPTIONAL_DISPOSABLE_ASSIGNMENT_CANARY", ("pass", "skip", "fail"), "critical", "disposable_object_state", "phase_result"),
    "P7_READBACK": ("P7_OPTIONAL_DISPOSABLE_ASSIGNMENT_CANARY", ("pass", "skip", "fail"), "critical", "disposable_object_state", "counts"),
    "P7_DEFAULT_POLICY_AUDIT": ("P7_OPTIONAL_DISPOSABLE_ASSIGNMENT_CANARY", ("pass", "skip", "fail"), "critical", "router_state_projection", "default_policy_unchanged"),
    "P7_RESTORE": ("P7_OPTIONAL_DISPOSABLE_ASSIGNMENT_CANARY", ("pass", "skip", "fail"), "critical", "disposable_object_state", "unrelated_state_unchanged"),
    "P8_PREREQUISITES": ("P8_FULL_ROUTERKIT_INSTALL_CANARY", ("pass", "fail"), "high", "setup_result", "phase_result"),
    "P8_CLEAN_PLAN": ("P8_FULL_ROUTERKIT_INSTALL_CANARY", ("pass", "fail"), "high", "setup_result", "phase_result"),
    "P8_RUNTIME": ("P8_FULL_ROUTERKIT_INSTALL_CANARY", ("pass", "fail"), "critical", "setup_result", "phase_result"),
    "P8_LISTENERS": ("P8_FULL_ROUTERKIT_INSTALL_CANARY", ("pass", "fail"), "critical", "setup_result", "loopback_only"),
    "P8_DISCOVERY_PLAN": ("P8_FULL_ROUTERKIT_INSTALL_CANARY", ("pass", "fail"), "high", "setup_result", "limitations"),
    "P8_EGRESS": ("P8_FULL_ROUTERKIT_INSTALL_CANARY", ("pass", "partial", "fail"), "high", "setup_result", "egress_result"),
    "P8_DEFAULT_POLICY_AUDIT": ("P8_FULL_ROUTERKIT_INSTALL_CANARY", ("pass", "fail"), "critical", "router_state_projection", "default_policy_unchanged"),
    "P9_RERUN": ("P9_IDEMPOTENT_RERUN", ("pass", "fail"), "high", "setup_result", "phase_result"),
    "P9_PROFILE_UPDATE": ("P9_IDEMPOTENT_RERUN", ("pass", "partial", "fail"), "high", "setup_result", "phase_result"),
    "P9_DEFAULT_POLICY_AUDIT": ("P9_IDEMPOTENT_RERUN", ("pass", "fail"), "critical", "router_state_projection", "default_policy_unchanged"),
    "P10_PLAN_FAILURE": ("P10_FAILURE_AND_ROLLBACK", ("pass", "fail"), "medium", "failure_result", "phase_result"),
    "P10_BOOTSTRAP_PRECONDITION_FAILURE": ("P10_FAILURE_AND_ROLLBACK", ("pass", "fail"), "high", "failure_result", "phase_result"),
    "P10_PREFLIGHT_FAILURE": ("P10_FAILURE_AND_ROLLBACK", ("pass", "fail"), "high", "failure_result", "phase_result"),
    "P10_BACKUP_GATE_FAILURE": ("P10_FAILURE_AND_ROLLBACK", ("pass", "fail"), "critical", "failure_result", "phase_result"),
    "P10_INSTALL_ROLLBACK": ("P10_FAILURE_AND_ROLLBACK", ("pass", "fail"), "critical", "failure_result", "rollback_result"),
    "P10_AUTOSTART_FAILURE": ("P10_FAILURE_AND_ROLLBACK", ("pass", "fail"), "critical", "failure_result", "rollback_result"),
    "P10_HEALTHCHECK_ROLLBACK": ("P10_FAILURE_AND_ROLLBACK", ("pass", "fail"), "critical", "failure_result", "rollback_result"),
    "P10_DISPOSABLE_WRITE_FAILURES": ("P10_FAILURE_AND_ROLLBACK", ("pass", "partial", "fail"), "critical", "failure_result", "rollback_result"),
    "P10_DEFAULT_POLICY_AUDIT": ("P10_FAILURE_AND_ROLLBACK", ("pass", "fail"), "critical", "router_state_projection", "default_policy_unchanged"),
    "P11_REBOOT": ("P11_REBOOT_AND_RECOVERY", ("pass", "fail"), "critical", "reboot_state", "reboot_result"),
    "P11_RUNTIME_RECOVERY": ("P11_REBOOT_AND_RECOVERY", ("pass", "partial", "fail"), "critical", "reboot_state", "loopback_only"),
    "P11_RERUN": ("P11_REBOOT_AND_RECOVERY", ("pass", "fail"), "high", "reboot_state", "phase_result"),
    "P11_USB_RECOVERY": ("P11_REBOOT_AND_RECOVERY", ("pass", "skip", "fail"), "critical", "reboot_state", "limitations"),
    "P11_DEFAULT_POLICY_AUDIT": ("P11_REBOOT_AND_RECOVERY", ("pass", "fail"), "critical", "router_state_projection", "default_policy_unchanged"),
    "P12_DEFAULT_POLICY_AUDIT": ("P12_FINAL_INVARIANT_AUDIT", ("pass", "fail"), "critical", "router_state_projection", "default_policy_unchanged"),
    "P12_UNRELATED_STATE": ("P12_FINAL_INVARIANT_AUDIT", ("pass", "fail"), "critical", "router_state_projection", "unrelated_state_unchanged"),
    "P12_RUNTIME_INVARIANTS": ("P12_FINAL_INVARIANT_AUDIT", ("pass", "fail"), "critical", "reboot_state", "loopback_only"),
    "P12_PRIVACY": ("P12_FINAL_INVARIANT_AUDIT", ("pass", "fail"), "high", "cleanup_state", "phase_result"),
    "P12_UNSUPPORTED_REJECTION": ("P12_FINAL_INVARIANT_AUDIT", ("pass", "partial", "fail"), "high", "failure_result", "unsupported_rejected"),
    "P13_REMOVE_DISPOSABLE": ("P13_CLEANUP_AND_DEVICE_RETURN", ("pass", "fail"), "critical", "cleanup_state", "cleanup_complete"),
    "P13_RESTORE_ASSIGNMENT": ("P13_CLEANUP_AND_DEVICE_RETURN", ("pass", "skip", "fail"), "critical", "cleanup_state", "cleanup_complete"),
    "P13_FINAL_STATE": ("P13_CLEANUP_AND_DEVICE_RETURN", ("pass", "fail"), "critical", "cleanup_state", "unrelated_state_unchanged"),
    "P13_PRIVATE_EVIDENCE": ("P13_CLEANUP_AND_DEVICE_RETURN", ("pass", "fail"), "high", "cleanup_state", "cleanup_complete"),
    "P13_DEVICE_RETURN": ("P13_CLEANUP_AND_DEVICE_RETURN", ("pass", "fail"), "critical", "cleanup_state", "device_returned"),
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
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_BINARY", 0)
    )
    fd: Optional[int] = None
    try:
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            raise PacketError("could not open packet") from exc
        try:
            metadata = os.fstat(fd)
        except OSError as exc:
            raise PacketError("could not inspect packet") from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise PacketError("packet must be a regular file")
        if metadata.st_size > MAX_PACKET_BYTES:
            raise PacketError("packet exceeds the 1 MiB limit")
        try:
            with os.fdopen(fd, "rb", closefd=True) as stream:
                fd = None
                raw = stream.read(MAX_PACKET_BYTES + 1)
        except OSError as exc:
            raise PacketError("could not read packet") from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
    if len(raw) > MAX_PACKET_BYTES:
        raise PacketError("packet exceeds the 1 MiB limit")
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
        or patch_reserve < 30
    ):
        _append(
            errors,
            "session_budget.patch_reentry_minimum_reserve_minutes",
            "must be an integer no smaller than 30 and the cleanup reserve",
        )
    return budget


def _validate_canonical_phase_and_check_contract(
    phases: Sequence[Mapping[str, Any]],
    errors: List[str],
) -> None:
    observed_checks: Set[str] = set()
    for index, phase in enumerate(phases):
        phase_id = phase.get("id")
        if phase_id not in CANONICAL_PHASE_CONTRACTS:
            continue
        (
            category,
            dependencies,
            estimate,
            timeout,
            optional,
            authorization,
            rollback_ids,
            check_ids,
        ) = CANONICAL_PHASE_CONTRACTS[phase_id]
        path = "phases[{}]".format(index)
        expected_fields = {
            "category": category,
            "dependencies": list(dependencies),
            "estimated_minutes": estimate,
            "hard_timeout_minutes": timeout,
            "optional": optional,
            "required_operator_authorization": authorization,
            "rollback_check_ids": list(rollback_ids),
        }
        stops, private_categories, public_fields = CANONICAL_PHASE_STOPS_AND_EVIDENCE[phase_id]
        expected_fields.update(
            {
                "stop_condition_ids": list(stops),
                "private_evidence_categories": list(private_categories),
                "public_evidence_fields": list(public_fields),
            }
        )
        for field, expected in expected_fields.items():
            if phase.get(field) != expected:
                _append(errors, "{}.{}".format(path, field), "does not match the canonical v1 contract")
        checks = phase.get("checks")
        if not isinstance(checks, list):
            continue
        if tuple(check.get("id") for check in checks if isinstance(check, dict)) != check_ids:
            _append(errors, "{}.checks".format(path), "must match the exact canonical v1 check inventory")
        for check_index, check in enumerate(checks):
            if not isinstance(check, dict):
                continue
            check_id = check.get("id")
            observed_checks.add(check_id)
            if check_id not in CANONICAL_CHECK_CONTRACTS:
                _append(errors, "{}.checks[{}].id".format(path, check_index), "is not a canonical v1 check")
                continue
            owner, outcomes, risk, evidence, public = CANONICAL_CHECK_CONTRACTS[check_id]
            expected = {
                "outcome_categories": list(outcomes),
                "risk_class": risk,
                "evidence_category": evidence,
                "public_field": public,
            }
            if owner != phase_id:
                _append(errors, "{}.checks[{}].id".format(path, check_index), "belongs to {}".format(owner))
            for field, expected_value in expected.items():
                if check.get(field) != expected_value:
                    _append(
                        errors,
                        "{}.checks[{}].{}".format(path, check_index, field),
                        "does not match the canonical v1 check contract",
                    )
    missing_checks = sorted(set(CANONICAL_CHECK_CONTRACTS) - observed_checks)
    extra_checks = sorted(check for check in observed_checks if check not in CANONICAL_CHECK_CONTRACTS)
    if missing_checks:
        _append(errors, "phases.checks", "missing canonical IDs: {}".format(", ".join(missing_checks)))
    if extra_checks:
        _append(errors, "phases.checks", "noncanonical IDs: {}".format(", ".join(extra_checks)))


def _validate_route_time_feasibility(
    phases: Sequence[Mapping[str, Any]],
    budget: Mapping[str, Any],
    errors: List[str],
) -> None:
    if not all(isinstance(budget.get(key), int) and not isinstance(budget.get(key), bool) for key in SESSION_BUDGET_KEYS):
        return
    phase_timeouts = {
        phase.get("id"): phase.get("hard_timeout_minutes")
        for phase in phases
        if isinstance(phase, dict)
    }
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in phase_timeouts.values()):
        return
    ceiling = budget["hard_session_ceiling_minutes"]
    reserve = budget["cleanup_reserve_minutes"]
    p13_timeout = phase_timeouts.get("P13_CLEANUP_AND_DEVICE_RETURN")
    if p13_timeout is None:
        return
    if p13_timeout < reserve:
        _append(errors, "P13_CLEANUP_AND_DEVICE_RETURN.hard_timeout_minutes", "must cover the cleanup reserve")
    for route_name, route in CANONICAL_SESSION_ROUTES:
        try:
            route_timeout = sum(phase_timeouts[phase_id] for phase_id in route)
        except KeyError:
            continue
        pre_cleanup = route_timeout - p13_timeout
        if pre_cleanup > ceiling - reserve:
            _append(errors, "session route {}".format(route_name), "starts work inside the protected cleanup reserve")
        if pre_cleanup + p13_timeout > ceiling:
            _append(errors, "session route {}".format(route_name), "exceeds the hard session ceiling")


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
        _validate_route_time_feasibility(phases, budget, errors)
    _validate_canonical_phase_and_check_contract(phases, errors)


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
    budget = packet.get("session_budget")
    if isinstance(budget, dict) and cleanup.get("minimum_reserve_minutes") != budget.get("cleanup_reserve_minutes"):
        _append(errors, "cleanup_contract.minimum_reserve_minutes", "must equal the session cleanup reserve")
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
        if tuple(cleanup["required_check_ids"]) != REQUIRED_CLEANUP_CHECK_IDS:
            _append(errors, "cleanup_contract.required_check_ids", "must match the exact P13 cleanup contract")
        non_p13 = sorted(check_id for check_id in cleanup["required_check_ids"] if CANONICAL_CHECK_CONTRACTS.get(check_id, ("",))[0] != "P13_CLEANUP_AND_DEVICE_RETURN")
        if non_p13:
            _append(errors, "cleanup_contract.required_check_ids", "must all belong to P13")
    if not _is_string_list(cleanup["final_outcomes"]):
        _append(errors, "cleanup_contract.final_outcomes", "must be a non-empty string array")
    elif tuple(cleanup["final_outcomes"]) != REQUIRED_FINAL_OUTCOME_ORDER:
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


def _parse_rfc3339(value: Any, path: str, errors: List[str]) -> Optional[_dt.datetime]:
    if not _is_nonempty_string(value):
        _append(errors, path, "must be a timezone-aware date-time string")
        return None
    try:
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        _append(errors, path, "must be a valid RFC3339 date-time")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _append(errors, path, "must include a timezone offset")
        return None
    return parsed


def _validate_manifest_provenance(manifest: Mapping[str, Any], errors: List[str]) -> None:
    if manifest["baseline_commit"] != EXPECTED_MAIN:
        _append(errors, "manifest.baseline_commit", "must equal the released baseline commit")
    if manifest["execution_source"] != "released_baseline":
        _append(errors, "manifest.execution_source", "schema v1 only permits released_baseline execution")
    if manifest["execution_commit"] != EXPECTED_MAIN:
        _append(errors, "manifest.execution_commit", "must equal the released baseline commit in schema v1")
    if manifest["compatibility_patch"] is not None:
        _append(errors, "manifest.compatibility_patch", "requires a future schema version")


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
        "baseline_commit",
        "execution_commit",
        "execution_source",
        "compatibility_patch",
        "started_at",
        "ended_at",
        "expected_target",
        "observed_target",
        "phases",
        "artifacts",
        "cleanup_status",
        "manual_recovery_required",
        "final_outcome",
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
    for key in ("baseline_commit", "execution_commit"):
        if not isinstance(manifest[key], str) or re.fullmatch(r"[0-9a-f]{40}", manifest[key]) is None:
            _append(errors, "manifest.{}".format(key), "must be a lowercase 40-hex commit")
    _validate_manifest_provenance(manifest, errors)
    if not isinstance(manifest["session_id"], str) or re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", manifest["session_id"]
    ) is None:
        _append(errors, "manifest.session_id", "has an invalid format")
    session_started = _parse_rfc3339(manifest["started_at"], "manifest.started_at", errors)
    session_ended = None
    if manifest["ended_at"] is not None:
        session_ended = _parse_rfc3339(manifest["ended_at"], "manifest.ended_at", errors)
    if session_started is not None and session_ended is not None and session_ended < session_started:
        _append(errors, "manifest.ended_at", "must not be earlier than started_at")

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
    check_outcomes = {
        check_id: set(contract[1])
        for check_id, contract in CANONICAL_CHECK_CONTRACTS.items()
    }
    phase_keys = {
        "phase_id",
        "started_at",
        "ended_at",
        "outcome",
        "decision",
        "checks",
        "notes_category",
    }
    phases = manifest["phases"]
    phase_ids: List[str] = []
    recorded_checks: Set[Tuple[str, str]] = set()
    phase_outcomes: Dict[str, str] = {}
    phase_decisions: Dict[str, Optional[str]] = {}
    previous_phase_end: Optional[_dt.datetime] = None
    seen_p13 = False
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
                expected_index = PHASE_IDS.index(phase_id)
                if expected_index != index:
                    _append(errors, "{}.phase_id".format(path), "must appear in canonical P0-P13 order")
                if seen_p13 and phase_id != "P13_CLEANUP_AND_DEVICE_RETURN":
                    _append(errors, "{}.phase_id".format(path), "cannot appear after P13 cleanup")
                if phase_id == "P13_CLEANUP_AND_DEVICE_RETURN":
                    seen_p13 = True
                checks = phase["checks"]
                if not isinstance(checks, list):
                    _append(errors, "{}.checks".format(path), "must be an array")
                else:
                    check_ids = []
                    check_results: Dict[str, str] = {}
                    for check_index, check in enumerate(checks):
                        check_path = "{}.checks[{}]".format(path, check_index)
                        if not _check_exact_keys(check, {"check_id", "outcome"}, check_path, errors):
                            continue
                        check_id = check["check_id"]
                        outcome = check["outcome"]
                        check_ids.append(check_id)
                        if check_id not in packet_checks[phase_id]:
                            _append(errors, "{}.check_id".format(check_path), "does not belong to the phase")
                            continue
                        if outcome not in check_outcomes.get(check_id, set()):
                            _append(errors, "{}.outcome".format(check_path), "is not allowed for the check")
                        check_results[check_id] = outcome
                        recorded_checks.add((phase_id, check_id))
                    duplicate_checks = _duplicates(check_ids)
                    if duplicate_checks:
                        _append(errors, "{}.checks".format(path), "contains duplicate IDs")
                    if phase["outcome"] == "pass":
                        missing_passes = sorted(
                            check_id
                            for check_id in packet_checks[phase_id]
                            if check_results.get(check_id) != "pass"
                        )
                        if missing_passes:
                            _append(errors, "{}.checks".format(path), "phase pass requires every required check to pass")
                    if phase["outcome"] == "partial" and not checks:
                        _append(errors, "{}.checks".format(path), "phase partial requires at least one check")
                    if phase["outcome"] == "fail" and not checks:
                        _append(errors, "{}.checks".format(path), "phase fail requires at least one attempted check")
                    if phase["outcome"] == "skip" and phase_id != "P7_OPTIONAL_DISPOSABLE_ASSIGNMENT_CANARY":
                        _append(
                            errors,
                            "{}.outcome".format(path),
                            "only optional P7 may be skipped",
                        )
            if phase["outcome"] not in {"not_started", "pass", "partial", "fail", "skip"}:
                _append(errors, "{}.outcome".format(path), "invalid outcome")
            else:
                phase_outcomes[phase_id] = phase["outcome"]
            decision = phase["decision"]
            if phase_id == "P4_OFF_DEVICE_COMPATIBILITY_DECISION":
                if decision not in P4_DECISIONS:
                    _append(errors, "{}.decision".format(path), "must be an exact P4 decision")
                elif (
                    (phase["outcome"] == "pass" and decision != "GO_WITH_EXISTING_ALPHA16_CONTRACT")
                    or (phase["outcome"] == "partial" and decision != "OFF_DEVICE_NARROW_PATCH_REQUIRED")
                    or (phase["outcome"] == "fail" and decision != "STOP_UNSUPPORTED_OR_AMBIGUOUS")
                ):
                    _append(errors, "{}.decision".format(path), "does not match the P4 outcome")
            elif decision is not None:
                _append(errors, "{}.decision".format(path), "is only allowed for P4")
            phase_decisions[phase_id] = decision
            phase_started = _parse_rfc3339(phase["started_at"], "{}.started_at".format(path), errors)
            phase_ended = None
            if phase["ended_at"] is not None:
                phase_ended = _parse_rfc3339(phase["ended_at"], "{}.ended_at".format(path), errors)
            if phase_started and phase_ended and phase_ended < phase_started:
                _append(errors, "{}.ended_at".format(path), "must not be earlier than started_at")
            if session_started and phase_started and phase_started < session_started:
                _append(errors, "{}.started_at".format(path), "must be within the session")
            if session_ended and phase_ended and phase_ended > session_ended:
                _append(errors, "{}.ended_at".format(path), "must be within the session")
            if previous_phase_end and phase_started and phase_started < previous_phase_end:
                _append(errors, "{}.started_at".format(path), "overlaps the previous phase")
            if phase_ended:
                previous_phase_end = phase_ended
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
        if tuple(phase_ids) != tuple(sorted(phase_ids, key=PHASE_IDS.index)):
            _append(errors, "manifest.phases", "must be in canonical P0-P13 order")

    def phase_passed(phase_id: str) -> bool:
        return phase_outcomes.get(phase_id) == "pass"

    predecessor_rules = (
        ("P1_READ_ONLY_PLATFORM_INVENTORY", ("P0_OPERATOR_PREFLIGHT",)),
        ("P2_READ_ONLY_DEVICE_DISCOVERY_CONTRACT", ("P1_READ_ONLY_PLATFORM_INVENTORY",)),
        ("P3_READ_ONLY_POLICY_CONTRACT", ("P2_READ_ONLY_DEVICE_DISCOVERY_CONTRACT",)),
        ("P4_OFF_DEVICE_COMPATIBILITY_DECISION", ("P3_READ_ONLY_POLICY_CONTRACT",)),
        ("P6_DISPOSABLE_POLICY_CANARY", ("P5_DISPOSABLE_CONNECTION_CANARY",)),
        ("P9_IDEMPOTENT_RERUN", ("P8_FULL_ROUTERKIT_INSTALL_CANARY",)),
        ("P10_FAILURE_AND_ROLLBACK", ("P9_IDEMPOTENT_RERUN",)),
        ("P11_REBOOT_AND_RECOVERY", ("P10_FAILURE_AND_ROLLBACK",)),
        ("P12_FINAL_INVARIANT_AUDIT", ("P11_REBOOT_AND_RECOVERY",)),
    )
    for phase_id, required in predecessor_rules:
        if phase_id in phase_outcomes and not all(phase_passed(item) for item in required):
            _append(errors, "manifest.phases.{}".format(phase_id), "requires predecessor phases to pass")
    if "P5_DISPOSABLE_CONNECTION_CANARY" in phase_outcomes:
        if not all(phase_passed(item) for item in PHASE_IDS[:4]):
            _append(errors, "manifest.phases.P5_DISPOSABLE_CONNECTION_CANARY", "requires P0-P3 pass")
        if phase_decisions.get("P4_OFF_DEVICE_COMPATIBILITY_DECISION") != "GO_WITH_EXISTING_ALPHA16_CONTRACT":
            _append(errors, "manifest.phases.P5_DISPOSABLE_CONNECTION_CANARY", "requires explicit P4 GO decision")
    if "P7_OPTIONAL_DISPOSABLE_ASSIGNMENT_CANARY" in phase_outcomes and not phase_passed("P6_DISPOSABLE_POLICY_CANARY"):
        _append(errors, "manifest.phases.P7_OPTIONAL_DISPOSABLE_ASSIGNMENT_CANARY", "requires P6 pass")
    if "P8_FULL_ROUTERKIT_INSTALL_CANARY" in phase_outcomes:
        if not phase_passed("P6_DISPOSABLE_POLICY_CANARY"):
            _append(errors, "manifest.phases.P8_FULL_ROUTERKIT_INSTALL_CANARY", "requires P6 pass")
        p7 = phase_outcomes.get("P7_OPTIONAL_DISPOSABLE_ASSIGNMENT_CANARY")
        if p7 not in (None, "skip", "pass"):
            _append(errors, "manifest.phases.P8_FULL_ROUTERKIT_INSTALL_CANARY", "requires P7 absent, skipped, or passed")

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
            elif (phase_id, check_id) not in recorded_checks:
                _append(errors, "{}.check_id".format(path), "must reference an executed manifest check")
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
            if artifact["sensitivity_class"] != "public_safe" and artifact["redaction_status"] == "sanitized":
                _append(errors, "{}.redaction_status".format(path), "private artifacts cannot become public by redaction flag")
            if manifest["retention_decision"] == "securely_disposed" and artifact["retention_decision"] != "securely_disposed":
                _append(errors, "{}.retention_decision".format(path), "conflicts with session secure disposal")
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
    if not isinstance(manifest["manual_recovery_required"], bool):
        _append(errors, "manifest.manual_recovery_required", "must be boolean")
    final_outcome = manifest["final_outcome"]
    if final_outcome is not None and final_outcome not in REQUIRED_FINAL_OUTCOMES:
        _append(errors, "manifest.final_outcome", "invalid final outcome")
    if manifest["ended_at"] is None and final_outcome is not None:
        _append(errors, "manifest.final_outcome", "must be null for active sessions")
    if manifest["ended_at"] is not None and final_outcome is None:
        _append(errors, "manifest.final_outcome", "is required for ended sessions")

    p13_pass = phase_outcomes.get("P13_CLEANUP_AND_DEVICE_RETURN") == "pass"
    p13_checks = {
        check_id
        for phase_id, check_id in recorded_checks
        if phase_id == "P13_CLEANUP_AND_DEVICE_RETURN"
    }
    if manifest["cleanup_status"] == "complete":
        if not p13_pass:
            _append(errors, "manifest.cleanup_status", "complete requires P13 pass")
        missing_cleanup = sorted(set(REQUIRED_CLEANUP_CHECK_IDS) - p13_checks)
        if missing_cleanup:
            _append(errors, "manifest.cleanup_status", "complete requires every cleanup check")
        if manifest.get("manual_recovery_required") is True:
            _append(errors, "manifest.cleanup_status", "complete conflicts with manual recovery")
    if manifest["cleanup_status"] == "not_started" and "P13_CLEANUP_AND_DEVICE_RETURN" in phase_outcomes:
        _append(errors, "manifest.cleanup_status", "not_started conflicts with P13 evidence")
    if manifest["cleanup_status"] == "in_progress" and p13_pass:
        _append(errors, "manifest.cleanup_status", "in_progress conflicts with P13 pass")
    if manifest["cleanup_status"] == "manual_recovery_required" and manifest.get("manual_recovery_required") is not True:
        _append(errors, "manifest.manual_recovery_required", "must be true for manual recovery cleanup")
    if final_outcome in PASS_FINAL_OUTCOMES:
        if not phase_passed("P12_FINAL_INVARIANT_AUDIT") or not p13_pass:
            _append(errors, "manifest.final_outcome", "PASS requires P12 and P13 pass")
        if manifest.get("manual_recovery_required") is True:
            _append(errors, "manifest.final_outcome", "PASS conflicts with manual recovery")
    if final_outcome in FAILURE_FINAL_OUTCOMES and manifest["cleanup_status"] != "complete" and manifest.get("manual_recovery_required") is not True:
        _append(errors, "manifest.final_outcome", "failure outcomes require cleanup complete or manual recovery")
    return sorted(set(errors))


def _structural_repository_contract(
    packet: Mapping[str, Any],
    repo_root: Path,
    canonical_repository_packet: bool = True,
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
        "canonical_repository_packet": canonical_repository_packet,
        "referenced_paths_present": referenced_paths_present,
        "phase_contract_present": True,
        "check_contract_present": True,
        "stop_routes_present": True,
        "time_feasibility_present": True,
        "cleanup_contract_present": True,
        "read_contract_present": True,
        "write_contract_present": True,
        "rollback_contract_present": True,
        "full_matrix_present": True,
        "evidence_contract_present": True,
        "docs_checklist_contract_present": referenced_paths_present,
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
        "canonical_repository_packet",
        "referenced_paths_present",
        "phase_contract_present",
        "check_contract_present",
        "stop_routes_present",
        "time_feasibility_present",
        "cleanup_contract_present",
        "read_contract_present",
        "write_contract_present",
        "rollback_contract_present",
        "full_matrix_present",
        "evidence_contract_present",
        "docs_checklist_contract_present",
        "static_guard_contract_present",
        "test_contract_present",
        "review_gate_present",
    }
    if set(repository_contract) != required_keys:
        return CHANGES_REQUIRED
    if repository_contract["canonical_repository_packet"] is not True:
        return CHANGES_REQUIRED
    if not repository_contract["referenced_paths_present"]:
        return BLOCKED
    if repository_contract["baseline_release"] != RELEASED_BASELINE:
        return CHANGES_REQUIRED
    if repository_contract["baseline_commit"] != EXPECTED_MAIN:
        return CHANGES_REQUIRED
    for key in (
        "phase_contract_present",
        "check_contract_present",
        "stop_routes_present",
        "time_feasibility_present",
        "cleanup_contract_present",
        "read_contract_present",
        "write_contract_present",
        "rollback_contract_present",
        "full_matrix_present",
        "evidence_contract_present",
        "docs_checklist_contract_present",
        "static_guard_contract_present",
        "test_contract_present",
        "review_gate_present",
    ):
        if repository_contract[key] is not True:
            return CHANGES_REQUIRED
    return READY


def render_checklist(packet: Mapping[str, Any], verdict: str = CHANGES_REQUIRED) -> str:
    budget = packet["session_budget"]
    lines = [
        "# RouterKit Netcraze hardware-canary checklist",
        "",
        "Offline packet verdict: {}".format(verdict),
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
                phase_id=row["phase_id"],
                category=row["category"],
                estimated_minutes=row["estimated_minutes"],
                hard_timeout_minutes=row["hard_timeout_minutes"],
                optional=row["optional"],
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


def _select_packet_from_request(args: argparse.Namespace) -> Tuple[Path, bool]:
    if args.packet is None:
        return default_packet_path(), True
    return Path(args.packet), False


def run_cli(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        _validate_args(args)
        if args.mode == "status":
            _print_status(args.json)
            return 0

        root = repository_root()
        packet_path, canonical_repository_packet = _select_packet_from_request(args)
        packet = load_packet(packet_path)
        errors = validate_packet(packet, root)
        evidence_path = root / packet.get("evidence_contract", {}).get(
            "private_manifest_schema", "missing"
        )
        errors.extend(validate_private_manifest_schema(evidence_path))
        errors = sorted(set(errors))
        repository_contract = _structural_repository_contract(
            packet,
            root,
            canonical_repository_packet=canonical_repository_packet,
        )
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
            sys.stdout.write(render_checklist(packet, verdict))
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
