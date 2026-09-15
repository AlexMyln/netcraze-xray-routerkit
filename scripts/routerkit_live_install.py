#!/usr/bin/env python3
"""First-class RouterKit live-install state machine.

The orchestrator owns ordering, bounded handoffs, state epochs, and a
secret-safe receipt.  Existing RouterKit tools continue to own bootstrap,
profile acquisition, generation, strict planning, install/rollback,
healthcheck, autostart, local routing reconciliation, and native Netcraze
planning/apply/verification semantics.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import routerkit_netcraze_external as external
import routerkit_netcraze_live as live
from routerkit_devices import DeviceDiscoveryError, normalize_trusted_device_mac
from routerkit_netcraze_plan import NetcrazePlanError, load_local_endpoint_manifest
from routerkit_private_io import (
    PrivateFileError,
    ensure_private_directory,
    read_owner_only_text_file,
    write_private_bytes_atomic,
)
from routerkit_profile_source import PayloadValidationError, validate_env_name


RECEIPT_SCHEMA = "routerkit.live-install.v1"
EVIDENCE_SCHEMA = "routerkit.live-install.evidence.v1"
SUPPORTED_TRANSPORTS = ("local-ndmc", "external")
DEFAULT_HARDWARE_CONTRACT = live.SUPPORTED_CONTRACT
MAX_RECEIPT_BYTES = 128 * 1024
MAX_EVIDENCE_BYTES = 128 * 1024
ROLLBACK_UNPROVEN = 3
HANDOFF_REQUIRED = 4

PENDING = "PENDING"
PASS = "PASS"
FAIL = "FAIL"
SKIPPED = "SKIPPED"

STAGE_ORDER = (
    "network_preflight",
    "usb_ext4_entware_readiness",
    "pinned_xray_bootstrap",
    "protected_profile_source",
    "generate",
    "strict_plan",
    "backup",
    "install",
    "healthcheck",
    "autostart",
    "native_proxy_component",
    "controlled_reboot",
    "post_reboot_proof",
    "native_proxy_policy_transaction",
    "protected_dns_gate",
    "selected_client_acceptance",
    "final_result",
)

INSTALLATION_STAGES = (
    "network_preflight",
    "usb_ext4_entware_readiness",
    "pinned_xray_bootstrap",
    "protected_profile_source",
    "generate",
    "strict_plan",
    "backup",
    "install",
    "healthcheck",
    "autostart",
)

POST_REBOOT_CHECKS = (
    "management",
    "wan_pppoe",
    "lan",
    "wifi",
    "usb_ext4_opt",
    "entware",
    "xray",
    "loopback_listeners",
)

EVIDENCE_CHECKS = POST_REBOOT_CHECKS + ("proxy_component",)
SAFE_INTERFACE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
PROHIBITED_TEXT_MARKERS = (
    "vless://",
    "private_key",
    "publickey",
    "shortid",
    "subscription",
    "password",
    "running-config",
    "startup-config",
)


class LiveInstallError(Exception):
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class LiveInstallHandoff(LiveInstallError):
    def __init__(self, action: str, expectations: Sequence[str]) -> None:
        super().__init__(action, HANDOFF_REQUIRED)
        self.action = action
        self.expectations = tuple(expectations)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _path_fingerprint(path: Path) -> str:
    return hashlib.sha256(str(Path(path).resolve()).encode("utf-8")).hexdigest()


def _strict_keys(value: Mapping[str, Any], expected: Sequence[str], label: str) -> None:
    if set(value) != set(expected):
        raise LiveInstallError("%s contains unsupported or missing fields." % label, 2)


def _require_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or not HASH_RE.fullmatch(value):
        raise LiveInstallError("%s is invalid." % label, 2)
    return value


def _load_bootstrap_module(repo_root: Path):
    path = Path(repo_root) / "scripts" / "routerkit-bootstrap.py"
    spec = importlib.util.spec_from_file_location("routerkit_live_install_bootstrap", path)
    if spec is None or spec.loader is None:
        raise LiveInstallError("Bootstrap module could not be loaded.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def artifact_identity(repo_root: Path, manifest_path: Optional[Path] = None) -> Dict[str, str]:
    """Reuse the bootstrap manifest validator/resolver for receipt identity."""

    module = _load_bootstrap_module(repo_root)
    selected = manifest_path or (Path(repo_root) / "manifests" / "xray-artifacts.json")
    try:
        manifest = module.load_manifest(Path(selected))
        artifact_key, artifact = module.resolve_artifact(manifest, "Linux", "aarch64")
    except module.BootstrapError as exc:
        raise LiveInstallError("Pinned Xray artifact identity is invalid: %s" % exc, 2) from None
    return {
        "artifact_key": artifact_key,
        "release": str(manifest["upstream"]["release_tag"]),
        "archive_sha256": str(artifact["sha256"]),
    }


def selected_device_fingerprint(mac: str) -> str:
    try:
        normalized = normalize_trusted_device_mac(mac)
    except DeviceDiscoveryError:
        raise LiveInstallError("Selected device MAC is invalid or unsafe.", 2) from None
    return hashlib.sha256(("routerkit-selected-device-v1\0" + normalized).encode("ascii")).hexdigest()


def build_intent(
    *,
    hardware_contract: str,
    transport_mode: str,
    artifact: Mapping[str, str],
    selected_device_mac: str,
    profile_slot: int,
    move_device_authorized: bool = False,
    controlled_reboot_authorized: bool = False,
) -> Dict[str, Any]:
    if hardware_contract != DEFAULT_HARDWARE_CONTRACT:
        raise LiveInstallError("Unsupported live-install hardware contract.", 2)
    if transport_mode not in SUPPORTED_TRANSPORTS:
        raise LiveInstallError("Unsupported live-install transport mode.", 2)
    if type(profile_slot) is not int or profile_slot not in (1, 2, 3):
        raise LiveInstallError("Selected profile slot must be 1, 2, or 3.", 2)
    return {
        "hardware_contract": hardware_contract,
        "transport_mode": transport_mode,
        "artifact": dict(artifact),
        "selected_device_fingerprint": selected_device_fingerprint(selected_device_mac),
        "selected_profile_slot": profile_slot,
        "move_device_authorized": bool(move_device_authorized),
        "controlled_reboot_authorized": bool(controlled_reboot_authorized),
    }


def initial_receipt(intent: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "schema": RECEIPT_SCHEMA,
        "intent_fingerprint": _fingerprint(intent),
        "hardware_contract": intent["hardware_contract"],
        "transport_mode": intent["transport_mode"],
        "artifact": dict(intent["artifact"]),
        "selected_device_fingerprint": intent["selected_device_fingerprint"],
        "selected_profile_slot": intent["selected_profile_slot"],
        "move_device_authorized": intent["move_device_authorized"],
        "controlled_reboot_authorized": intent["controlled_reboot_authorized"],
        "endpoint_manifest_fingerprint": None,
        "state_epoch": 0,
        "discovery": {"epoch": None, "evidence_fingerprint": None},
        "reboot_required": None,
        "completed_reboot_epoch": None,
        "native_transaction": {
            "fingerprint": None,
            "status": PENDING,
            "write_required": None,
            "save_required": None,
            "pre_epoch": None,
            "dns_refresh_required": False,
            "pre_snapshot_path_fingerprint": None,
            "running_snapshot_path_fingerprint": None,
        },
        "dns_gate_status": PENDING,
        "final_verification": {
            "installation": PENDING,
            "xray": PENDING,
            "autostart": PENDING,
            "native_routing": PENDING,
            "dns": PENDING,
            "client_domain_https": PENDING,
        },
        "stages": {
            stage: {"status": PENDING, "epoch": None} for stage in STAGE_ORDER
        },
    }


def _validate_stage_records(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != set(STAGE_ORDER):
        raise LiveInstallError("Live-install receipt stage set is incompatible.", 2)
    for stage in STAGE_ORDER:
        record = value[stage]
        if not isinstance(record, dict):
            raise LiveInstallError("Live-install receipt stage record is malformed.", 2)
        _strict_keys(record, ("status", "epoch"), "Live-install receipt stage")
        if record["status"] not in (PENDING, PASS, FAIL, SKIPPED):
            raise LiveInstallError("Live-install receipt stage status is unsupported.", 2)
        if record["epoch"] is not None and (
            type(record["epoch"]) is not int or record["epoch"] < 0
        ):
            raise LiveInstallError("Live-install receipt stage epoch is invalid.", 2)


def validate_receipt(receipt: Any) -> Dict[str, Any]:
    if not isinstance(receipt, dict):
        raise LiveInstallError("Live-install receipt is malformed.", 2)
    expected = (
        "schema",
        "intent_fingerprint",
        "hardware_contract",
        "transport_mode",
        "artifact",
        "selected_device_fingerprint",
        "selected_profile_slot",
        "move_device_authorized",
        "controlled_reboot_authorized",
        "endpoint_manifest_fingerprint",
        "state_epoch",
        "discovery",
        "reboot_required",
        "completed_reboot_epoch",
        "native_transaction",
        "dns_gate_status",
        "final_verification",
        "stages",
    )
    _strict_keys(receipt, expected, "Live-install receipt")
    if receipt["schema"] != RECEIPT_SCHEMA:
        raise LiveInstallError("Live-install receipt schema is unsupported.", 2)
    _require_hash(receipt["intent_fingerprint"], "Intent fingerprint")
    _require_hash(receipt["selected_device_fingerprint"], "Selected-device fingerprint")
    endpoint = receipt["endpoint_manifest_fingerprint"]
    if endpoint is not None:
        _require_hash(endpoint, "Endpoint-manifest fingerprint")
    if receipt["hardware_contract"] != DEFAULT_HARDWARE_CONTRACT:
        raise LiveInstallError("Live-install receipt hardware contract is unsupported.", 2)
    if receipt["transport_mode"] not in SUPPORTED_TRANSPORTS:
        raise LiveInstallError("Live-install receipt transport mode is unsupported.", 2)
    if receipt["selected_profile_slot"] not in (1, 2, 3):
        raise LiveInstallError("Live-install receipt profile slot is invalid.", 2)
    if type(receipt["move_device_authorized"]) is not bool or type(
        receipt["controlled_reboot_authorized"]
    ) is not bool:
        raise LiveInstallError("Live-install receipt authorization scope is malformed.", 2)
    if type(receipt["state_epoch"]) is not int or receipt["state_epoch"] < 0:
        raise LiveInstallError("Live-install receipt state epoch is invalid.", 2)
    artifact = receipt["artifact"]
    if not isinstance(artifact, dict):
        raise LiveInstallError("Live-install receipt artifact identity is malformed.", 2)
    _strict_keys(artifact, ("artifact_key", "release", "archive_sha256"), "Artifact identity")
    _require_hash(artifact["archive_sha256"], "Artifact archive fingerprint")
    if not all(isinstance(artifact[name], str) and artifact[name] for name in artifact):
        raise LiveInstallError("Live-install receipt artifact identity is malformed.", 2)
    discovery = receipt["discovery"]
    if not isinstance(discovery, dict):
        raise LiveInstallError("Live-install receipt discovery state is malformed.", 2)
    _strict_keys(discovery, ("epoch", "evidence_fingerprint"), "Discovery state")
    if (discovery["epoch"] is None) != (discovery["evidence_fingerprint"] is None):
        raise LiveInstallError("Live-install receipt discovery state is inconsistent.", 2)
    if discovery["epoch"] is not None:
        if type(discovery["epoch"]) is not int or discovery["epoch"] < 0:
            raise LiveInstallError("Live-install receipt discovery epoch is invalid.", 2)
        _require_hash(discovery["evidence_fingerprint"], "Discovery evidence fingerprint")
    reboot_epoch = receipt["completed_reboot_epoch"]
    if receipt["reboot_required"] is not None and type(receipt["reboot_required"]) is not bool:
        raise LiveInstallError("Reboot-required receipt flag is invalid.", 2)
    if reboot_epoch is not None and (type(reboot_epoch) is not int or reboot_epoch < 1):
        raise LiveInstallError("Completed reboot epoch is invalid.", 2)
    native = receipt["native_transaction"]
    if not isinstance(native, dict):
        raise LiveInstallError("Live-install native transaction state is malformed.", 2)
    _strict_keys(
        native,
        (
            "fingerprint",
            "status",
            "write_required",
            "save_required",
            "pre_epoch",
            "dns_refresh_required",
            "pre_snapshot_path_fingerprint",
            "running_snapshot_path_fingerprint",
        ),
        "Native transaction state",
    )
    if native["fingerprint"] is not None:
        _require_hash(native["fingerprint"], "Native transaction fingerprint")
    if native["status"] not in (PENDING, "PLANNED", "RUNNING_VERIFIED", PASS):
        raise LiveInstallError("Native transaction receipt status is unsupported.", 2)
    for name in ("write_required", "save_required"):
        if native[name] is not None and type(native[name]) is not bool:
            raise LiveInstallError("Native transaction write gates are malformed.", 2)
    if native["pre_epoch"] is not None and (
        type(native["pre_epoch"]) is not int or native["pre_epoch"] < 0
    ):
        raise LiveInstallError("Native transaction pre-epoch is invalid.", 2)
    if type(native["dns_refresh_required"]) is not bool:
        raise LiveInstallError("Native DNS refresh gate is malformed.", 2)
    for name in ("pre_snapshot_path_fingerprint", "running_snapshot_path_fingerprint"):
        if native[name] is not None:
            _require_hash(native[name], "Native snapshot path fingerprint")
    if receipt["dns_gate_status"] not in (PENDING, PASS):
        raise LiveInstallError("DNS gate receipt status is unsupported.", 2)
    final = receipt["final_verification"]
    if not isinstance(final, dict):
        raise LiveInstallError("Final verification receipt is malformed.", 2)
    final_keys = (
        "installation",
        "xray",
        "autostart",
        "native_routing",
        "dns",
        "client_domain_https",
    )
    _strict_keys(final, final_keys, "Final verification")
    if any(final[name] not in (PENDING, PASS) for name in final_keys):
        raise LiveInstallError("Final verification status is unsupported.", 2)
    _validate_stage_records(receipt["stages"])
    incomplete_seen = False
    for stage in STAGE_ORDER:
        status = receipt["stages"][stage]["status"]
        if status in (PASS, SKIPPED):
            if incomplete_seen:
                raise LiveInstallError("Live-install receipt stage order is inconsistent.", 2)
        else:
            incomplete_seen = True
    if stage_done(receipt, "strict_plan") and receipt["endpoint_manifest_fingerprint"] is None:
        raise LiveInstallError("Live-install receipt is missing endpoint-manifest identity.", 2)
    component_status = receipt["stages"]["native_proxy_component"]["status"]
    if component_status == PASS and receipt["reboot_required"] is None:
        raise LiveInstallError("Live-install receipt is missing the component reboot decision.", 2)
    reboot_status = receipt["stages"]["controlled_reboot"]["status"]
    post_reboot_status = receipt["stages"]["post_reboot_proof"]["status"]
    if reboot_status == SKIPPED and post_reboot_status not in (PENDING, SKIPPED):
        raise LiveInstallError("Live-install skipped-reboot state is inconsistent.", 2)
    if reboot_status == PASS and receipt["completed_reboot_epoch"] is None:
        raise LiveInstallError("Live-install receipt is missing its completed reboot epoch.", 2)
    native_stage = receipt["stages"]["native_proxy_policy_transaction"]["status"]
    if (native_stage == PASS) != (native["status"] == PASS):
        raise LiveInstallError("Live-install native transaction state is inconsistent.", 2)
    dns_stage = receipt["stages"]["protected_dns_gate"]["status"]
    if (dns_stage == PASS) != (receipt["dns_gate_status"] == PASS):
        raise LiveInstallError("Live-install DNS state is inconsistent.", 2)
    expected_final = {
        "installation": PASS if all(stage_done(receipt, item) for item in INSTALLATION_STAGES) else PENDING,
        "xray": PASS if stage_done(receipt, "healthcheck") else PENDING,
        "autostart": PASS if stage_done(receipt, "autostart") else PENDING,
        "native_routing": PASS if native_stage == PASS else PENDING,
        "dns": PASS if dns_stage == PASS else PENDING,
        "client_domain_https": PASS
        if receipt["stages"]["selected_client_acceptance"]["status"] == PASS
        else PENDING,
    }
    if final != expected_final:
        raise LiveInstallError("Live-install final verification state is inconsistent.", 2)
    if receipt["stages"]["final_result"]["status"] == PASS and any(
        value != PASS for value in final.values()
    ):
        raise LiveInstallError("Live-install final PASS is inconsistent.", 2)
    receipt_intent = {
        "hardware_contract": receipt["hardware_contract"],
        "transport_mode": receipt["transport_mode"],
        "artifact": receipt["artifact"],
        "selected_device_fingerprint": receipt["selected_device_fingerprint"],
        "selected_profile_slot": receipt["selected_profile_slot"],
        "move_device_authorized": receipt["move_device_authorized"],
        "controlled_reboot_authorized": receipt["controlled_reboot_authorized"],
    }
    if receipt["intent_fingerprint"] != _fingerprint(receipt_intent):
        raise LiveInstallError("Live-install receipt intent integrity check failed.", 2)
    encoded = _canonical_json(receipt).lower()
    if any(marker in encoded for marker in PROHIBITED_TEXT_MARKERS):
        raise LiveInstallError("Live-install receipt contains a prohibited secret/config marker.", 2)
    return receipt


def _parse_receipt_text(text: str) -> Dict[str, Any]:
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        raise LiveInstallError("Live-install receipt is not valid JSON.", 2) from None
    return validate_receipt(value)


def load_receipt(path: Path) -> Dict[str, Any]:
    try:
        text = read_owner_only_text_file(
            Path(path), maximum_bytes=MAX_RECEIPT_BYTES, description="Live-install receipt"
        )
    except PrivateFileError as exc:
        raise LiveInstallError(str(exc), 2) from None
    return _parse_receipt_text(text)


def write_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    value = validate_receipt(dict(receipt))
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    parent = Path(path).parent
    try:
        ensure_private_directory(parent, description="Live-install receipt directory")
        write_private_bytes_atomic(
            Path(path),
            data,
            maximum_bytes=MAX_RECEIPT_BYTES,
            description="Live-install receipt",
            validate_existing_text=lambda text: _parse_receipt_text(text),
        )
    except PrivateFileError as exc:
        raise LiveInstallError(str(exc), 2) from None


def validate_intent_compatible(receipt: Dict[str, Any], intent: Mapping[str, Any]) -> bool:
    """Reject identity drift while allowing explicit monotonic authority upgrades."""

    if receipt["intent_fingerprint"] == _fingerprint(intent):
        return False
    immutable = (
        "hardware_contract",
        "transport_mode",
        "artifact",
        "selected_device_fingerprint",
        "selected_profile_slot",
    )
    if any(receipt[name] != intent[name] for name in immutable):
        raise LiveInstallError("Stale or incompatible live-install receipt was rejected.", 2)
    changed = False
    for name in ("move_device_authorized", "controlled_reboot_authorized"):
        current = receipt[name]
        requested = intent[name]
        if current and not requested:
            raise LiveInstallError("Live-install resume cannot silently drop prior authority.", 2)
        if requested and not current:
            receipt[name] = True
            changed = True
    if not changed:
        raise LiveInstallError("Stale or incompatible live-install receipt was rejected.", 2)
    upgraded_intent = {
        "hardware_contract": receipt["hardware_contract"],
        "transport_mode": receipt["transport_mode"],
        "artifact": receipt["artifact"],
        "selected_device_fingerprint": receipt["selected_device_fingerprint"],
        "selected_profile_slot": receipt["selected_profile_slot"],
        "move_device_authorized": receipt["move_device_authorized"],
        "controlled_reboot_authorized": receipt["controlled_reboot_authorized"],
    }
    receipt["intent_fingerprint"] = _fingerprint(upgraded_intent)
    validate_receipt(receipt)
    return True


def mark_stage(receipt: Dict[str, Any], stage: str, status: str) -> None:
    if stage not in STAGE_ORDER or status not in (PASS, FAIL, SKIPPED):
        raise ValueError("invalid stage transition")
    receipt["stages"][stage] = {"status": status, "epoch": receipt["state_epoch"]}


def stage_done(receipt: Mapping[str, Any], stage: str) -> bool:
    return receipt["stages"][stage]["status"] in (PASS, SKIPPED)


def _empty_native_transaction() -> Dict[str, Any]:
    return {
        "fingerprint": None,
        "status": PENDING,
        "write_required": None,
        "save_required": None,
        "pre_epoch": None,
        "dns_refresh_required": False,
        "pre_snapshot_path_fingerprint": None,
        "running_snapshot_path_fingerprint": None,
    }


def _sync_final_verification(receipt: Dict[str, Any]) -> None:
    receipt["final_verification"] = {
        "installation": PASS
        if all(stage_done(receipt, item) for item in INSTALLATION_STAGES)
        else PENDING,
        "xray": PASS if stage_done(receipt, "healthcheck") else PENDING,
        "autostart": PASS if stage_done(receipt, "autostart") else PENDING,
        "native_routing": PASS
        if stage_done(receipt, "native_proxy_policy_transaction")
        else PENDING,
        "dns": PASS if stage_done(receipt, "protected_dns_gate") else PENDING,
        "client_domain_https": PASS
        if stage_done(receipt, "selected_client_acceptance")
        else PENDING,
    }


def _reset_from_stage(receipt: Dict[str, Any], first_stage: str) -> None:
    start = STAGE_ORDER.index(first_stage)
    for stage in STAGE_ORDER[start:]:
        receipt["stages"][stage] = {"status": PENDING, "epoch": None}
    if start <= STAGE_ORDER.index("generate"):
        receipt["endpoint_manifest_fingerprint"] = None
    if start <= STAGE_ORDER.index("native_proxy_component"):
        receipt["reboot_required"] = None
        receipt["completed_reboot_epoch"] = None
    elif start <= STAGE_ORDER.index("controlled_reboot"):
        receipt["completed_reboot_epoch"] = None
    if start <= STAGE_ORDER.index("native_proxy_policy_transaction"):
        receipt["native_transaction"] = _empty_native_transaction()
    if start <= STAGE_ORDER.index("protected_dns_gate"):
        receipt["dns_gate_status"] = PENDING
    _sync_final_verification(receipt)


def validate_evidence(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise LiveInstallError("Live-install evidence is malformed.", 2)
    _strict_keys(
        value,
        (
            "schema",
            "hardware_contract",
            "epoch",
            "state_change",
            "checks",
            "component",
            "reboot",
            "dns",
            "client",
        ),
        "Live-install evidence",
    )
    if value["schema"] != EVIDENCE_SCHEMA:
        raise LiveInstallError("Live-install evidence schema is unsupported.", 2)
    if value["hardware_contract"] != DEFAULT_HARDWARE_CONTRACT:
        raise LiveInstallError("Live-install evidence hardware contract is unsupported.", 2)
    if type(value["epoch"]) is not int or value["epoch"] < 0:
        raise LiveInstallError("Live-install evidence epoch is invalid.", 2)
    if value["state_change"] not in (
        "none",
        "component",
        "reboot",
        "native",
        "opt",
        "routerkit-xray",
        "contradictory",
    ):
        raise LiveInstallError("Live-install evidence state change is unsupported.", 2)
    checks = value["checks"]
    if not isinstance(checks, dict) or set(checks) != set(EVIDENCE_CHECKS):
        raise LiveInstallError("Live-install evidence check set is incompatible.", 2)
    if any(checks[name] not in (PASS, FAIL, "UNKNOWN") for name in EVIDENCE_CHECKS):
        raise LiveInstallError("Live-install evidence check status is invalid.", 2)
    component = value["component"]
    if not isinstance(component, dict):
        raise LiveInstallError("Live-install component evidence is malformed.", 2)
    _strict_keys(component, ("present", "install_required", "reboot_required"), "Component evidence")
    if any(type(component[name]) is not bool for name in component):
        raise LiveInstallError("Live-install component evidence is malformed.", 2)
    if component["present"] and component["install_required"]:
        raise LiveInstallError("Live-install component evidence is inconsistent.", 2)
    reboot = value["reboot"]
    if not isinstance(reboot, dict):
        raise LiveInstallError("Live-install reboot evidence is malformed.", 2)
    _strict_keys(reboot, ("completed",), "Reboot evidence")
    if type(reboot["completed"]) is not bool:
        raise LiveInstallError("Live-install reboot evidence is malformed.", 2)
    dns = value["dns"]
    if not isinstance(dns, dict):
        raise LiveInstallError("Live-install DNS evidence is malformed.", 2)
    _strict_keys(
        dns,
        ("protected", "availability", "bound_interfaces", "policy_interfaces", "verified"),
        "DNS evidence",
    )
    if type(dns["protected"]) is not bool or type(dns["verified"]) is not bool:
        raise LiveInstallError("Live-install DNS evidence is malformed.", 2)
    if dns["availability"] not in ("any", "unbound", "interfaces", "unknown"):
        raise LiveInstallError("Live-install DNS availability is unsupported.", 2)
    for name in ("bound_interfaces", "policy_interfaces"):
        interfaces = dns[name]
        if not isinstance(interfaces, list) or len(interfaces) > 32:
            raise LiveInstallError("Live-install DNS interface evidence is malformed.", 2)
        if any(not isinstance(item, str) or not SAFE_INTERFACE_RE.fullmatch(item) for item in interfaces):
            raise LiveInstallError("Live-install DNS interface evidence is unsafe.", 2)
        if len(interfaces) != len(set(interfaces)):
            raise LiveInstallError("Live-install DNS interface evidence contains duplicates.", 2)
    client = value["client"]
    if not isinstance(client, dict):
        raise LiveInstallError("Live-install client evidence is malformed.", 2)
    _strict_keys(
        client,
        ("selected_assignment", "domain_dns", "domain_https", "probe_transport"),
        "Client evidence",
    )
    if any(type(client[name]) is not bool for name in (
        "selected_assignment", "domain_dns", "domain_https"
    )):
        raise LiveInstallError("Live-install client evidence is malformed.", 2)
    if client["probe_transport"] not in ("none", "local", "external-agent"):
        raise LiveInstallError("Live-install client probe transport is unsupported.", 2)
    encoded = _canonical_json(value).lower()
    if any(marker in encoded for marker in PROHIBITED_TEXT_MARKERS):
        raise LiveInstallError("Live-install evidence contains a prohibited secret/config marker.", 2)
    return value


def load_evidence(path: Optional[Path]) -> Optional[Dict[str, Any]]:
    if path is None:
        return None
    try:
        text = read_owner_only_text_file(
            Path(path), maximum_bytes=MAX_EVIDENCE_BYTES, description="Live-install evidence"
        )
    except PrivateFileError as exc:
        raise LiveInstallError(str(exc), 2) from None
    try:
        return validate_evidence(json.loads(text))
    except (TypeError, ValueError):
        raise LiveInstallError("Live-install evidence is not valid JSON.", 2) from None


def accept_discovery_evidence(receipt: Dict[str, Any], evidence: Mapping[str, Any]) -> None:
    current_epoch = receipt["state_epoch"]
    supplied_epoch = evidence["epoch"]
    state_change = evidence["state_change"]
    if supplied_epoch == current_epoch:
        if state_change != "none":
            raise LiveInstallError("Same-epoch evidence cannot claim a state change.", 2)
    elif supplied_epoch == current_epoch + 1:
        if state_change not in (
            "component",
            "reboot",
            "native",
            "opt",
            "routerkit-xray",
            "contradictory",
        ):
            raise LiveInstallError("A new state epoch requires an allowed state-change reason.", 2)
        receipt["state_epoch"] = supplied_epoch
        if state_change == "component" and stage_done(receipt, "native_proxy_component"):
            _reset_from_stage(receipt, "native_proxy_component")
        elif state_change == "reboot" and stage_done(receipt, "controlled_reboot"):
            _reset_from_stage(receipt, "controlled_reboot")
            receipt["reboot_required"] = True
        elif state_change == "opt":
            _reset_from_stage(receipt, "usb_ext4_entware_readiness")
        elif state_change == "routerkit-xray":
            _reset_from_stage(receipt, "pinned_xray_bootstrap")
        elif state_change == "contradictory":
            _reset_from_stage(receipt, "network_preflight")
        elif state_change == "native":
            native = receipt["native_transaction"]
            expected_refresh = bool(
                native["status"] == PASS
                and native["dns_refresh_required"]
                and native["pre_epoch"] is not None
                and supplied_epoch == native["pre_epoch"] + 1
            )
            if not expected_refresh and stage_done(
                receipt, "native_proxy_policy_transaction"
            ):
                _reset_from_stage(receipt, "native_proxy_policy_transaction")
    else:
        raise LiveInstallError("Live-install evidence epoch is stale or skips an epoch.", 2)

    fingerprint = _fingerprint(evidence)
    discovery = receipt["discovery"]
    if discovery["epoch"] == supplied_epoch:
        if discovery["evidence_fingerprint"] != fingerprint:
            raise LiveInstallError("Contradictory discovery evidence was supplied in one state epoch.", 2)
        return
    discovery["epoch"] = supplied_epoch
    discovery["evidence_fingerprint"] = fingerprint


def dns_gate(evidence: Mapping[str, Any]) -> Tuple[bool, str]:
    dns = evidence["dns"]
    if not dns["protected"] or not dns["verified"]:
        return False, "no verified protected DNS path is available to the selected policy"
    if dns["availability"] in ("any", "unbound"):
        return True, "existing protected DNS path is reusable as Any/unbound"
    if dns["availability"] == "interfaces":
        overlap = set(dns["bound_interfaces"]) & set(dns["policy_interfaces"])
        if overlap:
            return True, "existing protected DNS path is reachable through the selected policy"
        return False, "protected DNS is bound only to interfaces absent from the selected policy"
    return False, "protected DNS availability is unknown"


def client_acceptance_gate(evidence: Mapping[str, Any]) -> bool:
    client = evidence["client"]
    return bool(
        client["selected_assignment"]
        and client["domain_dns"]
        and client["domain_https"]
        and client["probe_transport"] != "none"
    )


def render_bounded_plan(args: argparse.Namespace, artifact: Mapping[str, str]) -> str:
    lines = [
        "RouterKit live-install bounded plan",
        "",
        "Scope:",
        "- receipt schema: %s" % RECEIPT_SCHEMA,
        "- hardware contract: %s" % args.hardware_contract,
        "- native transport: %s" % args.transport,
        "- pinned Xray: %s (%s)" % (artifact["release"], artifact["artifact_key"]),
        "- selected device fingerprint: %s" % selected_device_fingerprint(args.selected_device_mac),
        "- selected profile slot: %d" % args.profile_slot,
        "- existing selected-device policy move authorized: %s" % str(bool(args.move_device)).lower(),
        "- controlled reboot authorized: %s" % str(bool(args.authorize_reboot)).lower(),
        "- one confirmation covers every listed mutable stage",
        "- browser/Web UI fallback: prohibited",
        "- automatic RU routing packs: disabled",
        "",
        "Stages:",
    ]
    descriptions = {
        "network_preflight": "one bounded network/system preflight for the current epoch",
        "usb_ext4_entware_readiness": "literal /opt on external EXT4 with Entware readiness",
        "pinned_xray_bootstrap": "existing pinned bootstrap transaction",
        "protected_profile_source": "existing protected source/private workspace flow",
        "generate": "existing Xray generator",
        "strict_plan": "existing strict install planner",
        "backup": "existing backup boundary",
        "install": "existing install transaction and routing-override reconcile",
        "healthcheck": "existing Xray/loopback healthcheck",
        "autostart": "existing autostart transaction",
        "native_proxy_component": "typed component inspection/install handoff; no ndmc invention",
        "controlled_reboot": "at most one, only when required and authorized",
        "post_reboot_proof": "fresh management/WAN/LAN/Wi-Fi/USB/Entware/Xray/listener proof",
        "native_proxy_policy_transaction": "existing local ndmc adapter or external transaction protocol",
        "protected_dns_gate": "reuse verified policy-reachable protected DNS or emit exact delta handoff",
        "selected_client_acceptance": "selected assignment plus real domain DNS and HTTPS",
        "final_result": "honest PASS/PENDING classification",
    }
    for index, stage in enumerate(STAGE_ORDER, 1):
        lines.append("%d. %s: %s" % (index, stage, descriptions[stage]))
    lines.extend(
        [
            "",
            "Plan mode performs no writes, protected profile-source reads, discovery, native commands, reboot, or prompts.",
        ]
    )
    return "\n".join(lines)


def _run(
    command: Sequence[str],
    *,
    capture: bool = False,
    suppress: bool = False,
    env: Optional[Mapping[str, str]] = None,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(command),
            check=False,
            stdin=None,
            stdout=subprocess.PIPE if capture or suppress else None,
            stderr=subprocess.PIPE if suppress else None,
            text=True,
            env=None if env is None else dict(env),
        )
    except OSError:
        raise LiveInstallError("Could not execute delegated RouterKit stage.", 127) from None


def _require_success(result: subprocess.CompletedProcess, stage: str) -> None:
    if result.returncode == ROLLBACK_UNPROVEN:
        raise LiveInstallError(
            "%s failed and its existing rollback boundary could not be proven." % stage,
            ROLLBACK_UNPROVEN,
        )
    if result.returncode != 0:
        raise LiveInstallError(
            "%s failed with exit code %d; later mutable stages were not started."
            % (stage, result.returncode),
            result.returncode,
        )


def _require_stage_success(
    result: subprocess.CompletedProcess,
    label: str,
    receipt_path: Path,
    receipt: Dict[str, Any],
    stage: str,
) -> None:
    try:
        _require_success(result, label)
    except LiveInstallError:
        mark_stage(receipt, stage, FAIL)
        _persist(receipt_path, receipt)
        raise


def _source_setup_command(args: argparse.Namespace, repo_root: Path) -> List[str]:
    command = [
        sys.executable,
        str(Path(repo_root) / "scripts" / "routerkit.py"),
        "--repo-root",
        str(repo_root),
        "setup",
        "--generated",
        args.generated,
        "--target-root",
        "/opt",
    ]
    if args.source_env:
        command.extend(("--source-env", args.source_env))
    if args.source_file:
        command.extend(("--source-file", args.source_file))
    if args.reuse_profiles:
        command.extend(("--reuse-profiles", args.reuse_profiles))
    if args.primary_index is not None:
        command.extend(("--primary-index", str(args.primary_index)))
    for index in args.fallback_index:
        command.extend(("--fallback-index", str(index)))
    return command


def _endpoint_manifest(args: argparse.Namespace):
    return load_local_endpoint_manifest(
        Path(args.generated) / "routerkit-local-endpoints.json"
    )


def _persist(path: Path, receipt: Dict[str, Any]) -> None:
    write_receipt(path, receipt)


def _handoff(
    path: Path,
    receipt: Dict[str, Any],
    action: str,
    expectations: Sequence[str],
) -> None:
    _persist(path, receipt)
    raise LiveInstallHandoff(action, expectations)


def _run_installation_stages(
    args: argparse.Namespace,
    repo_root: Path,
    receipt_path: Path,
    receipt: Dict[str, Any],
    evidence: Optional[Mapping[str, Any]],
) -> None:
    env = os.environ.copy()
    pre_source_env = env.copy()
    if args.source_env:
        pre_source_env.pop(args.source_env, None)

    if not stage_done(receipt, "network_preflight") or not stage_done(
        receipt, "usb_ext4_entware_readiness"
    ):
        readiness_stage = (
            "network_preflight"
            if not stage_done(receipt, "network_preflight")
            else "usb_ext4_entware_readiness"
        )
        failed_epoch = receipt["stages"][readiness_stage]["epoch"]
        if (
            receipt["stages"][readiness_stage]["status"] == FAIL
            and (evidence is None or evidence["epoch"] <= failed_epoch)
        ):
            _handoff(
                receipt_path,
                receipt,
                "FAILED_PREFLIGHT_STATE_CHANGE_REQUIRED",
                (
                    "Do not repeat discovery in the failed state epoch.",
                    "Resume only with fresh evidence after an allowed state change.",
                ),
            )
        if evidence is None:
            _handoff(
                receipt_path,
                receipt,
                "NETWORK_PREFLIGHT_EVIDENCE_REQUIRED",
                (
                    "Use bounded typed/read-only checks for management, WAN/PPPoE, LAN, and Wi-Fi.",
                    "Return owner-only routerkit.live-install.evidence.v1 evidence for epoch 0.",
                ),
            )
        accept_discovery_evidence(receipt, evidence)
        required = ("management", "wan_pppoe", "lan", "wifi", "usb_ext4_opt", "entware")
        if any(evidence["checks"][name] != PASS for name in required):
            _handoff(
                receipt_path,
                receipt,
                "NETWORK_OR_STORAGE_READINESS_REQUIRED",
                (
                    "Management, WAN/PPPoE, LAN, Wi-Fi, external EXT4 /opt, and Entware must pass.",
                    "Do not start bootstrap while a required readiness invariant is unknown or failed.",
                ),
            )
        result = _run(
            ["sh", str(repo_root / "scripts" / "preflight.sh"), "--bootstrap-readiness"],
            env=pre_source_env,
        )
        _require_stage_success(
            result,
            "network/preflight and USB/EXT4/Entware readiness",
            receipt_path,
            receipt,
            readiness_stage,
        )
        mark_stage(receipt, "network_preflight", PASS)
        mark_stage(receipt, "usb_ext4_entware_readiness", PASS)
        _persist(receipt_path, receipt)

    if not stage_done(receipt, "pinned_xray_bootstrap"):
        command = [
            sys.executable,
            str(repo_root / "scripts" / "routerkit-bootstrap.py"),
            "--apply",
            "--yes",
        ]
        if args.artifact_manifest:
            command.extend(("--manifest", args.artifact_manifest))
        result = _run(command, env=pre_source_env)
        _require_stage_success(
            result, "pinned Xray bootstrap", receipt_path, receipt, "pinned_xray_bootstrap"
        )
        mark_stage(receipt, "pinned_xray_bootstrap", PASS)
        _persist(receipt_path, receipt)

    if not stage_done(receipt, "strict_plan"):
        result = _run(_source_setup_command(args, repo_root), env=env)
        if args.source_env:
            os.environ.pop(args.source_env, None)
            env.pop(args.source_env, None)
        _require_stage_success(
            result,
            "protected profile source/generate/strict plan",
            receipt_path,
            receipt,
            "protected_profile_source",
        )
        manifest = _endpoint_manifest(args)
        receipt["endpoint_manifest_fingerprint"] = external.manifest_fingerprint(manifest)
        for stage in ("protected_profile_source", "generate", "strict_plan"):
            mark_stage(receipt, stage, PASS)
        _persist(receipt_path, receipt)
    else:
        manifest = _endpoint_manifest(args)
        if external.manifest_fingerprint(manifest) != receipt["endpoint_manifest_fingerprint"]:
            raise LiveInstallError("Generated endpoint manifest does not match the receipt.", 2)

    if not stage_done(receipt, "backup"):
        result = _run(["sh", str(repo_root / "scripts" / "backup.sh")], env=env)
        _require_stage_success(result, "backup", receipt_path, receipt, "backup")
        mark_stage(receipt, "backup", PASS)
        _persist(receipt_path, receipt)

    if not stage_done(receipt, "install"):
        result = _run(
            ["sh", str(repo_root / "scripts" / "install-xray-direct.sh"), args.generated],
            env=env,
        )
        _require_stage_success(result, "install", receipt_path, receipt, "install")
        mark_stage(receipt, "install", PASS)
        _persist(receipt_path, receipt)

    if not stage_done(receipt, "healthcheck"):
        result = _run(["sh", str(repo_root / "scripts" / "healthcheck.sh")], env=env)
        _require_stage_success(result, "healthcheck", receipt_path, receipt, "healthcheck")
        mark_stage(receipt, "healthcheck", PASS)
        receipt["final_verification"]["xray"] = PASS
        _persist(receipt_path, receipt)

    if not stage_done(receipt, "autostart"):
        result = _run(
            [
                sys.executable,
                str(repo_root / "scripts" / "routerkit-autostart.py"),
                "--enable",
                "--apply",
                "--yes",
                "--json",
            ],
            capture=True,
            env=env,
        )
        _require_stage_success(result, "autostart", receipt_path, receipt, "autostart")
        try:
            payload = json.loads(result.stdout)
        except (TypeError, ValueError):
            mark_stage(receipt, "autostart", FAIL)
            _persist(receipt_path, receipt)
            raise LiveInstallError("Autostart returned an invalid machine-readable result.") from None
        if not payload.get("runtime_verified"):
            mark_stage(receipt, "autostart", FAIL)
            _persist(receipt_path, receipt)
            raise LiveInstallError("Autostart did not return verified state.")
        mark_stage(receipt, "autostart", PASS)
        receipt["final_verification"]["installation"] = PASS
        receipt["final_verification"]["autostart"] = PASS
        _persist(receipt_path, receipt)


def _component_and_reboot(
    args: argparse.Namespace,
    receipt_path: Path,
    receipt: Dict[str, Any],
    evidence: Optional[Mapping[str, Any]],
) -> None:
    if evidence is not None:
        accept_discovery_evidence(receipt, evidence)
        _persist(receipt_path, receipt)

    if not stage_done(receipt, "native_proxy_component"):
        if evidence is None:
            _handoff(
                receipt_path,
                receipt,
                "PROXY_COMPONENT_INSPECTION_REQUIRED",
                (
                    "Use a typed vendor operation to inspect the native Proxy client component.",
                    "Return fresh owner-only routerkit.live-install.evidence.v1 evidence.",
                ),
            )
        component = evidence["component"]
        if not component["present"]:
            _handoff(
                receipt_path,
                receipt,
                "PROXY_COMPONENT_INSTALL_REQUIRED",
                (
                    "Install only the native Proxy client component through a typed vendor operation.",
                    "Do not invent an ndmc component-install command or use the Web UI fallback.",
                    "Resume with fresh component-change evidence in the next state epoch.",
                ),
            )
        if evidence["checks"]["proxy_component"] != PASS:
            raise LiveInstallError("Proxy component evidence did not prove readiness.")
        receipt["reboot_required"] = bool(component["reboot_required"])
        mark_stage(receipt, "native_proxy_component", PASS)
        _persist(receipt_path, receipt)

    reboot_required = bool(receipt["reboot_required"])
    if not stage_done(receipt, "controlled_reboot"):
        if not reboot_required:
            mark_stage(receipt, "controlled_reboot", SKIPPED)
            mark_stage(receipt, "post_reboot_proof", SKIPPED)
            _persist(receipt_path, receipt)
        elif evidence is not None and evidence["reboot"]["completed"]:
            if evidence["state_change"] != "reboot" or evidence["epoch"] < 1:
                raise LiveInstallError("Reboot completion requires a fresh reboot state epoch.", 2)
            mark_stage(receipt, "controlled_reboot", PASS)
            receipt["completed_reboot_epoch"] = receipt["state_epoch"]
            if any(evidence["checks"][name] != PASS for name in POST_REBOOT_CHECKS):
                raise LiveInstallError("Post-reboot proof is incomplete; native mutation was not started.")
            mark_stage(receipt, "post_reboot_proof", PASS)
            _persist(receipt_path, receipt)
        elif not args.authorize_reboot:
            _handoff(
                receipt_path,
                receipt,
                "CONTROLLED_REBOOT_AUTHORIZATION_REQUIRED",
                ("Authorize the single controlled reboot in the installation scope.",),
            )
        elif not evidence["reboot"]["completed"]:
            _handoff(
                receipt_path,
                receipt,
                "CONTROLLED_REBOOT_REQUIRED",
                (
                    "Use the typed vendor reboot operation exactly once.",
                    "Resume with fresh reboot evidence in the next state epoch.",
                ),
            )


def _local_native_stage(
    args: argparse.Namespace,
    repo_root: Path,
    receipt_path: Path,
    receipt: Dict[str, Any],
) -> None:
    manifest_path = str(Path(args.generated) / "routerkit-local-endpoints.json")
    common = [
        "--manifest-file",
        manifest_path,
        "--device-mac",
        args.selected_device_mac,
        "--profile-slot",
        str(args.profile_slot),
    ]
    if args.move_device:
        common.append("--move-device")
    if args.ndmc_path:
        common.extend(("--ndmc-path", args.ndmc_path))
    script = str(repo_root / "scripts" / "routerkit-netcraze-live.py")
    plan = _run([sys.executable, script, "plan", "--json"] + common, capture=True)
    _require_success(plan, "local ndmc native plan")
    apply_command = [
        sys.executable,
        script,
        "apply",
        "--json",
        "--yes",
        "--contract",
        args.hardware_contract,
    ] + common
    applied = _run(apply_command, capture=True)
    _require_success(applied, "local ndmc native apply")
    try:
        payload = json.loads(applied.stdout)
    except (TypeError, ValueError):
        raise LiveInstallError("Local native adapter returned invalid JSON.") from None
    if not payload.get("verified"):
        raise LiveInstallError("Local native adapter did not verify applied state.")
    native = receipt["native_transaction"]
    native["status"] = PASS
    native["write_required"] = bool(payload.get("write_required"))
    native["save_required"] = bool(payload.get("write_required"))
    native["pre_epoch"] = receipt["state_epoch"]
    native["dns_refresh_required"] = bool(payload.get("write_required"))
    mark_stage(receipt, "native_proxy_policy_transaction", PASS)
    receipt["final_verification"]["native_routing"] = PASS
    _persist(receipt_path, receipt)


def _external_native_stage(
    args: argparse.Namespace,
    receipt_path: Path,
    receipt: Dict[str, Any],
) -> None:
    manifest = _endpoint_manifest(args)
    transaction_path = Path(args.transaction_file) if args.transaction_file else (
        receipt_path.parent / "netcraze-transaction.json"
    )
    native = receipt["native_transaction"]

    if native["status"] == PENDING:
        if not args.external_pre_snapshot_file:
            _handoff(
                receipt_path,
                receipt,
                "EXTERNAL_NATIVE_PRE_STATE_REQUIRED",
                (
                    "Retrieve one fresh running configuration through the official transport.",
                    "Store it only in an owner-only protected file.",
                ),
            )
        state = external.load_running_config_snapshot(Path(args.external_pre_snapshot_file))
        packet = external.build_external_transaction(
            manifest,
            state,
            device_mac=args.selected_device_mac,
            profile_slot=args.profile_slot,
            allow_move=args.move_device,
            hardware_contract=args.hardware_contract,
        )
        try:
            ensure_private_directory(
                transaction_path.parent,
                description="External native transaction directory",
            )
        except PrivateFileError as exc:
            raise LiveInstallError(str(exc), 2) from None
        external.write_external_transaction(transaction_path, packet)
        verification = external.verify_external_transaction(packet, manifest, state, phase="pre")
        if not verification["verified"]:
            raise LiveInstallError("External native pre-state was not verified.")
        native["fingerprint"] = packet["transaction_fingerprint"]
        native["status"] = "PLANNED"
        native["write_required"] = bool(packet["write_required"])
        native["save_required"] = bool(packet["save_required"])
        native["pre_epoch"] = receipt["state_epoch"]
        native["dns_refresh_required"] = bool(packet["write_required"])
        native["pre_snapshot_path_fingerprint"] = _path_fingerprint(
            Path(args.external_pre_snapshot_file)
        )
        _persist(receipt_path, receipt)

    packet = external.load_external_transaction(transaction_path)
    external.validate_external_transaction(packet, manifest)
    if packet["transaction_fingerprint"] != native["fingerprint"]:
        raise LiveInstallError("External transaction does not match the live-install receipt.", 2)

    if native["status"] == "PLANNED":
        if not args.external_post_snapshot_file:
            action = (
                "EXTERNAL_NATIVE_TRANSACTION_DELIVERY_REQUIRED"
                if native["write_required"]
                else "EXTERNAL_NATIVE_NOOP_READBACK_REQUIRED"
            )
            expectations = [
                "Execute only the packet commands in order." if native["write_required"] else "Execute no commands and do not save.",
                "Retrieve a fresh running-state snapshot; transport success is not verification.",
                "Resume with --external-post-snapshot-file.",
            ]
            _handoff(receipt_path, receipt, action, expectations)
        post_path_fingerprint = _path_fingerprint(Path(args.external_post_snapshot_file))
        if post_path_fingerprint == native["pre_snapshot_path_fingerprint"]:
            raise LiveInstallError(
                "External running-state verification requires a fresh snapshot file, not the pre-state path.",
                2,
            )
        state = external.load_running_config_snapshot(Path(args.external_post_snapshot_file))
        verification = external.verify_external_transaction(packet, manifest, state, phase="running")
        if not verification["verified"]:
            raise LiveInstallError("External native running state was not verified.")
        native["status"] = "RUNNING_VERIFIED"
        native["running_snapshot_path_fingerprint"] = post_path_fingerprint
        _persist(receipt_path, receipt)

    if native["status"] == "RUNNING_VERIFIED" and native["save_required"]:
        if not args.external_saved_snapshot_file:
            _handoff(
                receipt_path,
                receipt,
                "EXTERNAL_NATIVE_SAVE_REQUIRED",
                (
                    "Execute only the packet save_command authorized by running-state verification.",
                    "Retrieve fresh saved/startup state when available.",
                    "Resume with --external-saved-snapshot-file.",
                ),
            )
        saved_path_fingerprint = _path_fingerprint(Path(args.external_saved_snapshot_file))
        if saved_path_fingerprint in (
            native["pre_snapshot_path_fingerprint"],
            native["running_snapshot_path_fingerprint"],
        ):
            raise LiveInstallError(
                "External saved-state verification requires a fresh snapshot file.", 2
            )
        saved = external.load_running_config_snapshot(Path(args.external_saved_snapshot_file))
        verification = external.verify_external_transaction(packet, manifest, saved, phase="saved")
        if not verification["saved_state_verified"]:
            raise LiveInstallError("External native saved state was not verified.")

    native["status"] = PASS
    mark_stage(receipt, "native_proxy_policy_transaction", PASS)
    receipt["final_verification"]["native_routing"] = PASS
    _persist(receipt_path, receipt)


def _native_dns_client_stages(
    args: argparse.Namespace,
    repo_root: Path,
    receipt_path: Path,
    receipt: Dict[str, Any],
    evidence: Optional[Mapping[str, Any]],
) -> None:
    if not stage_done(receipt, "native_proxy_policy_transaction"):
        try:
            if args.transport == "local-ndmc":
                _local_native_stage(args, repo_root, receipt_path, receipt)
            else:
                _external_native_stage(args, receipt_path, receipt)
        except LiveInstallHandoff:
            raise
        except (LiveInstallError, live.LiveAdapterError, NetcrazePlanError, PrivateFileError):
            mark_stage(receipt, "native_proxy_policy_transaction", FAIL)
            _persist(receipt_path, receipt)
            raise

    native = receipt["native_transaction"]
    if not stage_done(receipt, "protected_dns_gate"):
        if evidence is None:
            _handoff(
                receipt_path,
                receipt,
                "PROTECTED_DNS_EVIDENCE_REQUIRED",
                ("Return fresh policy-aware protected DNS evidence.",),
            )
        if native["dns_refresh_required"] and evidence["epoch"] <= native["pre_epoch"]:
            _handoff(
                receipt_path,
                receipt,
                "PROTECTED_DNS_POST_NATIVE_EVIDENCE_REQUIRED",
                ("Return fresh DNS evidence after the native policy state change.",),
            )
        accepted, reason = dns_gate(evidence)
        if not accepted:
            _handoff(
                receipt_path,
                receipt,
                "PROTECTED_DNS_DELTA_REQUIRED",
                (
                    reason + ".",
                    "Reuse an existing protected path when possible.",
                    "A PPPoE-only binding absent from the Proxy policy is insufficient.",
                    "Any/unbound is the known NC-3812/5.1.5 compatible form; ip global is not a DNS fix.",
                ),
            )
        mark_stage(receipt, "protected_dns_gate", PASS)
        receipt["dns_gate_status"] = PASS
        receipt["final_verification"]["dns"] = PASS
        _persist(receipt_path, receipt)

    if not stage_done(receipt, "selected_client_acceptance"):
        if evidence is None or not client_acceptance_gate(evidence):
            _handoff(
                receipt_path,
                receipt,
                "CLIENT_FUNCTIONAL_VERIFICATION_REQUIRED",
                (
                    "Prove the explicitly selected client is assigned to the intended RouterKit policy.",
                    "Prove real domain DNS resolution through that policy.",
                    "Prove real domain HTTPS through that policy.",
                    "Return bounded owner-only evidence; IP-only curl is insufficient.",
                ),
            )
        mark_stage(receipt, "selected_client_acceptance", PASS)
        receipt["final_verification"]["client_domain_https"] = PASS
        _persist(receipt_path, receipt)

    mark_stage(receipt, "final_result", PASS)
    _persist(receipt_path, receipt)


def _print_status(receipt: Mapping[str, Any]) -> None:
    print("STATE_SCHEMA=%s" % receipt["schema"])
    print("TRANSPORT_MODE=%s" % receipt["transport_mode"])
    print("STATE_EPOCH=%d" % receipt["state_epoch"])
    for stage in STAGE_ORDER:
        print("STAGE_%s=%s" % (stage.upper(), receipt["stages"][stage]["status"]))
    final = receipt["final_verification"]
    print("INSTALLATION=%s" % final["installation"])
    print("XRAY=%s" % final["xray"])
    print("AUTOSTART=%s" % final["autostart"])
    print("COMPONENT_STAGE=%s" % receipt["stages"]["native_proxy_component"]["status"])
    print("REBOOT_STAGE=%s" % receipt["stages"]["controlled_reboot"]["status"])
    print("NATIVE_ROUTING=%s" % final["native_routing"])
    print("DNS=%s" % final["dns"])
    print("CLIENT_DOMAIN_HTTPS=%s" % final["client_domain_https"])
    print(
        "LOCAL_ROUTING_PRESERVED=%s"
        % (PASS if stage_done(receipt, "install") else PENDING)
    )
    print("RESULT=%s" % receipt["stages"]["final_result"]["status"])


def confirm_scope(input_fn=input) -> bool:
    return input_fn("Proceed with the complete bounded live-install scope? [y/N]: ").strip().lower() in (
        "y",
        "yes",
    )


def _validate_args(args: argparse.Namespace) -> None:
    if args.mode == "status":
        return
    if args.target_root != "/opt":
        raise LiveInstallError("live-install apply/resume supports only literal /opt.", 2)
    if not args.selected_device_mac or args.profile_slot is None:
        raise LiveInstallError(
            "live-install requires an explicit --selected-device-mac and --profile-slot.", 2
        )
    if args.source_env:
        try:
            validate_env_name(args.source_env)
        except PayloadValidationError:
            raise LiveInstallError("--source-env must be a valid dedicated ROUTERKIT_* name.", 2) from None
        if not args.source_env.startswith("ROUTERKIT_") or args.source_env == "ROUTERKIT_":
            raise LiveInstallError("--source-env must be a valid dedicated ROUTERKIT_* name.", 2)
    source_count = sum(bool(item) for item in (
        args.source_env,
        args.source_file,
        args.reuse_profiles,
    ))
    if source_count > 1:
        raise LiveInstallError("Profile source options are mutually exclusive.", 2)
    if args.fallback_index and args.primary_index is None:
        raise LiveInstallError("--fallback-index requires --primary-index.", 2)
    indexes = ([] if args.primary_index is None else [args.primary_index]) + args.fallback_index
    if len(args.fallback_index) > 2 or len(indexes) != len(set(indexes)):
        raise LiveInstallError("Profile selection indexes are invalid.", 2)
    if args.mode == "plan" and args.yes:
        raise LiveInstallError("live-install plan does not accept --yes.", 2)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run or resume the bounded RouterKit live-install state machine.")
    parser.add_argument("mode", choices=("plan", "apply", "resume", "status"))
    parser.add_argument("--repo-root")
    parser.add_argument("--transport", choices=SUPPORTED_TRANSPORTS, default="local-ndmc")
    parser.add_argument("--receipt-file", default="/opt/var/lib/routerkit/live-install/receipt.json")
    parser.add_argument("--target-root", default="/opt")
    parser.add_argument("--generated", default="generated")
    parser.add_argument("--hardware-contract", default=DEFAULT_HARDWARE_CONTRACT)
    parser.add_argument("--artifact-manifest")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--source-env")
    source.add_argument("--source-file")
    source.add_argument("--reuse-profiles")
    parser.add_argument("--primary-index", type=int)
    parser.add_argument("--fallback-index", type=int, action="append", default=[])
    parser.add_argument("--selected-device-mac")
    parser.add_argument("--profile-slot", type=int)
    parser.add_argument("--move-device", action="store_true")
    parser.add_argument("--ndmc-path")
    parser.add_argument("--evidence-file")
    parser.add_argument("--authorize-reboot", action="store_true")
    parser.add_argument("--external-pre-snapshot-file")
    parser.add_argument("--external-post-snapshot-file")
    parser.add_argument("--external-saved-snapshot-file")
    parser.add_argument("--transaction-file")
    parser.add_argument("--yes", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None, *, input_fn=input) -> int:
    args = parse_args(argv)
    try:
        _validate_args(args)
        repo_root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parents[1]
        receipt_path = Path(args.receipt_file)
        if args.mode == "status":
            _print_status(load_receipt(receipt_path))
            return 0

        receipt = None
        if args.mode == "resume":
            receipt = load_receipt(receipt_path)
            # Resume retains already granted authority. Supplying either flag
            # explicitly is a monotonic, machine-recorded scope upgrade.
            args.move_device = bool(args.move_device or receipt["move_device_authorized"])
            args.authorize_reboot = bool(
                args.authorize_reboot or receipt["controlled_reboot_authorized"]
            )

        artifact = artifact_identity(
            repo_root, None if not args.artifact_manifest else Path(args.artifact_manifest)
        )
        intent = build_intent(
            hardware_contract=args.hardware_contract,
            transport_mode=args.transport,
            artifact=artifact,
            selected_device_mac=args.selected_device_mac,
            profile_slot=args.profile_slot,
            move_device_authorized=args.move_device,
            controlled_reboot_authorized=args.authorize_reboot,
        )
        if args.mode == "plan":
            print(render_bounded_plan(args, artifact))
            return 0

        if args.mode == "apply":
            if receipt_path.exists():
                raise LiveInstallError("Receipt already exists; use live-install resume.", 2)
            print(render_bounded_plan(args, artifact))
            if not args.yes and not confirm_scope(input_fn):
                print("Live-install cancelled before every mutable stage.")
                return 1
            receipt = initial_receipt(intent)
            write_receipt(receipt_path, receipt)
        else:
            if receipt is None:
                raise AssertionError("resume requires a loaded receipt")
            if validate_intent_compatible(receipt, intent):
                write_receipt(receipt_path, receipt)

        evidence = load_evidence(None if not args.evidence_file else Path(args.evidence_file))
        _run_installation_stages(args, repo_root, receipt_path, receipt, evidence)
        _component_and_reboot(args, receipt_path, receipt, evidence)
        _native_dns_client_stages(args, repo_root, receipt_path, receipt, evidence)
        _print_status(receipt)
        return 0
    except LiveInstallHandoff as exc:
        print("RESULT=PENDING")
        print("NEXT_ACTION=%s" % exc.action)
        for index, expectation in enumerate(exc.expectations, 1):
            print("EXPECTED_CONDITION_%d=%s" % (index, expectation))
        return exc.exit_code
    except (LiveInstallError, NetcrazePlanError, live.LiveAdapterError, PrivateFileError) as exc:
        code = getattr(exc, "exit_code", 1)
        print("routerkit-live-install: %s" % exc, file=sys.stderr)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
