#!/usr/bin/env python3
"""Offline, secret-safe parsing and selection for RouterKit profile sources.

The deliberately narrow compatibility policy in this module accepts VLESS
Reality nodes using TCP (or Xray's equivalent ``raw`` spelling) and either no
flow or ``xtls-rprx-vision``.  It performs no network access.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import stat
import tempfile
import urllib.parse
import uuid as uuid_module
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


MAX_PAYLOAD_BYTES = 1024 * 1024
MAX_DECODED_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_CANDIDATE_LINKS = 4096
MAX_LABEL_LENGTH = 48

_VLESS_PREFIX = "vless" + "://"
_VLESS_RE = re.compile(re.escape(_VLESS_PREFIX), re.IGNORECASE)
_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_UUID_RE = re.compile(
    r"(?i)(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])"
)
_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{24,}(?![A-Za-z0-9_-])")
_UNSAFE_LABEL_WORD_RE = re.compile(
    r"(?i)(?:secret|credential|password|passwd|bearer|private[ _-]?key|public[ _-]?key|"
    r"short[ _-]?id|access[ _-]?token)"
)
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHORT_ID_RE = re.compile(r"^(?:[0-9a-fA-F]{2}){0,8}$")
_PUBLIC_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
_FINGERPRINTS = {
    "chrome", "firefox", "safari", "ios", "android", "edge", "360", "qq",
    "random", "randomized",
}


class ProfileSourceError(Exception):
    """Base class for errors that are safe to display to a user."""


class PayloadValidationError(ProfileSourceError):
    pass


class NodeValidationError(ProfileSourceError):
    pass


class SelectionError(ProfileSourceError):
    pass


class OutputExistsError(ProfileSourceError):
    pass


def _inspect_output_destination(destination: Path) -> Optional[os.stat_result]:
    try:
        metadata = destination.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise ProfileSourceError("Could not inspect the private output path.") from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ProfileSourceError("Private output path must be a regular, non-symlink file.")
    return metadata


@dataclass(frozen=True, repr=False)
class NodeRecord(Mapping[str, Any]):
    """A parsed node. Secret-bearing fields are excluded from ``repr``."""

    name: str = field(repr=False)
    uuid: str = field(repr=False)
    host: str = field(repr=False)
    port: int
    network: str
    security: str
    flow: str
    sni: str = field(repr=False)
    fp: str
    public_key: str = field(repr=False)
    short_id: str = field(repr=False)
    spider_path: str = field(repr=False)
    raw_link: str = field(repr=False)

    _KEYS = (
        "name", "uuid", "host", "port", "network", "security", "flow",
        "sni", "fp", "pbk", "sid", "spx",
    )

    def __repr__(self) -> str:
        return (
            "NodeRecord(port={!r}, network={!r}, security={!r}, flow={!r}, fp={!r})"
        ).format(self.port, self.network, self.security, self.flow, self.fp)

    def __getitem__(self, key: str) -> Any:
        if key not in self._KEYS:
            raise KeyError(key)
        aliases = {"pbk": "public_key", "sid": "short_id", "spx": "spider_path"}
        key = aliases.get(key, key)
        return getattr(self, key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._KEYS)

    def __len__(self) -> int:
        return len(self._KEYS)

    @property
    def pbk(self) -> str:
        return self.public_key

    @property
    def sid(self) -> str:
        return self.short_id

    @property
    def spx(self) -> str:
        return self.spider_path

    @property
    def canonical_identity(self) -> tuple[Any, ...]:
        return (
            self.uuid.lower(), self.host.lower(), self.port, self.network,
            self.security, self.flow, self.sni.lower(), self.fp.lower(),
            self.public_key, self.short_id.lower(), self.spider_path,
        )


@dataclass(frozen=True, repr=False)
class SelectedNodes:
    primary: NodeRecord = field(repr=False)
    fallbacks: tuple[NodeRecord, ...] = field(default=(), repr=False)

    def __repr__(self) -> str:
        return f"SelectedNodes(fallback_count={len(self.fallbacks)})"


def validate_env_name(name: str) -> None:
    if not _ENV_NAME_RE.fullmatch(name):
        raise PayloadValidationError("Invalid environment variable name.")


def _bounded_utf8_size(text: str, limit: int, message: str) -> int:
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        raise PayloadValidationError("Payload is not valid UTF-8 text.") from None
    if size > limit:
        raise PayloadValidationError(message)
    return size


def _extract_from_string(text: str, found: list[str]) -> None:
    for start_match in _VLESS_RE.finditer(text):
        start = start_match.start()
        end = start
        while end < len(text) and text[end] not in "\r\n\t \"'<>[]{}":
            end += 1
        candidate = text[start:end].rstrip(",;")
        if candidate:
            found.append(candidate)
            if len(found) > MAX_CANDIDATE_LINKS:
                raise PayloadValidationError("Payload contains too many candidate links.")


def _iter_json_strings(value: Any, depth: int = 0) -> Iterator[str]:
    if depth > MAX_JSON_DEPTH:
        raise PayloadValidationError("Payload JSON nesting is too deep.")
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _iter_json_strings(item, depth + 1)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_json_strings(item, depth + 1)


def _maybe_base64_decode(text: str) -> Optional[str]:
    compact = "".join(text.split())
    if not compact or len(compact) < 8:
        return None
    if not re.fullmatch(r"[A-Za-z0-9+/_-]*={0,2}", compact):
        return None
    # Check the decoded bound before allocating a potentially large buffer.
    if ((len(compact) + 3) // 4) * 3 > MAX_DECODED_BYTES + 2:
        raise PayloadValidationError("Decoded payload is too large.")
    padded = compact + "=" * (-len(compact) % 4)
    try:
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
        if len(decoded) > MAX_DECODED_BYTES:
            raise PayloadValidationError("Decoded payload is too large.")
        return decoded.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None


def _collect_payload_links(text: str, found: list[str]) -> None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    except RecursionError:
        raise PayloadValidationError("Payload JSON nesting is too deep.") from None
    if parsed is not None:
        for value in _iter_json_strings(parsed):
            _extract_from_string(value, found)
    else:
        _extract_from_string(text, found)


def extract_vless_links(
    payload: str,
    *,
    max_payload_bytes: int = MAX_PAYLOAD_BYTES,
) -> list[str]:
    """Extract candidate links in deterministic order with one Base64 pass."""

    if not isinstance(payload, str):
        raise PayloadValidationError("Payload must be text.")
    _bounded_utf8_size(payload, max_payload_bytes, "Payload is too large.")
    found: list[str] = []
    _collect_payload_links(payload, found)
    decoded = _maybe_base64_decode(payload)
    if decoded is not None:
        _collect_payload_links(decoded, found)

    result: list[str] = []
    seen: set[str] = set()
    for link in found:
        if link not in seen:
            seen.add(link)
            result.append(link)
    return result


def _query_value(query: Mapping[str, list[str]], *names: str, default: str = "") -> str:
    lowered = {key.lower(): value for key, value in query.items()}
    for name in names:
        values = lowered.get(name.lower())
        if values:
            return values[0]
    return default


def parse_vless(link: str) -> NodeRecord:
    """Parse and strictly validate one compatible VLESS Reality/TCP link."""

    try:
        parsed = urllib.parse.urlsplit(link)
        port = parsed.port
        host = parsed.hostname
        user_id = urllib.parse.unquote(parsed.username or "")
        has_password = parsed.password is not None
    except (ValueError, UnicodeError):
        raise NodeValidationError("Malformed VLESS node.") from None

    if parsed.scheme.lower() != "vless":
        raise NodeValidationError("Unsupported node scheme.")
    if (
        not host
        or any(character.isspace() for character in host)
        or port is None
        or not (1 <= port <= 65535)
    ):
        raise NodeValidationError("VLESS node endpoint is invalid.")
    if has_password:
        raise NodeValidationError("VLESS user information is invalid.")
    try:
        canonical_uuid = str(uuid_module.UUID(user_id))
    except (ValueError, AttributeError):
        raise NodeValidationError("VLESS user identifier is invalid.") from None

    try:
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, strict_parsing=False)
        label = urllib.parse.unquote(parsed.fragment or "")
    except (ValueError, UnicodeError):
        raise NodeValidationError("Malformed VLESS node parameters.") from None

    security = _query_value(query, "security").strip().lower()
    raw_network = _query_value(query, "type", "network", default="tcp").strip().lower() or "tcp"
    network = "tcp" if raw_network in {"tcp", "raw"} else raw_network
    flow = _query_value(query, "flow").strip().lower()
    public_key = _query_value(query, "pbk", "publicKey").strip()
    short_id = _query_value(query, "sid", "shortId").strip()
    server_name = _query_value(query, "sni", "serverName").strip() or host
    fingerprint = _query_value(query, "fp", "fingerprint", default="chrome").strip().lower() or "chrome"
    spider_path = _query_value(query, "spx", "spiderX", default="/").strip() or "/"

    if security != "reality":
        raise NodeValidationError("VLESS node uses unsupported security.")
    if network != "tcp":
        raise NodeValidationError("VLESS node uses unsupported transport.")
    if flow not in {"", "xtls-rprx-vision"}:
        raise NodeValidationError("VLESS node uses unsupported flow.")
    if not _PUBLIC_KEY_RE.fullmatch(public_key):
        raise NodeValidationError("VLESS Reality public key is invalid.")
    if not _SHORT_ID_RE.fullmatch(short_id):
        raise NodeValidationError("VLESS Reality short ID is invalid.")
    if fingerprint not in _FINGERPRINTS:
        raise NodeValidationError("VLESS fingerprint is unsupported.")
    if (
        _CONTROL_RE.search(server_name)
        or any(character.isspace() for character in server_name)
        or any(character in server_name for character in "/?#@")
        or len(server_name) > 255
    ):
        raise NodeValidationError("VLESS server name is invalid.")
    if _CONTROL_RE.search(spider_path) or len(spider_path) > 1024:
        raise NodeValidationError("VLESS spider path is invalid.")
    if not spider_path.startswith("/"):
        spider_path = "/" + spider_path

    return NodeRecord(
        name=label,
        uuid=canonical_uuid,
        host=host,
        port=port,
        network=network,
        security=security,
        flow=flow,
        sni=server_name,
        fp=fingerprint,
        public_key=public_key,
        short_id=short_id,
        spider_path=spider_path,
        raw_link=link,
    )


def parse_compatible_nodes(payload: str) -> list[NodeRecord]:
    links = extract_vless_links(payload)
    if not links:
        raise PayloadValidationError("No VLESS nodes were found in the payload.")
    nodes: list[NodeRecord] = []
    identities: set[tuple[Any, ...]] = set()
    for link in links:
        try:
            node = parse_vless(link)
        except NodeValidationError:
            continue
        if node.canonical_identity not in identities:
            identities.add(node.canonical_identity)
            nodes.append(node)
    if not nodes:
        raise NodeValidationError("No compatible VLESS Reality/TCP nodes were found.")
    return nodes


def safe_label(label: str, fallback_index: int) -> str:
    value = _ANSI_RE.sub("", label)
    value = _CONTROL_RE.sub(" ", value)
    value = " ".join(value.split()).strip()
    unsafe = (
        not value
        or "://" in value
        or "@" in value
        or bool(_UUID_RE.search(value))
        or bool(_TOKEN_RE.search(value))
        or bool(_UNSAFE_LABEL_WORD_RE.search(value))
    )
    if unsafe:
        return f"Node {fallback_index}"
    if len(value) > MAX_LABEL_LENGTH:
        value = value[: MAX_LABEL_LENGTH - 1].rstrip() + "…"
    return value or f"Node {fallback_index}"


def safe_node_summary(node: NodeRecord, index: int) -> dict[str, object]:
    return {
        "index": index,
        "label": safe_label(node.name, index),
        "port": node.port,
        "security": "reality",
        "network": "tcp",
        "flow": node.flow or "none",
        "fingerprint": node.fp,
    }


def select_nodes(
    nodes: Sequence[NodeRecord],
    primary_index: int,
    fallback_indexes: Sequence[int] = (),
) -> SelectedNodes:
    """Select nodes using the CLI's 1-based indices."""

    fallbacks = tuple(fallback_indexes)
    if len(fallbacks) > 2:
        raise SelectionError("At most two fallback nodes may be selected.")
    indexes = (primary_index,) + fallbacks
    if any(isinstance(index, bool) or not isinstance(index, int) for index in indexes):
        raise SelectionError("Node indexes must be integers.")
    if len(set(indexes)) != len(indexes):
        raise SelectionError("Node selections must use distinct indexes.")
    if any(index < 1 or index > len(nodes) for index in indexes):
        raise SelectionError("A selected node index is out of range.")
    return SelectedNodes(
        primary=nodes[primary_index - 1],
        fallbacks=tuple(nodes[index - 1] for index in fallbacks),
    )


