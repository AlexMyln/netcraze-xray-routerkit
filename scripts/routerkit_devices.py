#!/usr/bin/env python3
"""Fixture-first read-only local-device discovery primitives for RouterKit."""

from __future__ import annotations

import argparse
import errno
import hashlib
import hmac
import ipaddress
import json
import os
import queue
import re
import secrets
import signal
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

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
ADAPTER_STATES = (
    STATE_SUPPORTED,
    STATE_UNSUPPORTED,
    STATE_CONTRACT_UNVERIFIED,
    STATE_MALFORMED_OUTPUT,
    STATE_PERMISSION_DENIED,
    STATE_TIMEOUT,
    STATE_OUTPUT_TOO_LARGE,
    STATE_SOURCE_MISSING,
)
SOURCE_KINDS = (
    "dhcp_leases",
    "wifi_associations",
    "ethernet_fdb",
    "policy_bindings",
    "fixture",
    "vendor",
)
CONFIDENCE_VALUES = (
    "fixture",
    "official_documented",
    "official_inferred",
    "secondary_corroborated",
    "hardware_confirmation_required",
)
ASSIGNMENT_TRUSTED_IDENTIFIER_TYPES = ("mac", "router_id")
SENSITIVITY_LOCAL = "local_sensitive"
SENSITIVITY_PUBLIC = "public_evidence_redacted"

MAX_SOURCE_COUNT = 16
MAX_RECORDS_PER_SOURCE = 256
MAX_TOTAL_RECORDS = 512
MAX_NORMALIZED_DEVICES = 512
MAX_ADDRESSES_PER_RECORD = 16
MAX_ADDRESSES_PER_DEVICE = 32
MAX_SOURCE_NAME_LENGTH = 64
MAX_DISPLAY_NAME_LENGTH = 128
MAX_IDENTIFIER_LENGTH = 128
MAX_SOURCE_RECORD_ID_LENGTH = 128
MAX_OPTIONAL_FIELD_LENGTH = 128
MAX_ERROR_COUNT = 64
COMMAND_TERM_GRACE_SECONDS = 0.25
COMMAND_READ_CHUNK_BYTES = 8192

