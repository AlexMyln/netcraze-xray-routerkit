#!/usr/bin/env python3
"""Secret-safe, bounded HTTPS acquisition for RouterKit profile sources."""

from __future__ import annotations

import http.client
import ipaddress
import multiprocessing
import re
import socket
import ssl
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence, Tuple


MAX_URL_BYTES = 8192
MAX_LOCATION_BYTES = 8192
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_DNS_ADDRESSES = 16
MAX_REDIRECTS = 5
DNS_TIMEOUT = 5.0
CONNECT_TIMEOUT = 10.0
OVERALL_TIMEOUT = 30.0
PROCESS_JOIN_GRACE = 0.25

_REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))
_HEX = frozenset("0123456789abcdefABCDEF")
_HOST_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_MEDIA_TYPE_RE = re.compile(
    r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+/[!#$%&'*+.^_`|~0-9A-Za-z-]+$"
)

# Fixed, reviewed policy derived from the IANA special-purpose registries on
# 2026-07-13. Runtime decisions never fetch registry data. These tuples are
# public so tests can pin their immutability and exact compatibility contract.
DENIED_IPV4_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
)

DENIED_IPV6_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "2001::/23",
        "2001:db8::/32",
        "3fff::/20",
    )
)

CONSERVATIVELY_DENIED_IPV6_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "::ffff:0:0/96",
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "2001::/32",
        "2001:10::/28",
        "2001:20::/28",
        "2002::/16",
    )
)

ALLOWED_SPECIAL_PURPOSE_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "192.0.0.9/32",
        "192.0.0.10/32",
        "2001:1::1/128",
        "2001:1::2/128",
        "2001:1::3/128",
        "2001:3::/32",
        "2001:4:112::/48",
        "2001:30::/28",
    )
)

_IPV6_PUBLIC_UNICAST_NETWORK = ipaddress.ip_network("2000::/3")
_CANCELLATION_EXCEPTIONS = (KeyboardInterrupt, SystemExit, GeneratorExit)


class ProfileNetworkError(Exception):
    """Base class for errors that are safe to display."""


class UrlPolicyError(ProfileNetworkError):
    pass


class DnsResolutionError(ProfileNetworkError):
    pass


class DestinationPolicyError(ProfileNetworkError):
    pass


class TlsConnectionError(ProfileNetworkError):
    pass


class RedirectPolicyError(ProfileNetworkError):
    pass


class ResponsePolicyError(ProfileNetworkError):
    pass


@dataclass(frozen=True, repr=False)
class ValidatedUrl:
    hostname: str = field(repr=False)
    authority: str = field(repr=False)
    request_target: str = field(repr=False)
    normalized_url: str = field(repr=False)
    canonical_identity: Tuple[str, str, str, str] = field(repr=False)
    literal_address: Optional[str] = field(default=None, repr=False)

    def __repr__(self) -> str:
        return "ValidatedUrl(is_ip_literal={!r})".format(self.literal_address is not None)


@dataclass(frozen=True, repr=False)
class ResolvedPayload:
    payload: str = field(repr=False)
    byte_count: int
    redirect_count: int
    content_type: Optional[str] = field(default=None, repr=False)

    def __repr__(self) -> str:
        return "ResolvedPayload(byte_count={!r}, redirect_count={!r})".format(
            self.byte_count, self.redirect_count
        )


def _generic_url_error() -> UrlPolicyError:
    return UrlPolicyError("HTTPS source URL is not allowed by policy.")


def normalize_https_source_value(value: str) -> str:
    """Strip only outer whitespace around one complete HTTPS source value."""

    if not isinstance(value, str):
        raise _generic_url_error()
    normalized = value.strip()
    if not normalized:
        raise _generic_url_error()
    return normalized


def _has_invalid_percent_escape(value: str) -> bool:
    index = 0
    while True:
        index = value.find("%", index)
        if index < 0:
            return False
        if index + 2 >= len(value) or value[index + 1] not in _HEX or value[index + 2] not in _HEX:
            return True
        index += 3


def _canonical_percent_escapes(value: str) -> str:
    parts = []
    index = 0
    while index < len(value):
        if value[index] == "%":
            parts.append("%" + value[index + 1 : index + 3].upper())
            index += 3
        else:
            parts.append(value[index])
            index += 1
    return "".join(parts)