def build_profiles_document(selection: SelectedNodes) -> dict[str, object]:
    nodes = (selection.primary,) + selection.fallbacks
    names = ("primary", "fallback-1", "fallback-2")
    ports = (1082, 1083, 1084)
    return {
        "profiles": [
            {
                "name": names[index],
                "port": ports[index],
                "vless": node.raw_link,
                "select": {
                    "index": 0,
                    "require_security": "reality",
                    "require_network": "tcp",
                },
            }
            for index, node in enumerate(nodes)
        ]
    }


def write_private_json(
    path: Path,
    data: Mapping[str, object],
    *,
    overwrite: bool = False,
) -> None:
    """Publish private JSON with mode 0600 and no secret-bearing backup."""

    destination = Path(path)
    existing = _inspect_output_destination(destination)
    if existing is not None and not overwrite:
        raise OutputExistsError("Output file already exists; use --force to replace it.")
    if not destination.parent.exists() or not destination.parent.is_dir():
        raise ProfileSourceError("Output directory does not exist.")

    fd = -1
    temporary_path: Optional[Path] = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        temporary_path = Path(name)
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        if overwrite:
            _inspect_output_destination(destination)
            os.replace(temporary_path, destination)
            temporary_path = None
        else:
            link = getattr(os, "link", None)
            if not callable(link):
                raise ProfileSourceError(
                    "Atomic no-overwrite publication is not supported on this platform."
                )
            try:
                link(temporary_path, destination)
            except FileExistsError:
                raise OutputExistsError(
                    "Output file already exists; use --force to replace it."
                ) from None
            temporary_path.unlink()
            temporary_path = None
    except (OutputExistsError, ProfileSourceError):
        raise
    except OSError:
        raise ProfileSourceError("Could not write the private profiles file.") from None
    finally:
        if fd >= 0:
            os.close(fd)
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
