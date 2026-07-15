#!/usr/bin/env python3
"""Fixture-first read-only local-device discovery primitives for RouterKit."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from routerkit_private_io import (
    PrivateFileEncodingError,
    PrivateFileError,
    PrivateFileTooLargeError,
    read_owner_only_text_file,
)

FIXTURE_SCHEMA = "routerkit.devices.fixture.v1"
DISCOVERY_SCHEMA = "routerkit.devices.discovery.v1"
MAX_INVENTORY_BYTES = 256 * 1024
MAX_ADAPTER_OUTPUT_BYTES = 256 * 1024

STATE_SUPPORTED = "supported"
STATE_UNSUPPORTED = "unsupported"
STATE_CONTRACT_UNVERIFIED = "contract_unverified"
STATE_MALFORMED_OUTPUT = "malformed_output"
STATE_PERMISSION_DENIED = "permission_denied"
STATE_TIMEOUT = "timeout"
STATE_OUTPUT_TOO_LARGE = "output_too_large"
STATE_SOURCE_MISSING = "source_missing"

ONLINE_STATES = ("online", "unknown", "offline")
CONNECTION_TYPES = ("ethernet", "wifi", "unknown")
SENSITIVITY_LOCAL = "local_sensitive"
SENSITIVITY_PUBLIC = "public_evidence_redacted"

_RECORD_KEYS = {
    "source_record_id",
    "display_name",
    "addresses",
    "stable_identifier",
    "stable_identifier_type",
    "vendor_record_id",
    "online_state",
    "connection_type",
    "wifi_band",
    "interface",
    "existing_policy",
    "last_seen",
    "stale",
}


class DeviceDiscoveryError(Exception):
    """Secret-safe discovery error suitable for user-facing output."""

    def __init__(self, message: str, state: str = STATE_MALFORMED_OUTPUT) -> None:
        super().__init__(message)
        self.state = state


class DeviceSelectionError(DeviceDiscoveryError):
    pass


@dataclass(frozen=True)
class CommandResult:
    argv: Tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes


class CommandExecutionError(DeviceDiscoveryError):
    pass


class BoundedCommandRunner:
    """Run exact allowlisted argv vectors without shell interpolation."""

    def __init__(self, allowed_argv: Iterable[Sequence[str]]) -> None:
        self.allowed_argv = {tuple(argv) for argv in allowed_argv}

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        maximum_output_bytes: int = MAX_ADAPTER_OUTPUT_BYTES,
        env: Optional[Mapping[str, str]] = None,
    ) -> CommandResult:
        argv_tuple = tuple(argv)
        if argv_tuple not in self.allowed_argv:
            raise CommandExecutionError(
                "Adapter command is not allowlisted.",
                state=STATE_UNSUPPORTED,
            )
        if env is None:
            env = {}
        try:
            completed = subprocess.run(
                list(argv_tuple),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                env=dict(env),
            )
        except subprocess.TimeoutExpired:
            raise CommandExecutionError(
                "Adapter command timed out.",
                state=STATE_TIMEOUT,
            ) from None
        except PermissionError:
            raise CommandExecutionError(
                "Adapter command permission denied.",
                state=STATE_PERMISSION_DENIED,
            ) from None
        except FileNotFoundError:
            raise CommandExecutionError(
                "Adapter command is unavailable.",
                state=STATE_UNSUPPORTED,
            ) from None
        except OSError:
            raise CommandExecutionError(
                "Adapter command could not be executed.",
                state=STATE_UNSUPPORTED,
            ) from None
        output_size = len(completed.stdout or b"") + len(completed.stderr or b"")
        if output_size > maximum_output_bytes:
            raise CommandExecutionError(
                "Adapter command output is too large.",
                state=STATE_OUTPUT_TOO_LARGE,
            )
        if completed.returncode == 126:
            raise CommandExecutionError(
                "Adapter command permission denied.",
                state=STATE_PERMISSION_DENIED,
            )
        return CommandResult(
            argv=argv_tuple,
            returncode=int(completed.returncode),
            stdout=completed.stdout or b"",
            stderr=completed.stderr or b"",
        )


@dataclass(frozen=True)
class DiscoverySource:
    name: str
    kind: str
    state: str = STATE_SUPPORTED
    confidence: str = "fixture"


@dataclass(frozen=True)
class RawDeviceRecord:
    source: str
    source_kind: str
    source_record_id: str
    display_name: Optional[str] = None
    addresses: Tuple[str, ...] = ()
    stable_identifier: Optional[str] = None
    stable_identifier_type: Optional[str] = None
    vendor_record_id: Optional[str] = None
    online_state: str = "unknown"
    connection_type: str = "unknown"
    wifi_band: Optional[str] = None
    interface: Optional[str] = None
    existing_policy: Optional[str] = None
    last_seen: Optional[str] = None
    stale: bool = False


@dataclass(frozen=True)
class NormalizedDevice:
    record_id: str
    display_name: str
    addresses: Tuple[str, ...]
    stable_identifier: Optional[str]
    stable_identifier_type: Optional[str]
    online_state: str
    connection_type: str
    wifi_band: Optional[str]
    interface: Optional[str]
    existing_policy: Optional[str]
    sources: Tuple[str, ...]
    stale: bool
    sensitivity: str = SENSITIVITY_LOCAL
    selectable: bool = False
    selection_block_reason: Optional[str] = None
    conflict: bool = False


@dataclass(frozen=True)
class DiscoveryResult:
    adapter_state: str
    sources: Tuple[DiscoverySource, ...]
    devices: Tuple[NormalizedDevice, ...]
    errors: Tuple[str, ...] = ()
    generated_at: Optional[str] = None


@dataclass(frozen=True)
class DeviceSelection:
    selected: bool
    token: Optional[str] = None
    device: Optional[NormalizedDevice] = None
    reason: str = "no device assignment"

    def to_json(self) -> Dict[str, Any]:
        if not self.selected:
            return {
                "selected": False,
                "reason": self.reason,
                "no_device_assignment": True,
            }
        assert self.device is not None
        return {
            "selected": True,
            "selection_token": self.token,
            "record_id": self.device.record_id,
            "display_name": self.device.display_name,
            "display_name_sensitivity": SENSITIVITY_LOCAL,
            "no_device_assignment": False,
        }


def _stable_hash(value: str, length: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _clean_text(value: Optional[Any]) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DeviceDiscoveryError("Device inventory contains an unsupported text field.")
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _normalize_mac(value: str) -> str:
    compact = re.sub(r"[^0-9a-fA-F]", "", value)
    if len(compact) != 12 or not re.fullmatch(r"[0-9a-fA-F]{12}", compact):
        raise DeviceDiscoveryError("Device inventory contains an invalid stable identifier.")
    octets = [compact[index : index + 2].lower() for index in range(0, 12, 2)]
    return ":".join(octets)


def _normalize_stable_identifier(
    identifier: Optional[str],
    identifier_type: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    identifier = _clean_text(identifier)
    identifier_type = (_clean_text(identifier_type) or "unknown").lower() if identifier else None
    if identifier is None:
        return None, None
    if identifier_type == "mac":
        return _normalize_mac(identifier), "mac"
    if identifier_type not in ("router_id", "vendor_record_id", "unknown"):
        raise DeviceDiscoveryError("Device inventory contains an unsupported identifier type.")
    return identifier.lower(), identifier_type


def _normalize_addresses(values: Any) -> Tuple[str, ...]:
    if values is None:
        return ()
    if not isinstance(values, list):
        raise DeviceDiscoveryError("Device inventory contains an unsupported address field.")
    addresses = []
    for value in values:
        if not isinstance(value, str):
            raise DeviceDiscoveryError("Device inventory contains an unsupported address field.")
        try:
            addresses.append(str(ipaddress.ip_address(value.strip())))
        except ValueError:
            raise DeviceDiscoveryError("Device inventory contains an invalid address.") from None
    return tuple(sorted(set(addresses), key=lambda item: (ipaddress.ip_address(item).version, ipaddress.ip_address(item).packed)))


def _selection_identity(record: RawDeviceRecord) -> Optional[str]:
    if record.stable_identifier:
        return "%s:%s" % (record.stable_identifier_type or "unknown", record.stable_identifier)
    if record.vendor_record_id:
        return "vendor_record_id:%s" % record.vendor_record_id
    return None


def _record_sort_key(record: RawDeviceRecord) -> Tuple[str, str, str]:
    return (
        record.source,
        record.source_record_id,
        _selection_identity(record) or ",".join(record.addresses),
    )


def _record_group_key(record: RawDeviceRecord) -> str:
    identity = _selection_identity(record)
    if identity:
        return "stable|" + identity
    return "weak|%s|%s|%s" % (
        record.source,
        record.source_record_id,
        ",".join(record.addresses),
    )


def _pick_name(records: Sequence[RawDeviceRecord], record_id: str) -> str:
    for record in records:
        if record.display_name:
            return record.display_name
    return "Unnamed device %s" % record_id[:8]


def _pick_online_state(records: Sequence[RawDeviceRecord]) -> str:
    if any(record.online_state == "online" for record in records):
        return "online"
    if any(record.online_state == "unknown" for record in records):
        return "unknown"
    return "offline"


def _pick_connection_type(records: Sequence[RawDeviceRecord]) -> str:
    concrete = [record.connection_type for record in records if record.connection_type != "unknown"]
    if not concrete:
        return "unknown"
    if len(set(concrete)) == 1:
        return concrete[0]
    return "unknown"


def _pick_optional(values: Iterable[Optional[str]]) -> Optional[str]:
    for value in values:
        if value:
            return value
    return None


def normalize_records(
    records: Sequence[RawDeviceRecord],
    *,
    source_errors: Sequence[str] = (),
) -> DiscoveryResult:
    grouped: Dict[str, List[RawDeviceRecord]] = {}
    for record in sorted(records, key=_record_sort_key):
        grouped.setdefault(_record_group_key(record), []).append(record)

    vendor_identities: Dict[str, set] = {}
    for record in records:
        if record.vendor_record_id:
            vendor_identities.setdefault(record.vendor_record_id, set()).add(
                _selection_identity(record) or "weak"
            )
    conflicting_vendor_ids = {
        vendor_id for vendor_id, identities in vendor_identities.items() if len(identities) > 1
    }

    devices: List[NormalizedDevice] = []
    for key in sorted(grouped):
        group = grouped[key]
        identity = _selection_identity(group[0])
        stable_identifier = group[0].stable_identifier
        stable_identifier_type = group[0].stable_identifier_type
        seed = identity or "|".join(
            "%s:%s:%s" % (record.source, record.source_record_id, ",".join(record.addresses))
            for record in group
        )
        record_id = "dev-" + _stable_hash(seed, length=20)
        addresses = tuple(
            sorted(
                {
                    address
                    for record in group
                    for address in record.addresses
                },
                key=lambda item: (ipaddress.ip_address(item).version, ipaddress.ip_address(item).packed),
            )
        )
        conflict = any(record.vendor_record_id in conflicting_vendor_ids for record in group)
        selectable = bool(identity and not conflict)
        reason = None
        if not identity:
            reason = "stable identifier unavailable"
        elif conflict:
            reason = "conflicting stable identity"
        sources = tuple(sorted(set(record.source_kind for record in group)))
        devices.append(
            NormalizedDevice(
                record_id=record_id,
                display_name=_pick_name(group, record_id),
                addresses=addresses,
                stable_identifier=stable_identifier,
                stable_identifier_type=stable_identifier_type,
                online_state=_pick_online_state(group),
                connection_type=_pick_connection_type(group),
                wifi_band=_pick_optional(record.wifi_band for record in group),
                interface=_pick_optional(record.interface for record in group),
                existing_policy=_pick_optional(record.existing_policy for record in group),
                sources=sources,
                stale=all(record.stale for record in group),
                selectable=selectable,
                selection_block_reason=reason,
                conflict=conflict,
            )
        )

    label_counts: Dict[Tuple[str, Tuple[str, ...], str], int] = {}
    for device in devices:
        label = (device.display_name.lower(), device.addresses, device.connection_type)
        label_counts[label] = label_counts.get(label, 0) + 1
    disambiguated = []
    for device in devices:
        label = (device.display_name.lower(), device.addresses, device.connection_type)
        if label_counts[label] > 1:
            disambiguated.append(
                NormalizedDevice(
                    record_id=device.record_id,
                    display_name=device.display_name,
                    addresses=device.addresses,
                    stable_identifier=device.stable_identifier,
                    stable_identifier_type=device.stable_identifier_type,
                    online_state=device.online_state,
                    connection_type=device.connection_type,
                    wifi_band=device.wifi_band,
                    interface=device.interface,
                    existing_policy=device.existing_policy,
                    sources=device.sources,
                    stale=device.stale,
                    sensitivity=device.sensitivity,
                    selectable=False,
                    selection_block_reason="ambiguous duplicate device label",
                    conflict=device.conflict,
                )
            )
        else:
            disambiguated.append(device)

    sorted_devices = tuple(sorted(disambiguated, key=device_sort_key))
    sources = tuple(sorted({DiscoverySource(record.source, record.source_kind) for record in records}, key=lambda source: source.name))
    return DiscoveryResult(
        adapter_state=STATE_SUPPORTED if not source_errors else STATE_MALFORMED_OUTPUT,
        sources=sources,
        devices=sorted_devices,
        errors=tuple(source_errors),
    )


def device_sort_key(device: NormalizedDevice) -> Tuple[int, str, str]:
    state_order = {"online": 0, "unknown": 1, "offline": 2}
    return (
        state_order.get(device.online_state, 1),
        device.display_name.casefold(),
        device.record_id,
    )


def _parse_source(source: Any) -> Tuple[DiscoverySource, List[RawDeviceRecord], List[str]]:
    if not isinstance(source, dict):
        raise DeviceDiscoveryError("Device inventory contains an unsupported source entry.")
    allowed_source_keys = {"name", "kind", "state", "confidence", "records"}
    if set(source) - allowed_source_keys:
        raise DeviceDiscoveryError("Device inventory contains unsupported source schema.")
    name = _clean_text(source.get("name"))
    kind = _clean_text(source.get("kind"))
    if not name or not kind:
        raise DeviceDiscoveryError("Device inventory source is missing required metadata.")
    state = _clean_text(source.get("state")) or STATE_SUPPORTED
    confidence = _clean_text(source.get("confidence")) or "fixture"
    summary = DiscoverySource(name=name, kind=kind, state=state, confidence=confidence)
    if state != STATE_SUPPORTED:
        return summary, [], ["source %s reported %s" % (name, state)]
    records_value = source.get("records")
    if not isinstance(records_value, list):
        raise DeviceDiscoveryError("Device inventory source has no record list.")
    records = []
    errors = []
    for index, item in enumerate(records_value):
        try:
            records.append(_parse_record(name, kind, index, item))
        except DeviceDiscoveryError:
            errors.append("malformed record skipped from source %s" % name)
    return summary, records, errors


def _parse_record(source_name: str, source_kind: str, index: int, item: Any) -> RawDeviceRecord:
    if not isinstance(item, dict):
        raise DeviceDiscoveryError("Device inventory contains a malformed record.")
    if set(item) - _RECORD_KEYS:
        raise DeviceDiscoveryError("Device inventory contains unsupported record schema.")
    source_record_id = _clean_text(item.get("source_record_id")) or "%s-%d" % (source_name, index + 1)
    stable_identifier, stable_identifier_type = _normalize_stable_identifier(
        item.get("stable_identifier"),
        item.get("stable_identifier_type"),
    )
    vendor_record_id = _clean_text(item.get("vendor_record_id"))
    online_state = (_clean_text(item.get("online_state")) or "unknown").lower()
    connection_type = (_clean_text(item.get("connection_type")) or "unknown").lower()
    if online_state not in ONLINE_STATES:
        raise DeviceDiscoveryError("Device inventory contains an unsupported online state.")
    if connection_type not in CONNECTION_TYPES:
        raise DeviceDiscoveryError("Device inventory contains an unsupported connection type.")
    addresses = _normalize_addresses(item.get("addresses", []))
    if not (stable_identifier or vendor_record_id or addresses):
        raise DeviceDiscoveryError("Device inventory record has no assignable or display identity.")
    return RawDeviceRecord(
        source=source_name,
        source_kind=source_kind,
        source_record_id=source_record_id,
        display_name=_clean_text(item.get("display_name")),
        addresses=addresses,
        stable_identifier=stable_identifier,
        stable_identifier_type=stable_identifier_type,
        vendor_record_id=vendor_record_id,
        online_state=online_state,
        connection_type=connection_type,
        wifi_band=_clean_text(item.get("wifi_band")),
        interface=_clean_text(item.get("interface")),
        existing_policy=_clean_text(item.get("existing_policy")),
        last_seen=_clean_text(item.get("last_seen")),
        stale=bool(item.get("stale", False)),
    )


def parse_fixture_inventory(text: str) -> DiscoveryResult:
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        raise DeviceDiscoveryError("Device inventory is not valid JSON.") from None
    if not isinstance(document, dict):
        raise DeviceDiscoveryError("Device inventory root must be an object.")
    allowed_keys = {"schema", "generated_at", "sources"}
    if set(document) - allowed_keys:
        raise DeviceDiscoveryError("Device inventory contains unsupported top-level schema.")
    if document.get("schema") != FIXTURE_SCHEMA:
        raise DeviceDiscoveryError("Device inventory schema is unsupported.")
    sources_value = document.get("sources")
    if not isinstance(sources_value, list):
        raise DeviceDiscoveryError("Device inventory sources must be a list.")
    all_sources: List[DiscoverySource] = []
    records: List[RawDeviceRecord] = []
    errors: List[str] = []
    for source_value in sources_value:
        source, source_records, source_errors = _parse_source(source_value)
        all_sources.append(source)
        records.extend(source_records)
        errors.extend(source_errors)
    result = normalize_records(records, source_errors=errors)
    return DiscoveryResult(
        adapter_state=result.adapter_state,
        sources=tuple(all_sources),
        devices=result.devices,
        errors=result.errors,
        generated_at=_clean_text(document.get("generated_at")),
    )


def read_private_inventory_file(path: Path) -> str:
    source = Path(path)
    try:
        metadata = source.lstat()
    except OSError:
        raise PrivateFileError("Device inventory file is missing.") from None
    if stat.S_ISLNK(metadata.st_mode):
        raise PrivateFileError("Device inventory file must be a regular, non-symlink file.")
    if not stat.S_ISREG(metadata.st_mode):
        raise PrivateFileError("Device inventory file must be a regular, non-symlink file.")
    if os.name == "posix" and metadata.st_nlink != 1:
        raise PrivateFileError("Device inventory file must not have hard links.")
    text = read_owner_only_text_file(
        source,
        maximum_bytes=MAX_INVENTORY_BYTES,
        description="Device inventory file",
    )
    final_metadata = source.lstat()
    if os.name == "posix" and final_metadata.st_nlink != 1:
        raise PrivateFileError("Device inventory file must not have hard links.")
    return text


class FixtureInventoryAdapter:
    def __init__(self, text: str) -> None:
        self.text = text

    def probe_capabilities(self) -> DiscoverySource:
        return DiscoverySource(
            name="fixture",
            kind="fixture",
            state=STATE_SUPPORTED,
            confidence="fixture",
        )

    def collect(self) -> str:
        return self.text

    def parse(self, collected: str) -> DiscoveryResult:
        return parse_fixture_inventory(collected)


class ContractPendingAdapter:
    """Documented placeholder for the future hardware-confirmed vendor adapter."""

    def probe_capabilities(self) -> DiscoverySource:
        return DiscoverySource(
            name="keenetic-netcraze",
            kind="vendor",
            state=STATE_CONTRACT_UNVERIFIED,
            confidence="hardware_confirmation_required",
        )

    def collect(self) -> str:
        raise DeviceDiscoveryError(
            "Keenetic/Netcraze device discovery contract is not hardware-confirmed.",
            state=STATE_CONTRACT_UNVERIFIED,
        )

    def parse(self, collected: str) -> DiscoveryResult:
        del collected
        raise DeviceDiscoveryError(
            "Keenetic/Netcraze device discovery contract is not hardware-confirmed.",
            state=STATE_CONTRACT_UNVERIFIED,
        )

    def result(self) -> DiscoveryResult:
        return DiscoveryResult(
            adapter_state=STATE_CONTRACT_UNVERIFIED,
            sources=(self.probe_capabilities(),),
            devices=(),
            errors=("hardware confirmation required before executing a vendor adapter",),
        )


def discover_from_inventory_text(text: str) -> DiscoveryResult:
    adapter = FixtureInventoryAdapter(text)
    return adapter.parse(adapter.collect())


def load_result_from_inventory_file(path: Path) -> DiscoveryResult:
    try:
        return discover_from_inventory_text(read_private_inventory_file(path))
    except PrivateFileTooLargeError:
        raise DeviceDiscoveryError("Device inventory file is too large.", STATE_OUTPUT_TOO_LARGE) from None
    except PrivateFileEncodingError:
        raise DeviceDiscoveryError("Device inventory file must contain UTF-8 text.", STATE_MALFORMED_OUTPUT) from None
    except PrivateFileError as exc:
        raise DeviceDiscoveryError(str(exc), STATE_PERMISSION_DENIED) from None


def _format_addresses(addresses: Sequence[str]) -> str:
    return ", ".join(addresses) if addresses else "no address"


def _format_connection(device: NormalizedDevice) -> str:
    if device.connection_type == "wifi":
        parts = ["Wi-Fi"]
        if device.wifi_band:
            parts.append(device.wifi_band)
        if device.interface:
            parts.append("(%s)" % device.interface)
        return " ".join(parts)
    if device.connection_type == "ethernet":
        return "Ethernet%s" % (" (%s)" % device.interface if device.interface else "")
    return "unknown link"


def render_text_inventory(result: DiscoveryResult) -> str:
    lines = ["Known local devices:"]
    for index, device in enumerate(result.devices, start=1):
        suffix = ""
        if not device.selectable and device.selection_block_reason:
            suffix = " [%s]" % device.selection_block_reason
        if device.existing_policy:
            suffix += " [policy: %s]" % device.existing_policy
        lines.append(
            "%d. %s - %s - %s - %s%s"
            % (
                index,
                device.display_name,
                _format_addresses(device.addresses),
                _format_connection(device),
                device.online_state,
                suffix,
            )
        )
    lines.append("0. Do not assign a device now")
    if result.errors:
        lines.append("Warnings:")
        for error in result.errors:
            lines.append("- %s" % error)
    return "\n".join(lines)


def _masked_addresses(addresses: Sequence[str]) -> List[Dict[str, str]]:
    masked = []
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        masked.append(
            {
                "family": "ipv%d" % parsed.version,
                "value": "masked",
                "sensitivity": SENSITIVITY_PUBLIC,
            }
        )
    return masked


def _hash_sensitive(value: Optional[str], salt: bytes) -> Optional[str]:
    if not value:
        return None
    digest = hmac.new(salt, value.encode("utf-8"), hashlib.sha256).hexdigest()
    return "hmac-sha256:%s" % digest[:24]


def result_to_jsonable(
    result: DiscoveryResult,
    *,
    public_evidence: bool = False,
    redaction_salt: Optional[str] = None,
) -> Dict[str, Any]:
    if public_evidence:
        salt = redaction_salt.encode("utf-8") if redaction_salt is not None else secrets.token_bytes(32)
        devices = []
        for index, device in enumerate(result.devices, start=1):
            devices.append(
                {
                    "record_id": "public-device-%d" % index,
                    "local_record_id_hash": _hash_sensitive(device.record_id, salt),
                    "display_name": "device-%d" % index,
                    "display_name_sensitivity": SENSITIVITY_PUBLIC,
                    "addresses": _masked_addresses(device.addresses),
                    "stable_identifier_hash": _hash_sensitive(device.stable_identifier, salt),
                    "online_state": device.online_state,
                    "connection_type": device.connection_type,
                    "source_types": list(device.sources),
                    "selectable": device.selectable,
                    "stale": device.stale,
                }
            )
        return {
            "schema": DISCOVERY_SCHEMA,
            "sensitivity": SENSITIVITY_PUBLIC,
            "redaction_note": "Redaction masks local fields for public evidence; it is not an anonymity guarantee.",
            "adapter_state": result.adapter_state,
            "device_count": len(result.devices),
            "sources": [source.__dict__ for source in result.sources],
            "devices": devices,
            "errors": list(result.errors),
        }

    return {
        "schema": DISCOVERY_SCHEMA,
        "sensitivity": SENSITIVITY_LOCAL,
        "adapter_state": result.adapter_state,
        "generated_at": result.generated_at,
        "sources": [source.__dict__ for source in result.sources],
        "devices": [
            {
                "record_id": device.record_id,
                "display_name": device.display_name,
                "display_name_sensitivity": SENSITIVITY_LOCAL,
                "addresses": list(device.addresses),
                "addresses_sensitivity": SENSITIVITY_LOCAL,
                "stable_identifier": device.stable_identifier,
                "stable_identifier_type": device.stable_identifier_type,
                "stable_identifier_sensitivity": SENSITIVITY_LOCAL if device.stable_identifier else None,
                "online_state": device.online_state,
                "connection_type": device.connection_type,
                "wifi_band": device.wifi_band,
                "interface": device.interface,
                "existing_policy": device.existing_policy,
                "sources": list(device.sources),
                "stale": device.stale,
                "selectable": device.selectable,
                "selection_block_reason": device.selection_block_reason,
                "conflict": device.conflict,
            }
            for device in result.devices
        ],
        "errors": list(result.errors),
    }


def render_json(
    result: DiscoveryResult,
    *,
    public_evidence: bool = False,
    redaction_salt: Optional[str] = None,
) -> str:
    return json.dumps(
        result_to_jsonable(
            result,
            public_evidence=public_evidence,
            redaction_salt=redaction_salt,
        ),
        indent=2,
        sort_keys=True,
    )


def selection_token(device: NormalizedDevice) -> str:
    seed = "%s|%s|%s" % (
        device.record_id,
        device.stable_identifier_type or "",
        device.stable_identifier or "",
    )
    return "routerkit-device-selection-v1:%s" % _stable_hash(seed, length=32)


def select_device(
    result: DiscoveryResult,
    choice: Optional[int],
) -> DeviceSelection:
    if choice is None or choice == 0:
        return DeviceSelection(selected=False, reason="no device assignment")
    if choice < 0 or choice > len(result.devices):
        raise DeviceSelectionError("Device selection index is out of range.")
    device = result.devices[choice - 1]
    if not device.selectable:
        raise DeviceSelectionError(
            "Device cannot be selected: %s." % (device.selection_block_reason or "ambiguous device"),
        )
    return DeviceSelection(
        selected=True,
        token=selection_token(device),
        device=device,
        reason="explicit device selected",
    )


def prompt_for_selection(result: DiscoveryResult, input_fn=input) -> DeviceSelection:
    try:
        value = input_fn("Select device number [0]: ")
    except EOFError:
        return select_device(result, 0)
    if value is None or value.strip() == "":
        return select_device(result, 0)
    try:
        choice = int(value.strip())
    except ValueError:
        raise DeviceSelectionError("Device selection must be a number.") from None
    return select_device(result, choice)


def render_selection(selection: DeviceSelection) -> str:
    if not selection.selected:
        return "Device selection: no device assignment."
    assert selection.device is not None
    return (
        "Device selection: %s (%s).\n"
        "Selection token: %s\n"
        "No policy or device assignment was written."
        % (
            selection.device.display_name,
            selection.device.record_id,
            selection.token,
        )
    )


def _load_result_for_args(args: argparse.Namespace) -> DiscoveryResult:
    if args.inventory_file:
        return load_result_from_inventory_file(Path(args.inventory_file))
    return ContractPendingAdapter().result()


def _write_line(stream: Any, text: str) -> None:
    stream.write(text + "\n")


def run_setup_selection_stage(
    *,
    inventory_file: Optional[str],
    choice: Optional[int],
    input_fn=input,
    output: Any = None,
    error: Any = None,
) -> Tuple[int, Optional[DeviceSelection]]:
    if output is None:
        output = sys.stdout
    if error is None:
        error = sys.stderr
    if not inventory_file:
        _write_line(
            error,
            "routerkit: device discovery contract is pending; pass --device-inventory-file only for fixture/offline validation.",
        )
        return 3, None
    try:
        result = load_result_from_inventory_file(Path(inventory_file))
        _write_line(output, render_text_inventory(result))
        selection = select_device(result, choice) if choice is not None else prompt_for_selection(result, input_fn=input_fn)
    except DeviceDiscoveryError as exc:
        _write_line(error, "routerkit: %s" % exc)
        return 2 if exc.state in (STATE_MALFORMED_OUTPUT, STATE_UNSUPPORTED) else 3, None
    _write_line(output, render_selection(selection))
    _write_line(output, "No Netcraze policy/device write occurred; #15 remains the write boundary.")
    return 0, selection


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only RouterKit local-device discovery.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "mode",
        choices=("status", "discover", "select"),
        nargs="?",
        default="status",
        help="status, discover, or select; default: status.",
    )
    parser.add_argument(
        "--inventory-file",
        metavar="PATH",
        help="Protected owner-only synthetic fixture inventory for offline validation.",
    )
    parser.add_argument("--json", action="store_true", help="Render JSON output.")
    parser.add_argument(
        "--public-evidence",
        action="store_true",
        help="Render redacted public-evidence JSON.",
    )
    parser.add_argument(
        "--redaction-salt",
        help="Caller-provided salt for deterministic public-evidence hashes.",
    )
    parser.add_argument(
        "--choice",
        type=int,
        help="Non-interactive selection index for select mode; 0 means no assignment.",
    )
    return parser.parse_args(argv)


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    input_fn=input,
    output: Any = None,
    error: Any = None,
) -> int:
    if output is None:
        output = sys.stdout
    if error is None:
        error = sys.stderr
    args = parse_args(argv)
    try:
        result = _load_result_for_args(args)
    except DeviceDiscoveryError as exc:
        _write_line(error, "routerkit: %s" % exc)
        return 2 if exc.state == STATE_MALFORMED_OUTPUT else 3

    if args.mode == "status":
        if args.json:
            _write_line(output, render_json(result, public_evidence=args.public_evidence, redaction_salt=args.redaction_salt))
        else:
            _write_line(output, "Device discovery adapter state: %s" % result.adapter_state)
            if result.adapter_state == STATE_CONTRACT_UNVERIFIED:
                _write_line(output, "Vendor client-enumeration contract requires hardware confirmation.")
        return 0

    if result.adapter_state == STATE_CONTRACT_UNVERIFIED:
        _write_line(
            error,
            "routerkit: device discovery contract is pending; use --inventory-file for fixture/offline validation.",
        )
        return 3

    if args.mode == "discover":
        if args.json or args.public_evidence:
            _write_line(output, render_json(result, public_evidence=args.public_evidence, redaction_salt=args.redaction_salt))
        else:
            _write_line(output, render_text_inventory(result))
        return 0

    if args.mode == "select":
        try:
            selection = select_device(result, args.choice) if args.choice is not None else prompt_for_selection(result, input_fn=input_fn)
        except DeviceDiscoveryError as exc:
            _write_line(error, "routerkit: %s" % exc)
            return 2
        if args.json:
            _write_line(output, json.dumps(selection.to_json(), indent=2, sort_keys=True))
        else:
            _write_line(output, render_text_inventory(result))
            _write_line(output, render_selection(selection))
        return 0

    raise AssertionError("unhandled mode")


if __name__ == "__main__":
    raise SystemExit(main())