def _validate_domain(hostname: str) -> str:
    candidate = hostname[:-1] if hostname.endswith(".") else hostname
    if not candidate or candidate.endswith(".") or "%" in candidate:
        raise _generic_url_error()
    labels = candidate.split(".")
    if any(not label for label in labels):
        raise _generic_url_error()
    encoded_labels = []
    try:
        for label in labels:
            encoded = label.encode("idna").decode("ascii")
            if len(encoded) > 63 or not _HOST_LABEL_RE.fullmatch(encoded):
                raise _generic_url_error()
            encoded_labels.append(encoded.lower())
    except (UnicodeError, UrlPolicyError):
        raise _generic_url_error() from None
    canonical = ".".join(encoded_labels)
    if len(canonical) > 253:
        raise _generic_url_error()
    if re.fullmatch(r"[0-9.]+", canonical):
        raise _generic_url_error()
    return canonical


def _normalize_address(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except (TypeError, ValueError):
        raise DestinationPolicyError("Destination address is not allowed by policy.") from None


def _contains_address(networks: Sequence[Any], address: Any) -> bool:
    return any(network.version == address.version and address in network for network in networks)


def _is_allowed_special_purpose(address: Any) -> bool:
    return _contains_address(ALLOWED_SPECIAL_PURPOSE_NETWORKS, address)


def _is_explicitly_denied(address: Any) -> bool:
    if address.version == 6 and _contains_address(
        CONSERVATIVELY_DENIED_IPV6_NETWORKS, address
    ):
        return True
    if _is_allowed_special_purpose(address):
        return False
    if address.version == 4:
        return _contains_address(DENIED_IPV4_NETWORKS, address)
    if address not in _IPV6_PUBLIC_UNICAST_NETWORK:
        return True
    return _contains_address(DENIED_IPV6_NETWORKS, address)


def _has_disallowed_ipaddress_properties(address: Any) -> bool:
    """Retain stdlib classification checks as defense in depth only."""

    return bool(
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
        or getattr(address, "ipv4_mapped", None) is not None
    )


def validate_address_set(addresses: Sequence[str]) -> Tuple[str, ...]:
    """Require a non-empty, entirely global address set."""

    if not addresses or len(addresses) > MAX_DNS_ADDRESSES:
        raise DestinationPolicyError("Destination address set is not allowed by policy.")
    accepted = {}
    for value in addresses:
        normalized = _normalize_address(value)
        address = ipaddress.ip_address(normalized)
        if _is_explicitly_denied(address) or (
            not _is_allowed_special_purpose(address)
            and _has_disallowed_ipaddress_properties(address)
        ):
            raise DestinationPolicyError("Destination address set is not allowed by policy.")
        accepted[(address.version, int(address))] = normalized
    if not accepted:
        raise DestinationPolicyError("Destination address set is not allowed by policy.")
    return tuple(accepted[key] for key in sorted(accepted))


def validate_https_url(url: str) -> ValidatedUrl:
    """Parse and strictly validate one absolute HTTPS URL without exposing it."""

    if not isinstance(url, str):
        raise _generic_url_error()
    try:
        encoded_url = url.encode("utf-8")
    except UnicodeEncodeError:
        raise _generic_url_error() from None
    if not encoded_url or len(encoded_url) > MAX_URL_BYTES:
        raise _generic_url_error()
    if any(char.isspace() or ord(char) < 32 or 127 <= ord(char) <= 159 for char in url):
        raise _generic_url_error()
    if "\\" in url or _has_invalid_percent_escape(url):
        raise _generic_url_error()

    try:
        parsed = urllib.parse.urlsplit(url, allow_fragments=True)
        port = parsed.port
        hostname = parsed.hostname
    except (ValueError, UnicodeError):
        raise _generic_url_error() from None

    if parsed.scheme.lower() != "https" or not parsed.netloc or not hostname:
        raise _generic_url_error()
    if "@" in parsed.netloc or parsed.username is not None or parsed.password is not None:
        raise _generic_url_error()
    if "#" in url or parsed.fragment or parsed.netloc.endswith(":"):
        raise _generic_url_error()
    if port not in (None, 443):
        raise _generic_url_error()
    try:
        (parsed.path + parsed.query).encode("ascii")
    except UnicodeEncodeError:
        raise _generic_url_error() from None

    literal_address = None
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        canonical_host = _validate_domain(hostname)
    else:
        literal_address = validate_address_set((str(literal),))[0]
        canonical_host = literal_address

    host_for_authority = "[{}]".format(canonical_host) if ":" in canonical_host else canonical_host
    explicit_port = port == 443 and parsed.netloc.rsplit(":", 1)[-1] == "443"
    authority = host_for_authority + (":443" if explicit_port else "")
    path = parsed.path or "/"
    query = parsed.query
    request_target = path + (("?" + query) if query else "")
    normalized_url = urllib.parse.urlunsplit(("https", authority, parsed.path, query, ""))
    identity = (
        "https",
        canonical_host,
        _canonical_percent_escapes(path),
        _canonical_percent_escapes(query),
    )
    return ValidatedUrl(
        hostname=canonical_host,
        authority=authority,
        request_target=request_target,
        normalized_url=normalized_url,
        canonical_identity=identity,
        literal_address=literal_address,
    )


def _dns_worker(send_connection: Any, hostname: str, port: int) -> None:
    """Top-level spawn target. Never sends exception or source-derived text."""

    try:
        results = socket.getaddrinfo(
            hostname,
            port,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
        )
        address_records = []
        seen = set()
        for family, _socktype, _proto, _canonname, sockaddr in results:
            if family not in (socket.AF_INET, socket.AF_INET6):
                continue
            try:
                normalized = str(ipaddress.ip_address(sockaddr[0]))
            except ValueError:
                continue
            if normalized not in seen:
                seen.add(normalized)
                address_records.append((family, normalized))
            if len(address_records) > MAX_DNS_ADDRESSES:
                break
        send_connection.send(("ok", tuple(address_records)))
    except Exception:
        try:
            send_connection.send(("error", ()))
        except Exception:
            pass
    finally:
        try:
            send_connection.close()
        except Exception:
            pass


def _stop_and_reap_process(process: Any, *, terminate_first: bool) -> None:
    try:
        if terminate_first and process.is_alive():
            process.terminate()
        process.join(PROCESS_JOIN_GRACE)
        if process.is_alive():
            process.terminate()
            process.join(PROCESS_JOIN_GRACE)
        if process.is_alive():
            kill = getattr(process, "kill", None)
            if kill is None:
                raise DnsResolutionError("DNS resolver process could not be stopped safely.")
            kill()
            process.join(PROCESS_JOIN_GRACE)
        if process.is_alive():
            raise DnsResolutionError("DNS resolver process could not be stopped safely.")
    except DnsResolutionError:
        raise
    except Exception:
        raise DnsResolutionError("DNS resolver process could not be stopped safely.") from None
    finally:
        close = getattr(process, "close", None)
        if close is not None:
            try:
                close()
            except Exception:
                pass


def _has_active_cancellation() -> bool:
    exception_type = sys.exc_info()[0]
    return exception_type is not None and issubclass(exception_type, _CANCELLATION_EXCEPTIONS)


def _best_effort_cleanup(action: Callable[[], Any], *, preserve_cancellation: bool) -> None:
    """Suppress ordinary cleanup failures and never replace an active cancellation."""

    try:
        action()
    except Exception:
        pass
    except _CANCELLATION_EXCEPTIONS:
        if not preserve_cancellation:
            raise


def resolve_addresses_bounded(
    hostname: str,
    port: int,
    *,
    timeout: float = DNS_TIMEOUT,
    mp_context: Any = None,
) -> Tuple[str, ...]:
    """Resolve in a disposable spawned process with a hard parent-side wait."""

    if timeout <= 0:
        raise DnsResolutionError("DNS resolution deadline expired.")
    context = mp_context or multiprocessing.get_context("spawn")
    receive_connection = None
    send_connection = None
    process = None
    timed_out = False
    try:
        receive_connection, send_connection = context.Pipe(duplex=False)
        process = context.Process(
            target=_dns_worker,
            args=(send_connection, hostname, port),
            name="routerkit-dns-resolver",
        )
        process.start()
        send_connection.close()
        send_connection = None
        if not receive_connection.poll(timeout):
            timed_out = True
            raise DnsResolutionError("DNS resolution timed out.")
        try:
            status, records = receive_connection.recv()
        except (EOFError, OSError, ValueError):
            raise DnsResolutionError("DNS resolution failed.") from None
        if status != "ok":
            raise DnsResolutionError("DNS resolution failed.")
        if len(records) > MAX_DNS_ADDRESSES:
            raise DnsResolutionError("DNS resolution returned too many addresses.")
        addresses = []
        for record in records:
            if not isinstance(record, tuple) or len(record) != 2:
                raise DnsResolutionError("DNS resolution failed.")
            family, value = record
            try:
                address = ipaddress.ip_address(value)
            except (TypeError, ValueError):
                raise DnsResolutionError("DNS resolution failed.") from None
            expected_family = socket.AF_INET if address.version == 4 else socket.AF_INET6
            if family != expected_family:
                raise DnsResolutionError("DNS resolution failed.")
            addresses.append(str(address))
        return tuple(addresses)
    except DnsResolutionError:
        raise
    except Exception:
        raise DnsResolutionError("DNS resolution failed.") from None
    finally:
        preserve_cancellation = _has_active_cancellation()
        if receive_connection is not None:
            _best_effort_cleanup(
                receive_connection.close,
                preserve_cancellation=preserve_cancellation,
            )
        if send_connection is not None:
            _best_effort_cleanup(
                send_connection.close,
                preserve_cancellation=preserve_cancellation,
            )
        if process is not None:
            try:
                _stop_and_reap_process(process, terminate_first=timed_out)
            except Exception:
                if not preserve_cancellation:
                    raise
            except _CANCELLATION_EXCEPTIONS:
                if not preserve_cancellation:
                    raise


class PinnedHTTPSConnection(http.client.HTTPConnection):
    """HTTP/1.1 connection whose TCP route is pinned but TLS identity is not."""

    default_port = 443

    def __init__(
        self,
        validated_url: ValidatedUrl,
        address: str,
        *,
        timeout: float,
        context: Optional[ssl.SSLContext] = None,
        socket_factory: Callable[..., socket.socket] = socket.socket,
    ) -> None:
        super().__init__(validated_url.hostname, port=443, timeout=timeout)
        self._validated_url = validated_url
        self._address = validate_address_set((address,))[0]
        self._context = context
        self._socket_factory = socket_factory

    def connect(self) -> None:
        address = ipaddress.ip_address(self._address)
        family = socket.AF_INET if address.version == 4 else socket.AF_INET6
        sockaddr = (self._address, 443) if family == socket.AF_INET else (self._address, 443, 0, 0)
        raw_socket = None
        tls_socket = None
        try:
            raw_socket = self._socket_factory(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
            raw_socket.settimeout(self.timeout)
            raw_socket.connect(sockaddr)
            context = self._context or ssl.create_default_context()
            if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
                raise TlsConnectionError("TLS verification policy is unavailable.")
            if self._context is None and hasattr(context, "set_alpn_protocols"):
                context.set_alpn_protocols(["http/1.1"])
            tls_socket = context.wrap_socket(
                raw_socket,
                server_hostname=self._validated_url.hostname,
                suppress_ragged_eofs=False,
            )
            raw_socket = None
            peer = tls_socket.getpeername()
            peer_address = str(ipaddress.ip_address(peer[0]))
            if peer_address != self._address:
                raise TlsConnectionError("Connected peer address did not match policy.")
            selected_protocol = tls_socket.selected_alpn_protocol()
            if selected_protocol not in (None, "http/1.1"):
                raise TlsConnectionError("TLS application protocol is not supported.")
            self.sock = tls_socket
            tls_socket = None
        except TlsConnectionError:
            raise
        except Exception:
            raise TlsConnectionError("Secure HTTPS connection failed.") from None
        finally:
            preserve_cancellation = _has_active_cancellation()
            if tls_socket is not None:
                _best_effort_cleanup(
                    tls_socket.close,
                    preserve_cancellation=preserve_cancellation,
                )
            if raw_socket is not None:
                _best_effort_cleanup(
                    raw_socket.close,
                    preserve_cancellation=preserve_cancellation,
                )


def _default_connection_factory(
    validated_url: ValidatedUrl, address: str, timeout: float
) -> PinnedHTTPSConnection:
    return PinnedHTTPSConnection(validated_url, address, timeout=timeout)


class _DeadlineGuard:
    """Force an active connection down at an absolute wall-clock budget."""

    def __init__(self, connection: Any, delay: float) -> None:
        self._connection = connection
        self._timer = threading.Timer(delay, self._expire)
        self._timer.daemon = True
        self._timer.start()

    def _expire(self) -> None:
        try:
            active_socket = getattr(self._connection, "sock", None)
            if active_socket is not None:
                try:
                    active_socket.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
            self._connection.close()
        except Exception:
            pass

    def stop(self) -> None:
        self._timer.cancel()
        if self._timer is not threading.current_thread():
            self._timer.join(PROCESS_JOIN_GRACE)


def _remaining(deadline: float, clock: Callable[[], float]) -> float:
    remaining = deadline - clock()
    if remaining <= 0:
        raise ResponsePolicyError("HTTPS source resolution timed out.")
    return remaining


def _header(response: Any, name: str) -> Optional[str]:
    value = response.getheader(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ResponsePolicyError("HTTPS response headers are not allowed by policy.")
    return value


def _single_location(response: Any) -> str:
    headers = getattr(response, "headers", None)
    if headers is not None and hasattr(headers, "get_all"):
        values = headers.get_all("Location", [])
        if len(values) != 1:
            raise RedirectPolicyError("HTTPS redirect is not allowed by policy.")
        location = values[0]
    else:
        location = _header(response, "Location")
    if not isinstance(location, str) or not location:
        raise RedirectPolicyError("HTTPS redirect is not allowed by policy.")
    try:
        size = len(location.encode("utf-8"))
    except UnicodeEncodeError:
        raise RedirectPolicyError("HTTPS redirect is not allowed by policy.") from None
    if size > MAX_LOCATION_BYTES:
        raise RedirectPolicyError("HTTPS redirect is not allowed by policy.")
    return location


def _content_length(response: Any) -> Optional[int]:
    value = _header(response, "Content-Length")
    if value is None:
        return None
    if not value or len(value) > 20 or not value.isdigit():
        raise ResponsePolicyError("HTTPS response length is not allowed by policy.")
    return int(value, 10)


def _sanitized_content_type(response: Any) -> Optional[str]:
    value = _header(response, "Content-Type")
    if value is None or len(value) > 255:
        return None
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type if _MEDIA_TYPE_RE.fullmatch(media_type) else None


def _request_one_hop(
    validated_url: ValidatedUrl,
    addresses: Sequence[str],
    *,
    connection_factory: Callable[[ValidatedUrl, str, float], Any],
    deadline: float,
    clock: Callable[[], float],
) -> Tuple[Any, Any, _DeadlineGuard]:
    for address in addresses:
        connection = None
        deadline_guard = None
        handed_off = False
        try:
            timeout = min(CONNECT_TIMEOUT, _remaining(deadline, clock))
            connection = connection_factory(validated_url, address, timeout)
            deadline_guard = _DeadlineGuard(connection, _remaining(deadline, clock))
            connection.request(
                "GET",
                validated_url.request_target,
                headers={
                    "Host": validated_url.authority,
                    "User-Agent": "RouterKit-profile-source/1",
                    "Accept": "*/*",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            if getattr(connection, "sock", None) is not None:
                connection.sock.settimeout(_remaining(deadline, clock))
            handed_off = True
            return response, connection, deadline_guard
        except ResponsePolicyError:
            raise
        except Exception:
            pass
        finally:
            if not handed_off:
                preserve_cancellation = _has_active_cancellation()
                if deadline_guard is not None:
                    _best_effort_cleanup(
                        deadline_guard.stop,
                        preserve_cancellation=preserve_cancellation,
                    )
                if connection is not None:
                    _best_effort_cleanup(
                        connection.close,
                        preserve_cancellation=preserve_cancellation,
                    )
    if deadline - clock() <= 0:
        raise ResponsePolicyError("HTTPS source resolution timed out.")
    raise TlsConnectionError("Secure HTTPS connection failed.")


def _read_body_bounded(
    response: Any,
    connection: Any,
    *,
    maximum: int,
    deadline: float,
    clock: Callable[[], float],
) -> bytes:
    chunks = []
    byte_count = 0
    read_once = getattr(response, "read1", None) or response.read
    while byte_count <= maximum:
        remaining = _remaining(deadline, clock)
        if getattr(connection, "sock", None) is not None:
            connection.sock.settimeout(remaining)
        amount = min(65536, maximum + 1 - byte_count)
        try:
            chunk = read_once(amount)
        except Exception:
            if deadline - clock() <= 0:
                raise ResponsePolicyError("HTTPS source resolution timed out.") from None
            raise ResponsePolicyError("HTTPS response body could not be read safely.") from None
        _remaining(deadline, clock)
        if not isinstance(chunk, bytes):
            raise ResponsePolicyError("HTTPS response body is not binary data.")
        if len(chunk) > amount:
            raise ResponsePolicyError("HTTPS response body is too large.")
        if not chunk:
            break
        chunks.append(chunk)
        byte_count += len(chunk)
    body = b"".join(chunks)
    if len(body) > maximum:
        raise ResponsePolicyError("HTTPS response body is too large.")
    return body


def resolve_https_source(
    source: str,
    *,
    resolver: Callable[..., Sequence[str]] = resolve_addresses_bounded,
    connection_factory: Callable[[ValidatedUrl, str, float], Any] = _default_connection_factory,
    clock: Callable[[], float] = time.monotonic,
    overall_timeout: float = OVERALL_TIMEOUT,
    max_redirects: int = MAX_REDIRECTS,
    max_response_bytes: int = MAX_RESPONSE_BYTES,
) -> ResolvedPayload:
    """Return one bounded UTF-8 payload after manual, validated HTTPS redirects."""

    if overall_timeout <= 0 or max_redirects < 0 or max_response_bytes <= 0:
        raise ProfileNetworkError("HTTPS resolver limits are invalid.")
    current = validate_https_url(normalize_https_source_value(source))
    deadline = clock() + overall_timeout
    visited = set()
    redirect_count = 0

    while True:
        _remaining(deadline, clock)
        if current.canonical_identity in visited:
            raise RedirectPolicyError("HTTPS redirect loop is not allowed.")
        visited.add(current.canonical_identity)

        if current.literal_address is not None:
            addresses = (current.literal_address,)
        else:
            dns_timeout = min(DNS_TIMEOUT, _remaining(deadline, clock))
            try:
                resolved = resolver(current.hostname, 443, timeout=dns_timeout)
                resolved_values = tuple(resolved)
            except ProfileNetworkError:
                raise
            except Exception:
                raise DnsResolutionError("DNS resolution failed.") from None
            addresses = validate_address_set(resolved_values)

        response = None
        connection = None
        deadline_guard = None
        try:
            response, connection, deadline_guard = _request_one_hop(
                current,
                addresses,
                connection_factory=connection_factory,
                deadline=deadline,
                clock=clock,
            )
            status = getattr(response, "status", None)
            if not isinstance(status, int) or not 100 <= status <= 599:
                raise ResponsePolicyError("HTTPS response status is not allowed by policy.")

            if status in _REDIRECT_STATUSES:
                if redirect_count >= max_redirects:
                    raise RedirectPolicyError("HTTPS redirect limit exceeded.")
                location = _single_location(response)
                try:
                    combined = urllib.parse.urljoin(current.normalized_url, location)
                    next_url = validate_https_url(combined)
                except Exception:
                    raise RedirectPolicyError("HTTPS redirect is not allowed by policy.") from None
                redirect_count += 1
                current = next_url
                continue

            if status != 200:
                raise ResponsePolicyError("HTTPS response status is not allowed by policy.")

            content_encoding = _header(response, "Content-Encoding")
            if content_encoding is not None and content_encoding.strip().lower() != "identity":
                raise ResponsePolicyError("Compressed HTTPS responses are not supported.")
            transfer_encoding = _header(response, "Transfer-Encoding")
            content_length = _content_length(response)
            if transfer_encoding is not None:
                if transfer_encoding.strip().lower() != "chunked" or content_length is not None:
                    raise ResponsePolicyError("HTTPS response framing is not allowed by policy.")
            if content_length is not None and content_length > max_response_bytes:
                raise ResponsePolicyError("HTTPS response body is too large.")

            body = _read_body_bounded(
                response,
                connection,
                maximum=max_response_bytes,
                deadline=deadline,
                clock=clock,
            )
            if content_length is not None and len(body) != content_length:
                raise ResponsePolicyError("HTTPS response body is incomplete.")
            try:
                payload = body.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise ResponsePolicyError("HTTPS response body is not valid UTF-8 text.") from None
            return ResolvedPayload(
                payload=payload,
                byte_count=len(body),
                redirect_count=redirect_count,
                content_type=_sanitized_content_type(response),
            )
        finally:
            preserve_cancellation = _has_active_cancellation()
            if deadline_guard is not None:
                _best_effort_cleanup(
                    deadline_guard.stop,
                    preserve_cancellation=preserve_cancellation,
                )
            if response is not None:
                _best_effort_cleanup(
                    response.close,
                    preserve_cancellation=preserve_cancellation,
                )
            if connection is not None:
                _best_effort_cleanup(
                    connection.close,
                    preserve_cancellation=preserve_cancellation,
                )
