#!/usr/bin/env python3
"""Persistent local direct-routing overrides for RouterKit Xray profiles.

The feature is provider-agnostic: selected domain suffixes are routed through
RouterKit's existing ``direct``/``freedom`` outbound before the per-profile
VLESS catch-all rules.  The normal VPN profile remains unchanged for all other
traffic.

The persistent desired state lives at
``/opt/etc/routerkit/routing-overrides.json``.  ``apply`` updates the active
Xray routing fragment transactionally and restarts only Xray.  ``reconcile``
is the non-restarting install hook used after a normal RouterKit config copy so
future setup/regeneration does not discard previously selected local routing
overrides.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from routerkit_private_io import (
    PrivateFileError,
    ensure_private_directory,
    read_owner_only_text_file,
    write_private_bytes_atomic,
)


OVERRIDES_SCHEMA = "routerkit.routing-overrides.v1"
SERVICE_SCHEMA = "routerkit.routing-service.v1"
MAX_STATE_BYTES = 64 * 1024
MAX_CONFIG_BYTES = 2 * 1024 * 1024
MAX_SERVICE_BYTES = 64 * 1024
MAX_SERVICE_COUNT = 64
MAX_DOMAIN_COUNT = 512
SERVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
ENTWARE_PATH = "/opt/sbin:/opt/bin:/usr/sbin:/usr/bin:/sbin:/bin"
PROBE_URL = "https://api.ipify.org"


class RoutingError(Exception):
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class RoutingOverrides:
    direct_services: Tuple[str, ...] = ()
    direct_domains: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema": OVERRIDES_SCHEMA,
            "direct_services": list(self.direct_services),
            "direct_domains": list(self.direct_domains),
        }


@dataclass(frozen=True)
class RoutingPlan:
    current: RoutingOverrides
    desired: RoutingOverrides
    direct_domains: Tuple[str, ...]
    inferred_existing_state: bool
    routing_change_required: bool
    state_change_required: bool

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema": "routerkit.routing-plan.v1",
            "current": self.current.to_dict(),
            "desired": self.desired.to_dict(),
            "expanded_direct_domains": list(self.direct_domains),
            "inferred_existing_state": self.inferred_existing_state,
            "routing_change_required": self.routing_change_required,
            "state_change_required": self.state_change_required,
        }


def repo_root_from_module() -> Path:
    return Path(__file__).resolve().parents[1]


def state_path(target_root: Path) -> Path:
    return Path(target_root) / "etc" / "routerkit" / "routing-overrides.json"


def config_dir(target_root: Path) -> Path:
    return Path(target_root) / "etc" / "xray" / "configs"


def init_path(target_root: Path) -> Path:
    return Path(target_root) / "etc" / "init.d" / "S23xray-direct"


def xray_path(target_root: Path) -> Path:
    return Path(target_root) / "sbin" / "xray"


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def normalize_service_name(value: str) -> str:
    name = str(value).strip().casefold()
    if not SERVICE_NAME_RE.fullmatch(name):
        raise RoutingError("Routing service name is invalid.", exit_code=2)
    return name


def normalize_domain_suffix(value: str) -> str:
    raw = str(value).strip().casefold()
    if raw.startswith("domain:"):
        raw = raw[len("domain:") :]
    raw = raw.rstrip(".")
    if not raw or len(raw) > 253:
        raise RoutingError("Direct-routing domain suffix is invalid.", exit_code=2)
    if any(token in raw for token in ("/", "\\", "*", "?", "#", "@", ":")):
        raise RoutingError("Direct-routing domain suffix is invalid.", exit_code=2)
    try:
        ascii_domain = raw.encode("idna").decode("ascii")
    except UnicodeError:
        raise RoutingError("Direct-routing domain suffix is invalid.", exit_code=2) from None
    try:
        ipaddress.ip_address(ascii_domain)
    except ValueError:
        pass
    else:
        raise RoutingError("Direct-routing entries must be domain suffixes, not IP addresses.", exit_code=2)
    labels = ascii_domain.split(".")
    if len(labels) < 2 or any(not LABEL_RE.fullmatch(label) for label in labels):
        raise RoutingError("Direct-routing domain suffix is invalid.", exit_code=2)
    return ascii_domain


def validate_overrides_value(value: object) -> RoutingOverrides:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "direct_services",
        "direct_domains",
    }:
        raise RoutingError("Routing overrides file is not recognized.")
    if value.get("schema") != OVERRIDES_SCHEMA:
        raise RoutingError("Routing overrides file is not recognized.")
    services = value.get("direct_services")
    domains = value.get("direct_domains")
    if not isinstance(services, list) or not isinstance(domains, list):
        raise RoutingError("Routing overrides file is not recognized.")
    if len(services) > MAX_SERVICE_COUNT or len(domains) > MAX_DOMAIN_COUNT:
        raise RoutingError("Routing overrides file exceeds the supported bounds.")
    normalized_services = tuple(sorted({normalize_service_name(item) for item in services}))
    normalized_domains = tuple(sorted({normalize_domain_suffix(item) for item in domains}))
    if len(normalized_services) != len(services) or len(normalized_domains) != len(domains):
        raise RoutingError("Routing overrides file contains duplicate or non-canonical entries.")
    return RoutingOverrides(normalized_services, normalized_domains)


def validate_overrides_text(text: str) -> None:
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        raise PrivateFileError("Routing overrides file is not recognized.") from None
    try:
        validate_overrides_value(value)
    except RoutingError:
        raise PrivateFileError("Routing overrides file is not recognized.") from None


def load_overrides(path: Path, *, missing_ok: bool = True) -> Optional[RoutingOverrides]:
    path = Path(path)
    try:
        text = read_owner_only_text_file(
            path,
            maximum_bytes=MAX_STATE_BYTES,
            description="Routing overrides file",
        )
    except PrivateFileError:
        try:
            path.lstat()
        except FileNotFoundError:
            if missing_ok:
                return None
        raise
    try:
        value = json.loads(text)
    except ValueError:
        raise RoutingError("Routing overrides file is not recognized.") from None
    return validate_overrides_value(value)


def write_overrides(path: Path, overrides: RoutingOverrides) -> None:
    path = Path(path)
    ensure_private_directory(path.parent, description="RouterKit routing state directory")
    payload = _json_bytes(overrides.to_dict())
    try:
        write_private_bytes_atomic(
            path,
            payload,
            maximum_bytes=MAX_STATE_BYTES,
            description="Routing overrides file",
            validate_existing_text=validate_overrides_text,
        )
    except PrivateFileError:
        raise RoutingError("Routing overrides state could not be published safely.") from None


def service_path(repo_root: Path, name: str) -> Path:
    return Path(repo_root) / "routing" / "services" / (normalize_service_name(name) + ".json")


def load_service_pack(repo_root: Path, name: str) -> Tuple[str, ...]:
    normalized_name = normalize_service_name(name)
    path = service_path(repo_root, normalized_name)
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise OSError
        if metadata.st_size > MAX_SERVICE_BYTES:
            raise OSError
        text = path.read_text(encoding="utf-8")
        value = json.loads(text)
    except (OSError, UnicodeError, ValueError):
        raise RoutingError("Requested routing service pack is unavailable or invalid.", exit_code=2) from None
    if not isinstance(value, dict) or set(value) != {"schema", "name", "domains"}:
        raise RoutingError("Requested routing service pack is unavailable or invalid.", exit_code=2)
    if value.get("schema") != SERVICE_SCHEMA or value.get("name") != normalized_name:
        raise RoutingError("Requested routing service pack is unavailable or invalid.", exit_code=2)
    domains = value.get("domains")
    if not isinstance(domains, list) or not domains or len(domains) > MAX_DOMAIN_COUNT:
        raise RoutingError("Requested routing service pack is unavailable or invalid.", exit_code=2)
    normalized = tuple(normalize_domain_suffix(item) for item in domains)
    if len(set(normalized)) != len(normalized):
        raise RoutingError("Requested routing service pack contains duplicate domains.", exit_code=2)
    return normalized


def available_service_names(repo_root: Path) -> Tuple[str, ...]:
    directory = Path(repo_root) / "routing" / "services"
    try:
        entries = list(directory.iterdir())
    except OSError:
        return ()
    result = []
    for entry in entries[: MAX_SERVICE_COUNT + 1]:
        if entry.suffix != ".json":
            continue
        name = entry.stem
        try:
            load_service_pack(repo_root, name)
        except RoutingError:
            continue
        result.append(name)
    return tuple(sorted(set(result)))


def expand_direct_domains(repo_root: Path, overrides: RoutingOverrides) -> Tuple[str, ...]:
    result: List[str] = []
    seen = set()
    for service in overrides.direct_services:
        for domain in load_service_pack(repo_root, service):
            if domain not in seen:
                result.append(domain)
                seen.add(domain)
    for domain in overrides.direct_domains:
        if domain not in seen:
            result.append(domain)
            seen.add(domain)
    if len(result) > MAX_DOMAIN_COUNT:
        raise RoutingError("Expanded direct-routing domain set exceeds the supported bound.")
    return tuple(result)


def mutate_overrides(
    current: RoutingOverrides,
    repo_root: Path,
    *,
    add_services: Sequence[str] = (),
    remove_services: Sequence[str] = (),
    add_domains: Sequence[str] = (),
    remove_domains: Sequence[str] = (),
) -> RoutingOverrides:
    services = set(current.direct_services)
    domains = set(current.direct_domains)
    for value in add_services:
        name = normalize_service_name(value)
        load_service_pack(repo_root, name)
        services.add(name)
    for value in remove_services:
        services.discard(normalize_service_name(value))
    for value in add_domains:
        domains.add(normalize_domain_suffix(value))
    for value in remove_domains:
        domains.discard(normalize_domain_suffix(value))
    if len(services) > MAX_SERVICE_COUNT or len(domains) > MAX_DOMAIN_COUNT:
        raise RoutingError("Routing override request exceeds the supported bounds.", exit_code=2)
    return RoutingOverrides(tuple(sorted(services)), tuple(sorted(domains)))


def direct_rule(inbound_tags: Sequence[str], domains: Sequence[str]) -> Dict[str, object]:
    tags = tuple(str(item) for item in inbound_tags)
    if not tags or len(tags) != len(set(tags)):
        raise RoutingError("RouterKit SOCKS inbound tags are missing or ambiguous.")
    normalized_domains = tuple(normalize_domain_suffix(item) for item in domains)
    if not normalized_domains:
        raise RoutingError("Direct-routing rule requires at least one domain.")
    return {
        "type": "field",
        "inboundTag": list(tags),
        "domain": ["domain:" + item for item in normalized_domains],
        "outboundTag": "direct",
    }


def extract_routerkit_inbounds(value: object) -> Tuple[str, ...]:
    if not isinstance(value, dict) or not isinstance(value.get("inbounds"), list):
        raise RoutingError("Active Xray inbound configuration is not recognized.")
    tags = []
    for inbound in value["inbounds"]:
        if not isinstance(inbound, dict):
            continue
        if inbound.get("protocol") != "socks":
            continue
        if inbound.get("listen") not in ("127.0.0.1", "::1"):
            continue
        tag = inbound.get("tag")
        port = inbound.get("port")
        if not isinstance(tag, str) or not tag.startswith("socks-"):
            continue
        if not isinstance(port, int) or isinstance(port, bool):
            continue
        tags.append(tag)
    if not tags or len(tags) != len(set(tags)):
        raise RoutingError("Active RouterKit SOCKS inbound set is missing or ambiguous.")
    return tuple(tags)


def extract_routerkit_ports(value: object) -> Tuple[int, ...]:
    if not isinstance(value, dict) or not isinstance(value.get("inbounds"), list):
        raise RoutingError("Active Xray inbound configuration is not recognized.")
    ports = []
    for inbound in value["inbounds"]:
        if not isinstance(inbound, dict):
            continue
        if inbound.get("protocol") != "socks" or inbound.get("listen") not in ("127.0.0.1", "::1"):
            continue
        port = inbound.get("port")
        if isinstance(port, int) and not isinstance(port, bool):
            ports.append(port)
    if not ports or len(ports) != len(set(ports)):
        raise RoutingError("Active RouterKit SOCKS port set is missing or ambiguous.")
    return tuple(ports)


def validate_direct_outbound(value: object) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("outbounds"), list):
        raise RoutingError("Active Xray outbound configuration is not recognized.")
    matches = [
        item
        for item in value["outbounds"]
        if isinstance(item, dict) and item.get("tag") == "direct"
    ]
    if len(matches) != 1 or matches[0].get("protocol") != "freedom":
        raise RoutingError("RouterKit requires exactly one existing direct/freedom outbound.")


def _routing_rules(value: object) -> List[object]:
    if not isinstance(value, dict):
        raise RoutingError("Active Xray routing configuration is not recognized.")
    routing = value.get("routing")
    if not isinstance(routing, dict) or not isinstance(routing.get("rules"), list):
        raise RoutingError("Active Xray routing configuration is not recognized.")
    return list(routing["rules"])


def _domain_rule_values(rule: object, inbound_tags: Sequence[str]) -> Optional[Tuple[str, ...]]:
    if not isinstance(rule, dict) or set(rule) != {"type", "inboundTag", "domain", "outboundTag"}:
        return None
    if rule.get("type") != "field" or rule.get("outboundTag") != "direct":
        return None
    if rule.get("inboundTag") != list(inbound_tags):
        return None
    domains = rule.get("domain")
    if not isinstance(domains, list) or not domains:
        return None
    normalized = []
    for item in domains:
        if not isinstance(item, str) or not item.startswith("domain:"):
            return None
        try:
            normalized.append(normalize_domain_suffix(item))
        except RoutingError:
            return None
    if len(normalized) != len(set(normalized)):
        return None
    return tuple(normalized)


def infer_known_service_state(
    repo_root: Path,
    routing_value: object,
    inbound_tags: Sequence[str],
) -> Optional[RoutingOverrides]:
    rules = _routing_rules(routing_value)
    if not rules:
        return None
    domains = _domain_rule_values(rules[0], inbound_tags)
    if domains is None:
        return None
    for service in available_service_names(repo_root):
        if domains == load_service_pack(repo_root, service):
            return RoutingOverrides((service,), ())
    return None


def reconcile_routing_document(
    routing_value: object,
    inbound_tags: Sequence[str],
    *,
    current_domains: Sequence[str],
    desired_domains: Sequence[str],
) -> Tuple[Dict[str, object], bool]:
    if not isinstance(routing_value, dict):
        raise RoutingError("Active Xray routing configuration is not recognized.")
    routing = routing_value.get("routing")
    if not isinstance(routing, dict):
        raise RoutingError("Active Xray routing configuration is not recognized.")
    rules = _routing_rules(routing_value)
    expected_current = direct_rule(inbound_tags, current_domains) if current_domains else None

    if rules and isinstance(rules[0], dict) and rules[0].get("outboundTag") == "direct":
        if expected_current is None or rules[0] != expected_current:
            raise RoutingError(
                "The first Xray direct rule is not the RouterKit-managed routing override; refusing to overwrite it."
            )
        rules = rules[1:]
    elif expected_current is not None:
        # The persistent state is authoritative.  A missing managed rule is a
        # recoverable drift case (for example immediately after setup copied a
        # freshly generated 05_routing.json); reconcile restores it.
        pass

    desired = list(rules)
    if desired_domains:
        desired.insert(0, direct_rule(inbound_tags, desired_domains))

    updated = dict(routing_value)
    updated_routing = dict(routing)
    updated_routing["rules"] = desired
    updated["routing"] = updated_routing
    return updated, updated != routing_value


def _read_json_private(path: Path, *, description: str) -> object:
    try:
        text = read_owner_only_text_file(
            Path(path),
            maximum_bytes=MAX_CONFIG_BYTES,
            description=description,
        )
        return json.loads(text)
    except PrivateFileError:
        raise RoutingError(f"{description} could not be read safely.") from None
    except ValueError:
        raise RoutingError(f"{description} is not valid JSON.") from None


def load_active_documents(target_root: Path) -> Tuple[object, object, object]:
    directory = config_dir(target_root)
    return (
        _read_json_private(directory / "03_inbounds.json", description="Xray inbound configuration"),
        _read_json_private(directory / "04_outbounds.json", description="Xray outbound configuration"),
        _read_json_private(directory / "05_routing.json", description="Xray routing configuration"),
    )


def build_plan(
    repo_root: Path,
    target_root: Path,
    *,
    add_services: Sequence[str] = (),
    remove_services: Sequence[str] = (),
    add_domains: Sequence[str] = (),
    remove_domains: Sequence[str] = (),
) -> Tuple[RoutingPlan, Dict[str, object], Tuple[int, ...]]:
    inbounds, outbounds, routing = load_active_documents(target_root)
    inbound_tags = extract_routerkit_inbounds(inbounds)
    ports = extract_routerkit_ports(inbounds)
    validate_direct_outbound(outbounds)

    persisted = load_overrides(state_path(target_root), missing_ok=True)
    inferred = False
    if persisted is None:
        inferred_state = infer_known_service_state(repo_root, routing, inbound_tags)
        if inferred_state is not None:
            current = inferred_state
            inferred = True
        else:
            rules = _routing_rules(routing)
            if rules and isinstance(rules[0], dict) and rules[0].get("outboundTag") == "direct":
                raise RoutingError(
                    "An unmanaged first direct rule already exists. Preserve it explicitly before using RouterKit routing overrides."
                )
            current = RoutingOverrides()
    else:
        current = persisted

    current_domains = expand_direct_domains(repo_root, current)
    desired = mutate_overrides(
        current,
        repo_root,
        add_services=add_services,
        remove_services=remove_services,
        add_domains=add_domains,
        remove_domains=remove_domains,
    )
    desired_domains = expand_direct_domains(repo_root, desired)
    updated_routing, routing_changed = reconcile_routing_document(
        routing,
        inbound_tags,
        current_domains=current_domains,
        desired_domains=desired_domains,
    )
    state_changed = persisted != desired
    plan = RoutingPlan(
        current=current,
        desired=desired,
        direct_domains=desired_domains,
        inferred_existing_state=inferred,
        routing_change_required=routing_changed,
        state_change_required=state_changed,
    )
    return plan, updated_routing, ports


def _safe_atomic_replace(path: Path, data: bytes) -> None:
    destination = Path(path)
    if len(data) > MAX_CONFIG_BYTES:
        raise RoutingError("Generated Xray routing configuration exceeds the supported bound.")
    try:
        parent_meta = destination.parent.lstat()
        if stat.S_ISLNK(parent_meta.st_mode) or not stat.S_ISDIR(parent_meta.st_mode):
            raise OSError
        old_meta = destination.lstat()
        if stat.S_ISLNK(old_meta.st_mode) or not stat.S_ISREG(old_meta.st_mode) or old_meta.st_nlink != 1:
            raise OSError
        if os.name == "posix" and old_meta.st_uid != os.geteuid():
            raise OSError
        directory_fd = os.open(
            destination.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError:
        raise RoutingError("Active Xray routing destination is unsafe.") from None

    temporary = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        for _attempt in range(32):
            candidate = ".routerkit-routing-%s" % secrets.token_hex(8)
            try:
                fd = os.open(candidate, flags, 0o600, dir_fd=directory_fd)
                temporary = candidate
                break
            except FileExistsError:
                continue
        else:
            raise OSError
        try:
            if os.name == "posix":
                os.fchmod(fd, 0o600)
            offset = 0
            while offset < len(data):
                written = os.write(fd, data[offset:])
                if written <= 0:
                    raise OSError
                offset += written
            os.fsync(fd)
        finally:
            os.close(fd)
        current = os.stat(destination.name, dir_fd=directory_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (old_meta.st_dev, old_meta.st_ino):
            raise RoutingError("Active Xray routing destination changed before publication.")
        os.replace(temporary, destination.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temporary = None
        os.fsync(directory_fd)
    except RoutingError:
        raise
    except OSError:
        raise RoutingError("Active Xray routing configuration could not be published safely.") from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except OSError:
                pass
        os.close(directory_fd)


def _entware_env() -> Dict[str, str]:
    env = os.environ.copy()
    current = env.get("PATH", "")
    env["PATH"] = ENTWARE_PATH + ((":" + current) if current else "")
    return env


def _run(command: Sequence[str], *, timeout: float = 60.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout,
            env=_entware_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RoutingError("RouterKit routing helper command failed to execute.") from None


def _xray_test(target_root: Path, confdir: Path) -> None:
    completed = _run(
        [str(xray_path(target_root)), "run", "-test", "-confdir", str(confdir)],
        timeout=45.0,
    )
    if completed.returncode != 0:
        raise RoutingError("Xray rejected the direct-routing configuration.")


def _stage_and_test(target_root: Path, routing_bytes: bytes) -> None:
    active = config_dir(target_root)
    stage_root = Path(target_root) / "var" / "lib" / "routerkit"
    ensure_private_directory(stage_root, description="RouterKit private state directory")
    stage = stage_root / ("routing-stage-%d-%s" % (os.getpid(), secrets.token_hex(4)))
    stage.mkdir(mode=0o700)
    try:
        count = 0
        for source in active.iterdir():
            if source.suffix != ".json":
                continue
            metadata = source.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise RoutingError("Active Xray config directory contains an unsafe JSON entry.")
            count += 1
            if count > 32:
                raise RoutingError("Active Xray config directory exceeds the bounded file count.")
            target = stage / source.name
            target.write_bytes(source.read_bytes())
            os.chmod(target, 0o600)
        (stage / "05_routing.json").write_bytes(routing_bytes)
        os.chmod(stage / "05_routing.json", 0o600)
        _xray_test(target_root, stage)
    finally:
        try:
            for entry in stage.iterdir():
                entry.unlink()
            stage.rmdir()
        except OSError:
            pass


def _restart_and_verify(repo_root: Path, target_root: Path, ports: Sequence[int]) -> None:
    init = init_path(target_root)
    completed = _run(["sh", str(init), "restart"], timeout=60.0)
    if completed.returncode != 0:
        raise RoutingError("Xray restart failed after the routing change.")
    verifier = _run(
        [sys.executable, str(Path(repo_root) / "scripts" / "routerkit-autostart.py"), "--verify"],
        timeout=45.0,
    )
    if verifier.returncode != 0:
        raise RoutingError("Xray restart completed but RouterKit listener verification failed.")
    for port in ports:
        probe = _run(
            [
                "curl",
                "-4",
                "--socks5-hostname",
                "127.0.0.1:%d" % port,
                "--connect-timeout",
                "10",
                "-m",
                "30",
                "-fsS",
                PROBE_URL,
            ],
            timeout=35.0,
        )
        if probe.returncode != 0 or not probe.stdout.strip():
            raise RoutingError("A RouterKit SOCKS profile failed the post-change HTTPS probe.")


def _backup_for_apply(target_root: Path, routing_bytes: bytes, state_bytes: Optional[bytes]) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup = Path(target_root) / "backups" / ("routerkit-routing-%s-%d" % (stamp, os.getpid()))
    try:
        backup.mkdir(parents=True, mode=0o700, exist_ok=False)
        os.chmod(backup, 0o700)
        route_target = backup / "05_routing.json"
        route_target.write_bytes(routing_bytes)
        os.chmod(route_target, 0o600)
        if state_bytes is not None:
            state_target = backup / "routing-overrides.json"
            state_target.write_bytes(state_bytes)
            os.chmod(state_target, 0o600)
    except OSError:
        raise RoutingError("Routing rollback backup could not be created.") from None
    return backup


def apply_plan(
    repo_root: Path,
    target_root: Path,
    plan: RoutingPlan,
    updated_routing: Dict[str, object],
    ports: Sequence[int],
) -> Optional[Path]:
    route_path = config_dir(target_root) / "05_routing.json"
    current_route_text = read_owner_only_text_file(
        route_path,
        maximum_bytes=MAX_CONFIG_BYTES,
        description="Xray routing configuration",
    )
    current_route_bytes = current_route_text.encode("utf-8")
    state_file = state_path(target_root)
    try:
        previous_state_text = read_owner_only_text_file(
            state_file,
            maximum_bytes=MAX_STATE_BYTES,
            description="Routing overrides file",
        )
        previous_state_bytes = previous_state_text.encode("utf-8")
    except PrivateFileError:
        try:
            state_file.lstat()
        except FileNotFoundError:
            previous_state_bytes = None
        else:
            raise RoutingError("Existing routing overrides state is unsafe.") from None

    if not plan.routing_change_required:
        if plan.state_change_required:
            write_overrides(state_file, plan.desired)
        return None

    desired_bytes = _json_bytes(updated_routing)
    backup = _backup_for_apply(target_root, current_route_bytes, previous_state_bytes)
    _stage_and_test(target_root, desired_bytes)
    published = False
    try:
        _safe_atomic_replace(route_path, desired_bytes)
        published = True
        _xray_test(target_root, config_dir(target_root))
        _restart_and_verify(repo_root, target_root, ports)
        if plan.state_change_required:
            write_overrides(state_file, plan.desired)
        return backup
    except Exception:
        if published:
            try:
                _safe_atomic_replace(route_path, current_route_bytes)
                _xray_test(target_root, config_dir(target_root))
                _restart_and_verify(repo_root, target_root, ports)
            except Exception:
                raise RoutingError(
                    "Routing update failed and automatic rollback could not be proven; use the routing rollback backup."
                ) from None
        raise


def reconcile_persisted_state(repo_root: Path, target_root: Path) -> bool:
    persisted = load_overrides(state_path(target_root), missing_ok=True)
    if persisted is None:
        return False
    inbounds, outbounds, routing = load_active_documents(target_root)
    inbound_tags = extract_routerkit_inbounds(inbounds)
    validate_direct_outbound(outbounds)
    desired_domains = expand_direct_domains(repo_root, persisted)
    updated, changed = reconcile_routing_document(
        routing,
        inbound_tags,
        current_domains=(),
        desired_domains=desired_domains,
    )
    if not changed:
        return False
    desired_bytes = _json_bytes(updated)
    _stage_and_test(target_root, desired_bytes)
    _safe_atomic_replace(config_dir(target_root) / "05_routing.json", desired_bytes)
    _xray_test(target_root, config_dir(target_root))
    return True


def render_plan_text(plan: RoutingPlan, *, services: Sequence[str]) -> str:
    lines = [
        "RouterKit local routing plan",
        "direct services: %s" % (", ".join(plan.desired.direct_services) or "none"),
        "direct custom domains: %s" % (", ".join(plan.desired.direct_domains) or "none"),
        "expanded direct domains: %s" % (", ".join(plan.direct_domains) or "none"),
        "routing change: %s" % ("yes" if plan.routing_change_required else "no"),
        "state change: %s" % ("yes" if plan.state_change_required else "no"),
        "inferred existing state: %s" % ("yes" if plan.inferred_existing_state else "no"),
        "available services: %s" % (", ".join(services) or "none"),
    ]
    return "\n".join(lines) + "\n"


def validate_mutation_args(args: argparse.Namespace) -> None:
    if args.mode == "status" and any(
        (args.add_service, args.remove_service, args.add_domain, args.remove_domain)
    ):
        raise RoutingError("status mode does not accept routing mutations.", exit_code=2)
    if args.mode == "reconcile" and any(
        (args.add_service, args.remove_service, args.add_domain, args.remove_domain)
    ):
        raise RoutingError("reconcile mode does not accept routing mutations.", exit_code=2)
    if args.mode == "apply" and not args.yes:
        raise RoutingError("apply mode requires --yes.", exit_code=2)
    if args.mode == "reconcile" and not args.yes:
        raise RoutingError("reconcile mode requires --yes.", exit_code=2)
    if args.mode in ("apply", "reconcile") and Path(args.target_root) != Path("/opt"):
        raise RoutingError("live routing writes currently support only --target-root /opt.", exit_code=2)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage persistent RouterKit local direct-routing overrides.")
    parser.add_argument(
        "mode",
        choices=("status", "plan", "apply", "reconcile"),
        nargs="?",
        default="status",
    )
    parser.add_argument("--repo-root")
    parser.add_argument("--target-root", default="/opt")
    parser.add_argument("--add-service", action="append", default=[])
    parser.add_argument("--remove-service", action="append", default=[])
    parser.add_argument("--add-domain", action="append", default=[])
    parser.add_argument("--remove-domain", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--yes", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        validate_mutation_args(args)
        repo_root = Path(args.repo_root).resolve() if args.repo_root else repo_root_from_module()
        target_root = Path(args.target_root)
        services = available_service_names(repo_root)

        if args.mode == "reconcile":
            changed = reconcile_persisted_state(repo_root, target_root)
            result = {
                "schema": "routerkit.routing-reconcile.v1",
                "changed": changed,
                "result": "pass",
            }
            if args.json:
                print(json.dumps(result, sort_keys=True))
            else:
                print("RouterKit routing reconcile: %s" % ("updated" if changed else "no-op"))
            return 0

        plan, updated_routing, ports = build_plan(
            repo_root,
            target_root,
            add_services=args.add_service,
            remove_services=args.remove_service,
            add_domains=args.add_domain,
            remove_domains=args.remove_domain,
        )
        if args.mode in ("status", "plan"):
            if args.json:
                value = plan.to_dict()
                value["available_services"] = list(services)
                print(json.dumps(value, sort_keys=True))
            else:
                sys.stdout.write(render_plan_text(plan, services=services))
            return 0

        backup = apply_plan(repo_root, target_root, plan, updated_routing, ports)
        value = plan.to_dict()
        value["result"] = "pass"
        value["rollback_backup"] = None if backup is None else str(backup)
        if args.json:
            print(json.dumps(value, sort_keys=True))
        else:
            print("RouterKit local routing apply: PASS")
            print("direct services: %s" % (", ".join(plan.desired.direct_services) or "none"))
            print("direct custom domains: %s" % (", ".join(plan.desired.direct_domains) or "none"))
            print("routing change: %s" % ("yes" if plan.routing_change_required else "no"))
            if backup is not None:
                print("rollback backup: %s" % backup)
        return 0
    except (RoutingError, PrivateFileError) as exc:
        print("routerkit-routing: %s" % exc, file=sys.stderr)
        return exc.exit_code if isinstance(exc, RoutingError) else 1


if __name__ == "__main__":
    raise SystemExit(main())
