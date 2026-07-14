#!/usr/bin/env python3
"""Bounded, proxy-free streaming acquisition for the pinned Xray archive."""

from __future__ import annotations

import hashlib
import os
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import routerkit_profile_network as profile_network


MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_REDIRECTS = 5
OVERALL_TIMEOUT = 180.0
_REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))


class ArtifactNetworkError(Exception):
    """A deliberately generic artifact acquisition failure safe for display."""


@dataclass(frozen=True)
class ArtifactDownload:
    byte_count: int
    sha256: str
    redirect_count: int


def _allowed_host(hostname: str, *, initial: bool) -> bool:
    if initial:
        return hostname == "github.com"
    return hostname == "github.com" or (
        hostname != "githubusercontent.com"
        and hostname.endswith(".githubusercontent.com")
    )


def _validate_hop(url: str, *, initial: bool) -> profile_network.ValidatedUrl:
    try:
        validated = profile_network.validate_https_url(url)
    except profile_network.ProfileNetworkError:
        raise ArtifactNetworkError("Pinned artifact URL is not allowed by policy.") from None
    if validated.literal_address is not None or not _allowed_host(
        validated.hostname, initial=initial
    ):
        raise ArtifactNetworkError("Pinned artifact URL is not allowed by policy.")
    return validated


def _open_destination(destination: Path) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(destination, flags, 0o600)
    if os.name == "posix":
        os.fchmod(fd, 0o600)
    return fd


def _write_all(fd: int, chunk: bytes) -> None:
    offset = 0
    while offset < len(chunk):
        written = os.write(fd, chunk[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


def download_pinned_archive(
    source_url: str,
    destination: Path,
    *,
    expected_url: str,
    resolver: Callable[..., Sequence[str]] = profile_network.resolve_addresses_bounded,
    connection_factory: Callable[..., Any] = profile_network._default_connection_factory,
    clock: Callable[[], float] = time.monotonic,
    overall_timeout: float = OVERALL_TIMEOUT,
    max_redirects: int = MAX_REDIRECTS,
    max_archive_bytes: int = MAX_ARCHIVE_BYTES,
) -> ArtifactDownload:
    """Stream exactly the manifest URL to one exclusive private file.

    The implementation intentionally uses the reviewed profile-source DNS,
    destination-address, pinned-peer, and TLS-hostname primitives. It does not
    consult urllib proxy handlers, cookies, ambient authentication, or proxy
    environment variables.
    """

    if (
        not isinstance(source_url, str)
        or source_url != expected_url
        or overall_timeout <= 0
        or max_redirects < 0
        or max_archive_bytes <= 0
    ):
        raise ArtifactNetworkError("Pinned artifact request is invalid.")

    current = _validate_hop(source_url, initial=True)
    deadline = clock() + overall_timeout
    visited = set()
    redirects = 0
    destination = Path(destination)

    while True:
        try:
            profile_network._remaining(deadline, clock)
        except profile_network.ProfileNetworkError:
            raise ArtifactNetworkError("Pinned artifact acquisition timed out.") from None
        if current.canonical_identity in visited:
            raise ArtifactNetworkError("Pinned artifact redirect is not allowed by policy.")
        visited.add(current.canonical_identity)

        try:
            dns_timeout = min(
                profile_network.DNS_TIMEOUT,
                profile_network._remaining(deadline, clock),
            )
            resolved = tuple(resolver(current.hostname, 443, timeout=dns_timeout))
            addresses = profile_network.validate_address_set(resolved)
        except profile_network.ProfileNetworkError:
            raise ArtifactNetworkError("Pinned artifact destination is not allowed.") from None
        except Exception:
            raise ArtifactNetworkError("Pinned artifact destination could not be resolved.") from None

        response = None
        connection = None
        guard = None
        try:
            response, connection, guard = profile_network._request_one_hop(
                current,
                addresses,
                connection_factory=connection_factory,
                deadline=deadline,
                clock=clock,
            )
            status = getattr(response, "status", None)
            if status in _REDIRECT_STATUSES:
                if redirects >= max_redirects:
                    raise ArtifactNetworkError("Pinned artifact redirect limit exceeded.")
                try:
                    location = profile_network._single_location(response)
                    combined = urllib.parse.urljoin(current.normalized_url, location)
                    next_hop = _validate_hop(combined, initial=False)
                except (profile_network.ProfileNetworkError, ArtifactNetworkError):
                    raise ArtifactNetworkError(
                        "Pinned artifact redirect is not allowed by policy."
                    ) from None
                redirects += 1
                current = next_hop
                continue

            if status != 200:
                raise ArtifactNetworkError("Pinned artifact response was not accepted.")
            content_encoding = profile_network._header(response, "Content-Encoding")
            if content_encoding is not None and content_encoding.strip().lower() != "identity":
                raise ArtifactNetworkError("Compressed HTTP responses are not accepted.")
            transfer_encoding = profile_network._header(response, "Transfer-Encoding")
            content_length = profile_network._content_length(response)
            if transfer_encoding is not None:
                if transfer_encoding.strip().lower() != "chunked" or content_length is not None:
                    raise ArtifactNetworkError("Pinned artifact response framing is invalid.")
            if content_length is not None and content_length > max_archive_bytes:
                raise ArtifactNetworkError("Pinned artifact response is too large.")

            digest = hashlib.sha256()
            count = 0
            fd = -1
            created = False
            try:
                fd = _open_destination(destination)
                created = True
                read_once = getattr(response, "read1", None) or response.read
                while count <= max_archive_bytes:
                    try:
                        remaining = profile_network._remaining(deadline, clock)
                    except profile_network.ProfileNetworkError:
                        raise ArtifactNetworkError(
                            "Pinned artifact acquisition timed out."
                        ) from None
                    if getattr(connection, "sock", None) is not None:
                        connection.sock.settimeout(remaining)
                    amount = min(65536, max_archive_bytes + 1 - count)
                    try:
                        chunk = read_once(amount)
                    except Exception:
                        raise ArtifactNetworkError(
                            "Pinned artifact body could not be read safely."
                        ) from None
                    if not isinstance(chunk, bytes) or len(chunk) > amount:
                        raise ArtifactNetworkError("Pinned artifact body is invalid.")
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > max_archive_bytes:
                        raise ArtifactNetworkError("Pinned artifact response is too large.")
                    digest.update(chunk)
                    _write_all(fd, chunk)
                if content_length is not None and count != content_length:
                    raise ArtifactNetworkError("Pinned artifact response is incomplete.")
                os.fsync(fd)
            except ArtifactNetworkError:
                raise
            except OSError:
                raise ArtifactNetworkError(
                    "Pinned artifact could not be stored privately."
                ) from None
            finally:
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                if created and not destination.exists():
                    created = False
            return ArtifactDownload(
                byte_count=count,
                sha256=digest.hexdigest(),
                redirect_count=redirects,
            )
        except ArtifactNetworkError:
            try:
                destination.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
            raise
        except profile_network.ProfileNetworkError:
            try:
                destination.unlink()
            except (FileNotFoundError, OSError):
                pass
            raise ArtifactNetworkError("Pinned artifact acquisition failed safely.") from None
        finally:
            preserve = profile_network._has_active_cancellation()
            if guard is not None:
                profile_network._best_effort_cleanup(
                    guard.stop, preserve_cancellation=preserve
                )
            if response is not None:
                profile_network._best_effort_cleanup(
                    response.close, preserve_cancellation=preserve
                )
            if connection is not None:
                profile_network._best_effort_cleanup(
                    connection.close, preserve_cancellation=preserve
                )