_RECORD_KEYS = {
    "source_record_id",
    "display_name",
    "addresses",
    "stable_identifier",
    "stable_identifier_type",
    "vendor_record_id",
    "assignment_stable",
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


class DeviceInventorySchemaError(DeviceDiscoveryError):
    pass


class DeviceSelectionError(DeviceDiscoveryError):
    pass


class DeviceCliUsageError(DeviceDiscoveryError):
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
        if maximum_output_bytes < 0:
            raise CommandExecutionError(
                "Adapter command output limit is invalid.",
                state=STATE_UNSUPPORTED,
            )
        deadline = time.monotonic() + timeout_seconds
        stream_queue: "queue.Queue[Tuple[str, Optional[bytes]]]" = queue.Queue()
        stdout_chunks: List[bytes] = []
        stderr_chunks: List[bytes] = []
        total_output = 0
        process: Optional[subprocess.Popen[bytes]] = None

        def drain_stream(label: str, stream: Any) -> None:
            try:
                while True:
                    chunk = os.read(stream.fileno(), COMMAND_READ_CHUNK_BYTES)
                    if not chunk:
                        break
                    stream_queue.put((label, chunk))
            finally:
                stream.close()
                stream_queue.put((label, None))

        try:
            process = subprocess.Popen(
                list(argv_tuple),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=dict(env),
                shell=False,
                start_new_session=(os.name == "posix"),
            )
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

        assert process.stdout is not None
        assert process.stderr is not None
        threads = [
            threading.Thread(target=drain_stream, args=("stdout", process.stdout)),
            threading.Thread(target=drain_stream, args=("stderr", process.stderr)),
        ]
        for thread in threads:
            thread.daemon = True
            thread.start()

        completed_streams = set()
        failure_state: Optional[str] = None
        while len(completed_streams) < 2:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure_state = STATE_TIMEOUT
                self._terminate_process_group(process)
                break
            try:
                label, chunk = stream_queue.get(timeout=min(0.05, remaining))
            except queue.Empty:
                if process.poll() is not None and len(completed_streams) == 2:
                    break
                continue
            if chunk is None:
                completed_streams.add(label)
                continue
            total_output += len(chunk)
            if total_output > maximum_output_bytes:
                failure_state = STATE_OUTPUT_TOO_LARGE
                self._terminate_process_group(process)
                break
            if label == "stdout":
                stdout_chunks.append(chunk)
            else:
                stderr_chunks.append(chunk)

        if failure_state is None:
            try:
                returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                failure_state = STATE_TIMEOUT
                self._terminate_process_group(process)
                returncode = self._wait_reaped(process)
        else:
            returncode = self._wait_reaped(process)

        for thread in threads:
            thread.join(timeout=COMMAND_TERM_GRACE_SECONDS)

        if failure_state == STATE_TIMEOUT:
            raise CommandExecutionError(
                "Adapter command timed out.",
                state=STATE_TIMEOUT,
            )
        if failure_state == STATE_OUTPUT_TOO_LARGE:
            raise CommandExecutionError(
                "Adapter command output is too large.",
                state=STATE_OUTPUT_TOO_LARGE,
            )
        if returncode == 126:
            raise CommandExecutionError(
                "Adapter command permission denied.",
                state=STATE_PERMISSION_DENIED,
            )
        return CommandResult(
            argv=argv_tuple,
            returncode=int(returncode),
            stdout=b"".join(stdout_chunks),
            stderr=b"".join(stderr_chunks),
        )

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except OSError as exc:
            if exc.errno != errno.ESRCH:
                raise
        try:
            process.wait(timeout=COMMAND_TERM_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except OSError as exc:
            if exc.errno != errno.ESRCH:
                raise

    @staticmethod
    def _wait_reaped(process: subprocess.Popen[bytes]) -> int:
        while True:
            try:
                return int(process.wait(timeout=COMMAND_TERM_GRACE_SECONDS))
            except subprocess.TimeoutExpired:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except OSError as exc:
                    if exc.errno != errno.ESRCH:
                        raise


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
    assignment_stable: bool = False
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
            "record_id": self.device.record_id,
            "record_id_sensitivity": SENSITIVITY_LOCAL,
            "display_name": self.device.display_name,
            "display_name_sensitivity": SENSITIVITY_LOCAL,
            "selection_handle": "internal-only",
            "selection_handle_sensitivity": SENSITIVITY_LOCAL,
            "no_device_assignment": False,
        }


def _stable_hash(value: str, length: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _clean_text(
    value: Optional[Any],
    *,
    maximum_length: int = MAX_OPTIONAL_FIELD_LENGTH,
    description: str = "text field",
) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DeviceDiscoveryError("Device inventory contains an unsupported text field.")
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > maximum_length:
        raise DeviceInventorySchemaError("Device inventory %s exceeds the maximum length." % description)
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
    identifier = _clean_text(
        identifier,
        maximum_length=MAX_IDENTIFIER_LENGTH,
        description="identifier field",
    )
    identifier_type = (
        _clean_text(
            identifier_type,
            maximum_length=MAX_IDENTIFIER_LENGTH,
            description="identifier type field",
        )
        or "unknown"
    ).lower() if identifier else None
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
    if len(values) > MAX_ADDRESSES_PER_RECORD:
        raise DeviceInventorySchemaError("Device inventory contains too many addresses.")
    addresses = []
    for value in values:
        if not isinstance(value, str):
            raise DeviceDiscoveryError("Device inventory contains an unsupported address field.")
        try:
            addresses.append(str(ipaddress.ip_address(value.strip())))
        except ValueError:
            raise DeviceDiscoveryError("Device inventory contains an invalid address.") from None
    return tuple(sorted(set(addresses), key=lambda item: (ipaddress.ip_address(item).version, ipaddress.ip_address(item).packed)))


def _display_identity(record: RawDeviceRecord) -> Optional[str]:
    if record.stable_identifier:
        return "%s:%s" % (record.stable_identifier_type or "unknown", record.stable_identifier)
    if record.vendor_record_id:
        return "vendor_record_id:%s" % record.vendor_record_id
    return None


def _assignment_identity(record: RawDeviceRecord) -> Optional[str]:
    if record.stable_identifier and record.stable_identifier_type in ASSIGNMENT_TRUSTED_IDENTIFIER_TYPES:
        return "%s:%s" % (record.stable_identifier_type, record.stable_identifier)
    if record.assignment_stable and record.stable_identifier_type == "vendor_record_id" and record.stable_identifier:
        return "vendor_record_id:%s" % record.stable_identifier
    if record.assignment_stable and record.vendor_record_id:
        return "vendor_record_id:%s" % record.vendor_record_id
    return None


def _record_sort_key(record: RawDeviceRecord) -> Tuple[str, str, str]:
    return (
        record.source,
        record.source_record_id,
        _display_identity(record) or ",".join(record.addresses),
    )


def _record_group_key(record: RawDeviceRecord) -> str:
    identity = _display_identity(record)
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
    if len(records) > MAX_TOTAL_RECORDS:
        raise DeviceDiscoveryError("Device inventory contains too many records.")
    grouped: Dict[str, List[RawDeviceRecord]] = {}
    for record in sorted(records, key=_record_sort_key):
        grouped.setdefault(_record_group_key(record), []).append(record)

    vendor_identities: Dict[str, set] = {}
    for record in records:
        if record.vendor_record_id:
            vendor_identities.setdefault(record.vendor_record_id, set()).add(
                _display_identity(record) or "weak"
            )
    conflicting_vendor_ids = {
        vendor_id for vendor_id, identities in vendor_identities.items() if len(identities) > 1
    }

    devices: List[NormalizedDevice] = []
    for key in sorted(grouped):
        group = grouped[key]
        display_identity = _display_identity(group[0])
        trusted_identities = {_assignment_identity(record) for record in group}
        trusted_identities.discard(None)
        trusted_identity = next(iter(trusted_identities)) if len(trusted_identities) == 1 else None
        stable_identifier = group[0].stable_identifier
        stable_identifier_type = group[0].stable_identifier_type
        seed = display_identity or "|".join(
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
        if len(addresses) > MAX_ADDRESSES_PER_DEVICE:
            raise DeviceDiscoveryError("Device inventory contains too many addresses.")
        conflict = any(record.vendor_record_id in conflicting_vendor_ids for record in group)
        selectable = bool(trusted_identity and not conflict)
        reason = None
        if not display_identity:
            reason = "stable identifier unavailable"
        elif not trusted_identity:
            reason = "assignment-stable identifier unavailable"
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
    if len(sorted_devices) > MAX_NORMALIZED_DEVICES:
        raise DeviceDiscoveryError("Device inventory contains too many normalized devices.")
    sources = tuple(sorted({DiscoverySource(record.source, record.source_kind) for record in records}, key=lambda source: source.name))
    return DiscoveryResult(
        adapter_state=STATE_SUPPORTED if not source_errors else STATE_MALFORMED_OUTPUT,
        sources=sources,
        devices=sorted_devices,
        errors=tuple(source_errors[:MAX_ERROR_COUNT]),
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
    name = _clean_text(
        source.get("name"),
        maximum_length=MAX_SOURCE_NAME_LENGTH,
        description="source name field",
    )
    kind = _clean_text(
        source.get("kind"),
        maximum_length=MAX_OPTIONAL_FIELD_LENGTH,
        description="source kind field",
    )
    if not name or not kind:
        raise DeviceDiscoveryError("Device inventory source is missing required metadata.")
    if kind not in SOURCE_KINDS:
        raise DeviceDiscoveryError("Device inventory source kind is unsupported.")
    state = _clean_text(
        source.get("state"),
        maximum_length=MAX_OPTIONAL_FIELD_LENGTH,
        description="source state field",
    ) or STATE_SUPPORTED
    confidence = _clean_text(
        source.get("confidence"),
        maximum_length=MAX_OPTIONAL_FIELD_LENGTH,
        description="source confidence field",
    ) or "fixture"
    if state not in ADAPTER_STATES:
        raise DeviceDiscoveryError("Device inventory source state is unsupported.")
    if confidence not in CONFIDENCE_VALUES:
        raise DeviceDiscoveryError("Device inventory source confidence is unsupported.")
    summary = DiscoverySource(name=name, kind=kind, state=state, confidence=confidence)
    if state != STATE_SUPPORTED:
        return summary, [], ["source %s reported %s" % (name, state)]
    records_value = source.get("records")
    if not isinstance(records_value, list):
        raise DeviceDiscoveryError("Device inventory source has no record list.")
    if len(records_value) > MAX_RECORDS_PER_SOURCE:
        raise DeviceDiscoveryError("Device inventory source contains too many records.")
    records = []
    errors = []
    for index, item in enumerate(records_value):
        try:
            records.append(_parse_record(name, kind, index, item))
        except DeviceInventorySchemaError:
            raise
        except DeviceDiscoveryError:
            if len(errors) < MAX_ERROR_COUNT:
                errors.append("malformed record skipped from source %s" % name)
    return summary, records, errors


def _parse_record(source_name: str, source_kind: str, index: int, item: Any) -> RawDeviceRecord:
    if not isinstance(item, dict):
        raise DeviceDiscoveryError("Device inventory contains a malformed record.")
    if set(item) - _RECORD_KEYS:
        raise DeviceDiscoveryError("Device inventory contains unsupported record schema.")
    source_record_id = _clean_text(
        item.get("source_record_id"),
        maximum_length=MAX_SOURCE_RECORD_ID_LENGTH,
        description="source record id field",
    ) or "%s-%d" % (source_name, index + 1)
    stable_identifier, stable_identifier_type = _normalize_stable_identifier(
        item.get("stable_identifier"),
        item.get("stable_identifier_type"),
    )
    vendor_record_id = _clean_text(
        item.get("vendor_record_id"),
        maximum_length=MAX_IDENTIFIER_LENGTH,
        description="vendor record id field",
    )
    assignment_stable = item.get("assignment_stable", False)
    if not isinstance(assignment_stable, bool):
        raise DeviceDiscoveryError("Device inventory contains an unsupported assignment-stable field.")
    online_state = (
        _clean_text(
            item.get("online_state"),
            maximum_length=MAX_OPTIONAL_FIELD_LENGTH,
            description="online state field",
        )
        or "unknown"
    ).lower()
    connection_type = (
        _clean_text(
            item.get("connection_type"),
            maximum_length=MAX_OPTIONAL_FIELD_LENGTH,
            description="connection type field",
        )
        or "unknown"
    ).lower()
    if online_state not in ONLINE_STATES:
        raise DeviceDiscoveryError("Device inventory contains an unsupported online state.")
    if connection_type not in CONNECTION_TYPES:
        raise DeviceDiscoveryError("Device inventory contains an unsupported connection type.")
    addresses = _normalize_addresses(item.get("addresses", []))
    stale = item.get("stale", False)
    if not isinstance(stale, bool):
        raise DeviceDiscoveryError("Device inventory contains an unsupported stale field.")
    if not (stable_identifier or vendor_record_id or addresses):
        raise DeviceDiscoveryError("Device inventory record has no assignable or display identity.")
    return RawDeviceRecord(
        source=source_name,
        source_kind=source_kind,
        source_record_id=source_record_id,
        display_name=_clean_text(
            item.get("display_name"),
            maximum_length=MAX_DISPLAY_NAME_LENGTH,
            description="display name field",
        ),
        addresses=addresses,
        stable_identifier=stable_identifier,
        stable_identifier_type=stable_identifier_type,
        vendor_record_id=vendor_record_id,
        assignment_stable=assignment_stable,
        online_state=online_state,
        connection_type=connection_type,
        wifi_band=_clean_text(item.get("wifi_band"), description="wifi band field"),
        interface=_clean_text(item.get("interface"), description="interface field"),
        existing_policy=_clean_text(item.get("existing_policy"), description="policy field"),
        last_seen=_clean_text(item.get("last_seen"), description="last-seen field"),
        stale=stale,
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
    if len(sources_value) > MAX_SOURCE_COUNT:
        raise DeviceDiscoveryError("Device inventory contains too many sources.")
    all_sources: List[DiscoverySource] = []
    records: List[RawDeviceRecord] = []
    errors: List[str] = []
    for source_value in sources_value:
        source, source_records, source_errors = _parse_source(source_value)
        all_sources.append(source)
        records.extend(source_records)
        if len(records) > MAX_TOTAL_RECORDS:
            raise DeviceDiscoveryError("Device inventory contains too many records.")
        for source_error in source_errors:
            if len(errors) < MAX_ERROR_COUNT:
                errors.append(source_error)
    result = normalize_records(records, source_errors=errors)
    return DiscoveryResult(
        adapter_state=result.adapter_state,
        sources=tuple(all_sources),
        devices=result.devices,
        errors=result.errors,
        generated_at=_clean_text(document.get("generated_at"), description="generated-at field"),
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


def _public_error_code(error: str) -> str:
    if "malformed record skipped" in error:
        return "malformed_record_skipped"
    if STATE_SOURCE_MISSING in error:
        return STATE_SOURCE_MISSING
    if STATE_PERMISSION_DENIED in error:
        return STATE_PERMISSION_DENIED
    if STATE_TIMEOUT in error:
        return STATE_TIMEOUT
    if STATE_OUTPUT_TOO_LARGE in error:
        return STATE_OUTPUT_TOO_LARGE
    if STATE_CONTRACT_UNVERIFIED in error or "hardware confirmation required" in error:
        return STATE_CONTRACT_UNVERIFIED
    return "source_error"


def _public_error_summary(errors: Sequence[str]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for error in errors[:MAX_ERROR_COUNT]:
        code = _public_error_code(error)
        counts[code] = counts.get(code, 0) + 1
    return [{"code": code, "count": counts[code]} for code in sorted(counts)]


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
                    "selection_readiness": selection_readiness(result)[0],
                    "stale": device.stale,
                }
            )
        return {
            "schema": DISCOVERY_SCHEMA,
            "sensitivity": SENSITIVITY_PUBLIC,
            "redaction_note": "Redaction masks local fields for public evidence; it is not an anonymity guarantee.",
            "adapter_state": result.adapter_state,
            "device_count": len(result.devices),
            "sources": [
                {
                    "source_index": index,
                    "kind": source.kind,
                    "state": source.state,
                    "confidence": source.confidence,
                }
                for index, source in enumerate(result.sources, start=1)
            ],
            "devices": devices,
            "error_count": len(result.errors),
            "errors": _public_error_summary(result.errors),
        }

    return {
        "schema": DISCOVERY_SCHEMA,
        "sensitivity": SENSITIVITY_LOCAL,
        "adapter_state": result.adapter_state,
        "generated_at": result.generated_at,
        "sources_sensitivity": SENSITIVITY_LOCAL,
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
        "errors_sensitivity": SENSITIVITY_LOCAL if result.errors else None,
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


def selection_readiness(result: DiscoveryResult) -> Tuple[bool, str]:
    if result.adapter_state != STATE_SUPPORTED:
        return False, "device evidence is not complete and trusted"
    if result.errors:
        return False, "device evidence contains sanitized adapter errors"
    if any(source.state != STATE_SUPPORTED for source in result.sources):
        return False, "device evidence includes degraded sources"
    return True, "device evidence is complete and trusted"


def selection_token(device: NormalizedDevice, token_factory: Callable[[int], str] = secrets.token_urlsafe) -> str:
    del device
    return "routerkit-device-selection-v1:%s" % token_factory(32)


def select_device(
    result: DiscoveryResult,
    choice: Optional[int],
    *,
    token_factory: Callable[[int], str] = secrets.token_urlsafe,
) -> DeviceSelection:
    if choice is None or choice == 0:
        return DeviceSelection(selected=False, reason="no device assignment")
    if choice < 0:
        raise DeviceSelectionError("Device selection index is out of range.")
    ready, reason = selection_readiness(result)
    if not ready:
        raise DeviceSelectionError("Device selection is unavailable: %s." % reason)
    if choice > len(result.devices):
        raise DeviceSelectionError("Device selection index is out of range.")
    device = result.devices[choice - 1]
    if not device.selectable:
        raise DeviceSelectionError(
            "Device cannot be selected: %s." % (device.selection_block_reason or "ambiguous device"),
        )
    return DeviceSelection(
        selected=True,
        token=selection_token(device, token_factory=token_factory),
        device=device,
        reason="explicit device selected",
    )


def prompt_for_selection(
    result: DiscoveryResult,
    input_fn=input,
    *,
    token_factory: Callable[[int], str] = secrets.token_urlsafe,
) -> DeviceSelection:
    try:
        value = input_fn("Select device number [0]: ")
    except EOFError:
        return select_device(result, 0, token_factory=token_factory)
    if value is None or value.strip() == "":
        return select_device(result, 0, token_factory=token_factory)
    try:
        choice = int(value.strip())
    except ValueError:
        raise DeviceSelectionError("Device selection must be a number.") from None
    return select_device(result, choice, token_factory=token_factory)


def render_selection(selection: DeviceSelection) -> str:
    if not selection.selected:
        return "Device selection: no device assignment."
    assert selection.device is not None
    return (
        "Device selection: %s (%s).\n"
        "No policy or device assignment was written."
        % (
            selection.device.display_name,
            selection.device.record_id,
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


def accept_read_only_device_selection(selection: Optional[DeviceSelection]) -> None:
    """Reviewed no-op boundary for future #15 assignment planning."""
    del selection


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only RouterKit local-device discovery.",
        epilog=(
            "Argument contract: status accepts only --json; discover accepts inventory, "
            "--json, --public-evidence, and --redaction-salt; select accepts inventory, "
            "--choice, and --json. --public-evidence implies JSON and is never valid for select."
        ),
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
        help="Discover mode only: render redacted public-evidence JSON.",
    )
    parser.add_argument(
        "--redaction-salt",
        help="Discover public-evidence only: caller-provided salt for deterministic hashes.",
    )
    parser.add_argument(
        "--choice",
        type=int,
        help="Select mode only: non-interactive selection index; 0 means no assignment.",
    )
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    if args.mode == "status":
        if args.inventory_file:
            raise DeviceCliUsageError("status does not accept --inventory-file.")
        if args.choice is not None:
            raise DeviceCliUsageError("status does not accept --choice.")
        if args.public_evidence:
            raise DeviceCliUsageError("status does not accept --public-evidence.")
        if args.redaction_salt:
            raise DeviceCliUsageError("status does not accept --redaction-salt.")
        return
    if args.mode == "discover":
        if args.choice is not None:
            raise DeviceCliUsageError("discover does not accept --choice.")
        if args.redaction_salt and not args.public_evidence:
            raise DeviceCliUsageError("--redaction-salt requires --public-evidence.")
        if args.public_evidence:
            args.json = True
        return
    if args.mode == "select":
        if args.public_evidence:
            raise DeviceCliUsageError("select does not accept --public-evidence.")
        if args.redaction_salt:
            raise DeviceCliUsageError("select does not accept --redaction-salt.")
        return
    raise DeviceCliUsageError("unsupported device discovery mode.")


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
        validate_args(args)
    except DeviceCliUsageError as exc:
        _write_line(error, "routerkit: %s" % exc)
        return 2
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
