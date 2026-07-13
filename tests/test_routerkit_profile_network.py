import socket
import ssl
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import routerkit_profile_network as network


PUBLIC_V4 = "93.184.216.34"
PUBLIC_V4_ALT = "1.1.1.1"
PUBLIC_V6 = "2606:4700:4700::1111"


def dns_records(*addresses):
    return tuple(
        (
            socket.AF_INET if ":" not in address else socket.AF_INET6,
            address,
        )
        for address in addresses
    )


class HeaderBag:
    def __init__(self, values):
        self.values = {}
        for key, value in values.items():
            self.values.setdefault(key.lower(), []).append(value)

    def get_all(self, name, default=None):
        return self.values.get(name.lower(), default)


class FakeResponse:
    def __init__(self, status=200, body=b"payload", headers=None, read_error=None, on_read=None):
        self.status = status
        self.body = body
        self.read_error = read_error
        self.on_read = on_read
        self.closed = False
        self.offset = 0
        values = dict(headers or {})
        if status == 200 and "Content-Length" not in values and "Transfer-Encoding" not in values:
            values["Content-Length"] = str(len(body))
        self._headers = values
        self.headers = HeaderBag(values)

    def getheader(self, name):
        values = self.headers.get_all(name, [])
        return ", ".join(values) if values else None

    def read(self, amount=None):
        if self.on_read is not None:
            self.on_read()
        if self.read_error is not None:
            raise self.read_error
        if amount is None:
            chunk = self.body[self.offset :]
            self.offset = len(self.body)
            return chunk
        chunk = self.body[self.offset : self.offset + amount]
        self.offset += len(chunk)
        return chunk

    def read1(self, amount=None):
        return self.read(amount)

    def close(self):
        self.closed = True


class FakeSocketTimeout:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)


class FakeConnection:
    def __init__(self, response):
        self.response = response
        self.requests = []
        self.closed = False
        self.sock = FakeSocketTimeout()

    def request(self, method, target, headers=None):
        self.requests.append((method, target, dict(headers or {})))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class PlannedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.connections = []
        self.calls = []

    def __call__(self, validated_url, address, timeout):
        self.calls.append((validated_url, address, timeout))
        connection = FakeConnection(self.responses.pop(0))
        self.connections.append(connection)
        return connection


def public_resolver(_hostname, _port, *, timeout):
    if timeout <= 0:
        raise AssertionError("resolver received an expired timeout")
    return (PUBLIC_V4,)


class UrlPolicyTests(unittest.TestCase):
    def test_https_source_normalization_strips_only_outer_whitespace(self):
        value = " \tHTTPS://example.test/path%20value?token=a%20b\r\n"
        self.assertEqual(
            network.normalize_https_source_value(value),
            "HTTPS://example.test/path%20value?token=a%20b",
        )

    def test_https_source_normalization_rejects_empty_value_generically(self):
        marker = "DO_NOT_LEAK_EMPTY_SOURCE"
        with self.assertRaises(network.UrlPolicyError) as caught:
            network.normalize_https_source_value(" \r\n\t")
        self.assertNotIn(marker, str(caught.exception))

    def test_normal_https_url(self):
        value = network.validate_https_url("https://example.test/path?token=synthetic")
        self.assertEqual(value.hostname, "example.test")
        self.assertEqual(value.request_target, "/path?token=synthetic")

    def test_explicit_port_443(self):
        value = network.validate_https_url("https://example.test:443/path")
        self.assertEqual(value.authority, "example.test:443")

    def test_http_rejected(self):
        with self.assertRaises(network.UrlPolicyError):
            network.validate_https_url("http://example.test/")

    def test_non_443_rejected(self):
        with self.assertRaises(network.UrlPolicyError):
            network.validate_https_url("https://example.test:444/")

    def test_userinfo_rejected(self):
        with self.assertRaises(network.UrlPolicyError):
            network.validate_https_url("https://user:pass@example.test/")

    def test_fragment_rejected(self):
        for value in ("https://example.test/#fragment", "https://example.test/#"):
            with self.subTest(value=value), self.assertRaises(network.UrlPolicyError):
                network.validate_https_url(value)

    def test_control_character_rejected(self):
        with self.assertRaises(network.UrlPolicyError):
            network.validate_https_url("https://example.test/\nsecret")

    def test_oversized_url_rejected(self):
        with self.assertRaises(network.UrlPolicyError):
            network.validate_https_url("https://example.test/" + "a" * network.MAX_URL_BYTES)

    def test_missing_host_rejected(self):
        with self.assertRaises(network.UrlPolicyError):
            network.validate_https_url("https:///path")

    def test_malformed_authority_rejected(self):
        for value in ("https://example.test:/", "https://example.test:443:443/", "https:\\example.test/"):
            with self.subTest(value=value), self.assertRaises(network.UrlPolicyError):
                network.validate_https_url(value)

    def test_malformed_percent_escape_rejected(self):
        with self.assertRaises(network.UrlPolicyError):
            network.validate_https_url("https://example.test/%GG")

    def test_idna_normalization(self):
        value = network.validate_https_url("https://bücher.example/path")
        self.assertEqual(value.hostname, "xn--bcher-kva.example")

    def test_public_literal_ipv4(self):
        value = network.validate_https_url("https://{}/".format(PUBLIC_V4))
        self.assertEqual(value.literal_address, PUBLIC_V4)

    def test_public_literal_ipv6(self):
        value = network.validate_https_url("https://[{}]/".format(PUBLIC_V6))
        self.assertEqual(value.literal_address, PUBLIC_V6)

    def test_private_literal_rejected(self):
        with self.assertRaises(network.DestinationPolicyError):
            network.validate_https_url("https://127.0.0.1/")

    def test_secrets_absent_from_error_and_repr(self):
        secret = "DO_NOT_LEAK_URL_TOKEN"
        with self.assertRaises(network.ProfileNetworkError) as caught:
            network.validate_https_url("https://user:{}@example.test/{}".format(secret, secret))
        self.assertNotIn(secret, str(caught.exception))
        value = network.validate_https_url("https://example.test/path?token=" + secret)
        self.assertNotIn(secret, repr(value))
        self.assertNotIn("example.test", repr(value))


class DestinationPolicyTests(unittest.TestCase):
    def test_global_ipv4_accepted(self):
        self.assertEqual(network.validate_address_set((PUBLIC_V4,)), (PUBLIC_V4,))

    def test_global_ipv6_accepted(self):
        self.assertEqual(network.validate_address_set((PUBLIC_V6,)), (PUBLIC_V6,))

    def test_explicit_policy_tables_are_immutable_and_deterministic(self):
        for table in (
            network.DENIED_IPV4_NETWORKS,
            network.DENIED_IPV6_NETWORKS,
            network.CONSERVATIVELY_DENIED_IPV6_NETWORKS,
            network.ALLOWED_SPECIAL_PURPOSE_NETWORKS,
        ):
            self.assertIsInstance(table, tuple)
            self.assertEqual(table, tuple(sorted(table, key=lambda item: (item.version, int(item.network_address), item.prefixlen))))

    def test_representative_ipv4_special_purpose_ranges_are_rejected(self):
        values = (
            "0.0.0.1",
            "10.0.0.1",
            "100.64.0.1",
            "127.0.0.1",
            "169.254.169.254",
            "172.16.0.1",
            "192.0.0.1",
            "192.0.0.8",
            "192.0.0.170",
            "192.0.2.1",
            "192.88.99.1",
            "192.168.0.1",
            "198.18.0.1",
            "198.51.100.1",
            "203.0.113.1",
            "224.0.0.1",
            "240.0.0.1",
            "255.255.255.255",
        )
        for value in values:
            with self.subTest(value=value), self.assertRaises(network.DestinationPolicyError):
                network.validate_address_set((value,))

    def test_globally_reachable_192_0_0_exceptions_are_accepted(self):
        self.assertEqual(
            network.validate_address_set(("192.0.0.10", "192.0.0.9")),
            ("192.0.0.9", "192.0.0.10"),
        )

    def test_narrow_globally_reachable_ipv6_exceptions_are_accepted(self):
        values = (
            "2001:1::1",
            "2001:1::2",
            "2001:1::3",
            "2001:3::1",
            "2001:4:112::1",
            "2001:30::1",
        )
        self.assertEqual(network.validate_address_set(values), values)

    def test_representative_ipv6_special_purpose_ranges_are_rejected(self):
        values = (
            "::",
            "::1",
            "100::1",
            "100:0:0:1::1",
            "2001:2::1",
            "2001:db8::1",
            "3fff::1",
            "5f00::1",
            "fc00::1",
            "fe80::1",
            "ff02::1",
        )
        for value in values:
            with self.subTest(value=value), self.assertRaises(network.DestinationPolicyError):
                network.validate_address_set((value,))

    def test_translation_tunneling_and_identifier_ranges_are_rejected(self):
        values = (
            "::ffff:8.8.8.8",
            "64:ff9b::808:808",
            "64:ff9b:1::808:808",
            "2001::1",
            "2001:10::1",
            "2001:20::1",
            "2002:0808:0808::1",
        )
        for value in values:
            with self.subTest(value=value), self.assertRaises(network.DestinationPolicyError):
                network.validate_address_set((value,))

    def test_explicit_policy_rejects_even_if_properties_are_permissive(self):
        with mock.patch.object(network, "_has_disallowed_ipaddress_properties", return_value=False):
            for value in ("100.64.0.1", "192.0.2.1", "2001:db8::1", "2002::1"):
                with self.subTest(value=value), self.assertRaises(network.DestinationPolicyError):
                    network.validate_address_set((value,))

    def test_unsafe_ranges_rejected(self):
        values = (
            "127.0.0.1",
            "10.0.0.1",
            "100.64.0.1",
            "169.254.1.1",
            "224.0.0.1",
            "0.0.0.0",
            "240.0.0.1",
            "::1",
            "fe80::1",
            "ff02::1",
        )
        for value in values:
            with self.subTest(value=value), self.assertRaises(network.DestinationPolicyError):
                network.validate_address_set((value,))

    def test_unsafe_ipv4_mapped_address_rejected(self):
        with self.assertRaises(network.DestinationPolicyError):
            network.validate_address_set(("::ffff:127.0.0.1",))

    def test_global_ipv4_mapped_address_is_conservatively_rejected(self):
        with self.assertRaises(network.DestinationPolicyError):
            network.validate_address_set(("::ffff:8.8.8.8",))

    def test_mixed_global_private_set_rejected(self):
        with self.assertRaises(network.DestinationPolicyError):
            network.validate_address_set((PUBLIC_V4, "10.0.0.1"))

    def test_duplicates_deduplicated_and_sorted(self):
        self.assertEqual(
            network.validate_address_set((PUBLIC_V4_ALT, PUBLIC_V4, PUBLIC_V4_ALT)),
            (PUBLIC_V4_ALT, PUBLIC_V4),
        )

    def test_empty_set_rejected(self):
        with self.assertRaises(network.DestinationPolicyError):
            network.validate_address_set(())

    def test_excessive_set_rejected(self):
        with self.assertRaises(network.DestinationPolicyError):
            network.validate_address_set(tuple("8.8.8.{}".format(index) for index in range(1, 18)))


class FakePipeEnd:
    def __init__(self, *, poll_result=True, message=("ok", dns_records(PUBLIC_V4))):
        self.poll_result = poll_result
        self.message = message
        self.closed = False
        self.poll_timeout = None

    def poll(self, timeout):
        self.poll_timeout = timeout
        return self.poll_result

    def recv(self):
        return self.message

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, *, terminate_stops=True):
        self.alive = True
        self.terminate_stops = terminate_stops
        self.started = False
        self.terminate_calls = 0
        self.kill_calls = 0
        self.join_calls = []
        self.closed = False

    def start(self):
        self.started = True

    def is_alive(self):
        return self.alive

    def join(self, timeout):
        self.join_calls.append(timeout)
        if self.started and self.terminate_calls == 0 and self.kill_calls == 0:
            self.alive = False

    def terminate(self):
        self.terminate_calls += 1
        if self.terminate_stops:
            self.alive = False

    def kill(self):
        self.kill_calls += 1
        self.alive = False

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, receive, process):
        self.receive = receive
        self.send = FakePipeEnd()
        self.process = process
        self.process_kwargs = None

    def Pipe(self, duplex):
        if duplex is not False:
            raise AssertionError("pipe must be one-way")
        return self.receive, self.send

    def Process(self, **kwargs):
        self.process_kwargs = kwargs
        return self.process


class DnsTimeoutTests(unittest.TestCase):
    def test_successful_bounded_result(self):
        receive = FakePipeEnd(message=("ok", dns_records(PUBLIC_V4, PUBLIC_V4_ALT)))
        process = FakeProcess()
        context = FakeContext(receive, process)
        result = network.resolve_addresses_bounded("secret.example", 443, timeout=1.25, mp_context=context)
        self.assertEqual(result, (PUBLIC_V4, PUBLIC_V4_ALT))
        self.assertEqual(receive.poll_timeout, 1.25)
        self.assertEqual(context.process_kwargs["name"], "routerkit-dns-resolver")
        self.assertTrue(process.closed)

    def test_timeout_terminates_and_joins_child(self):
        receive = FakePipeEnd(poll_result=False)
        process = FakeProcess()
        context = FakeContext(receive, process)
        with self.assertRaises(network.DnsResolutionError):
            network.resolve_addresses_bounded("secret.example", 443, timeout=0.01, mp_context=context)
        self.assertGreaterEqual(process.terminate_calls, 1)
        self.assertTrue(process.join_calls)
        self.assertFalse(process.alive)

    def test_forced_kill_path(self):
        receive = FakePipeEnd(poll_result=False)
        process = FakeProcess(terminate_stops=False)
        context = FakeContext(receive, process)
        with self.assertRaises(network.DnsResolutionError):
            network.resolve_addresses_bounded("secret.example", 443, timeout=0.01, mp_context=context)
        self.assertEqual(process.kill_calls, 1)
        self.assertFalse(process.alive)

    def test_worker_exception_is_sanitized(self):
        secret = "SECRET_HOST.example"
        context = FakeContext(FakePipeEnd(message=("error", ())), FakeProcess())
        with self.assertRaises(network.DnsResolutionError) as caught:
            network.resolve_addresses_bounded(secret, 443, mp_context=context)
        self.assertNotIn(secret, str(caught.exception))

    def test_excessive_worker_result_rejected(self):
        values = dns_records(*("8.8.8.{}".format(index) for index in range(1, 18)))
        context = FakeContext(FakePipeEnd(message=("ok", values)), FakeProcess())
        with self.assertRaises(network.DnsResolutionError):
            network.resolve_addresses_bounded("secret.example", 443, mp_context=context)

    def test_worker_family_mismatch_is_rejected(self):
        records = ((socket.AF_INET6, PUBLIC_V4),)
        context = FakeContext(FakePipeEnd(message=("ok", records)), FakeProcess())
        with self.assertRaises(network.DnsResolutionError):
            network.resolve_addresses_bounded("secret.example", 443, mp_context=context)

    def test_invalid_timeout_does_not_start_child(self):
        with self.assertRaises(network.DnsResolutionError):
            network.resolve_addresses_bounded("secret.example", 443, timeout=0)

    def test_interrupt_during_poll_reaps_child_and_is_not_converted(self):
        receive = FakePipeEnd()
        process = FakeProcess()
        context = FakeContext(receive, process)
        with mock.patch.object(receive, "poll", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                network.resolve_addresses_bounded(
                    "secret.example", 443, timeout=1, mp_context=context
                )
        self.assertFalse(process.alive)
        self.assertTrue(process.closed)
        self.assertTrue(receive.closed)
        self.assertTrue(context.send.closed)


class FakeRawSocket:
    def __init__(self):
        self.timeout = None
        self.connected_to = None
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def connect(self, address):
        self.connected_to = address

    def close(self):
        self.closed = True


class FakeTlsSocket:
    def __init__(self, peer=PUBLIC_V4, protocol="http/1.1"):
        self.peer = peer
        self.protocol = protocol
        self.closed = False

    def getpeername(self):
        return (self.peer, 443)

    def selected_alpn_protocol(self):
        return self.protocol

    def close(self):
        self.closed = True


class FakeTlsContext:
    def __init__(self, tls_socket=None, *, verified=True):
        self.check_hostname = verified
        self.verify_mode = ssl.CERT_REQUIRED if verified else ssl.CERT_NONE
        self.tls_socket = tls_socket or FakeTlsSocket()
        self.server_hostname = None
        self.suppress_ragged_eofs = None

    def wrap_socket(self, raw_socket, *, server_hostname, suppress_ragged_eofs):
        self.server_hostname = server_hostname
        self.suppress_ragged_eofs = suppress_ragged_eofs
        return self.tls_socket


class PinnedConnectionTests(unittest.TestCase):
    def make_connection(self, *, address=PUBLIC_V4, peer=PUBLIC_V4, hostname="origin.example"):
        validated = network.validate_https_url("https://{}/path".format(hostname))
        raw = FakeRawSocket()
        tls_context = FakeTlsContext(FakeTlsSocket(peer=peer))
        families = []

        def socket_factory(family, socktype, protocol):
            families.append((family, socktype, protocol))
            return raw

        connection = network.PinnedHTTPSConnection(
            validated,
            address,
            timeout=2.5,
            context=tls_context,
            socket_factory=socket_factory,
        )
        return connection, raw, tls_context, families

    def test_tcp_connects_to_validated_ip_and_tls_uses_hostname(self):
        connection, raw, context, _families = self.make_connection()
        connection.connect()
        self.assertEqual(raw.connected_to, (PUBLIC_V4, 443))
        self.assertEqual(context.server_hostname, "origin.example")
        self.assertFalse(context.suppress_ragged_eofs)

    def test_certificate_verification_cannot_be_disabled(self):
        validated = network.validate_https_url("https://origin.example/")
        connection = network.PinnedHTTPSConnection(
            validated,
            PUBLIC_V4,
            timeout=1,
            context=FakeTlsContext(verified=False),
            socket_factory=lambda *_args: FakeRawSocket(),
        )
        with self.assertRaises(network.TlsConnectionError):
            connection.connect()

    def test_peer_mismatch_rejected_and_closed(self):
        connection, _raw, context, _families = self.make_connection(peer=PUBLIC_V4_ALT)
        with self.assertRaises(network.TlsConnectionError):
            connection.connect()
        self.assertTrue(context.tls_socket.closed)

    def test_no_second_dns_lookup(self):
        connection, _raw, _context, _families = self.make_connection()
        with mock.patch.object(network.socket, "getaddrinfo", side_effect=AssertionError("DNS used")):
            connection.connect()

    def test_ipv6_uses_ipv6_socket_and_tuple(self):
        connection, raw, _context, families = self.make_connection(
            address=PUBLIC_V6, peer=PUBLIC_V6
        )
        connection.connect()
        self.assertEqual(families[0][0], socket.AF_INET6)
        self.assertEqual(raw.connected_to, (PUBLIC_V6, 443, 0, 0))

    def test_unsupported_alpn_rejected(self):
        connection, _raw, context, _families = self.make_connection()
        context.tls_socket.protocol = "h2"
        with self.assertRaises(network.TlsConnectionError):
            connection.connect()

    def test_interrupt_during_connect_closes_socket_and_is_not_converted(self):
        connection, raw, _context, _families = self.make_connection()
        with mock.patch.object(raw, "connect", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                connection.connect()
        self.assertTrue(raw.closed)

    def test_interrupt_during_tls_closes_socket_and_is_not_converted(self):
        connection, raw, context, _families = self.make_connection()
        with mock.patch.object(context, "wrap_socket", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                connection.connect()
        self.assertTrue(raw.closed)


class ResolverFlowTests(unittest.TestCase):
    def resolve(self, responses, source="https://first.example/start", **kwargs):
        transport = PlannedTransport(responses)
        result = network.resolve_https_source(
            source,
            resolver=public_resolver,
            connection_factory=transport,
            **kwargs
        )
        return result, transport

    def test_direct_200_and_safe_request_headers(self):
        result, transport = self.resolve([FakeResponse(body=b"hello")])
        self.assertEqual(result.payload, "hello")
        method, target, headers = transport.connections[0].requests[0]
        self.assertEqual((method, target), ("GET", "/start"))
        self.assertEqual(headers["Host"], "first.example")
        self.assertEqual(headers["Accept-Encoding"], "identity")
        self.assertEqual(headers["Connection"], "close")
        for forbidden in ("Cookie", "Authorization", "Proxy-Authorization", "Referer"):
            self.assertNotIn(forbidden, headers)

    def test_public_literal_skips_dns(self):
        transport = PlannedTransport([FakeResponse(body=b"literal")])
        result = network.resolve_https_source(
            "https://{}/".format(PUBLIC_V4),
            resolver=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("DNS used")),
            connection_factory=transport,
        )
        self.assertEqual(result.payload, "literal")
        self.assertEqual(transport.calls[0][1], PUBLIC_V4)

    def test_relative_redirect(self):
        result, transport = self.resolve(
            [FakeResponse(status=302, headers={"Location": "/next"}), FakeResponse(body=b"done")]
        )
        self.assertEqual(result.redirect_count, 1)
        self.assertEqual(transport.connections[1].requests[0][1], "/next")

    def test_absolute_multihop_redirect_resolves_each_host(self):
        resolved_hosts = []

        def resolver(host, _port, *, timeout):
            resolved_hosts.append(host)
            return (PUBLIC_V4,)

        transport = PlannedTransport(
            [
                FakeResponse(status=301, headers={"Location": "https://second.example/two"}),
                FakeResponse(status=308, headers={"Location": "https://third.example/three"}),
                FakeResponse(body=b"done"),
            ]
        )
        result = network.resolve_https_source(
            "https://first.example/one", resolver=resolver, connection_factory=transport
        )
        self.assertEqual(result.redirect_count, 2)
        self.assertEqual(resolved_hosts, ["first.example", "second.example", "third.example"])

    def test_unsafe_redirect_variants_rejected(self):
        locations = (
            "http://second.example/path",
            "https://second.example:444/path",
            "https://user@second.example/path",
            "https://second.example/path#fragment",
        )
        for location in locations:
            with self.subTest(location=location), self.assertRaises(network.RedirectPolicyError):
                self.resolve([FakeResponse(status=302, headers={"Location": location})])

    def test_redirect_loop_detected(self):
        with self.assertRaises(network.RedirectPolicyError):
            self.resolve([FakeResponse(status=302, headers={"Location": "/start"})])

    def test_maximum_redirects_enforced(self):
        responses = [
            FakeResponse(status=302, headers={"Location": "/{}".format(index)})
            for index in range(network.MAX_REDIRECTS + 1)
        ]
        with self.assertRaises(network.RedirectPolicyError):
            self.resolve(responses)

    def test_missing_malformed_and_oversized_location_rejected(self):
        values = (None, "%GG", "/" + "x" * network.MAX_LOCATION_BYTES)
        for value in values:
            headers = {} if value is None else {"Location": value}
            with self.subTest(value=value), self.assertRaises(network.RedirectPolicyError):
                self.resolve([FakeResponse(status=302, headers=headers)])

    def test_redirect_secret_absent_from_error(self):
        secret = "DO_NOT_LEAK_REDIRECT_VALUE"
        with self.assertRaises(network.ProfileNetworkError) as caught:
            self.resolve([FakeResponse(status=302, headers={"Location": "http://" + secret + "/"})])
        self.assertNotIn(secret, str(caught.exception))

    def test_valid_utf8_and_bom(self):
        for body, expected in (("текст".encode("utf-8"), "текст"), (b"\xef\xbb\xbftext", "text")):
            with self.subTest(body=body):
                result, _transport = self.resolve([FakeResponse(body=body)])
                self.assertEqual(result.payload, expected)

    def test_invalid_utf8_rejected(self):
        with self.assertRaises(network.ResponsePolicyError):
            self.resolve([FakeResponse(body=b"\xff")])

    def test_content_length_overflow_rejected_before_read(self):
        read_called = []
        response = FakeResponse(
            body=b"ignored",
            headers={"Content-Length": str(network.MAX_RESPONSE_BYTES + 1)},
            on_read=lambda: read_called.append(True),
        )
        with self.assertRaises(network.ResponsePolicyError):
            self.resolve([response])
        self.assertEqual(read_called, [])

    def test_streaming_overflow_without_content_length(self):
        body = b"x" * 17
        response = FakeResponse(body=body, headers={"Transfer-Encoding": "chunked"})
        with self.assertRaises(network.ResponsePolicyError):
            self.resolve([response], max_response_bytes=16)

    def test_compression_rejected(self):
        for encoding in ("gzip", "deflate", "br"):
            response = FakeResponse(headers={"Content-Encoding": encoding})
            with self.subTest(encoding=encoding), self.assertRaises(network.ResponsePolicyError):
                self.resolve([response])

    def test_non_200_final_status_rejected(self):
        for status in (204, 206, 401, 404, 500):
            with self.subTest(status=status), self.assertRaises(network.ResponsePolicyError):
                self.resolve([FakeResponse(status=status)])

    def test_ambiguous_response_framing_rejected(self):
        response = FakeResponse(
            body=b"x", headers={"Transfer-Encoding": "chunked", "Content-Length": "1"}
        )
        with self.assertRaises(network.ResponsePolicyError):
            self.resolve([response])

    def test_incomplete_declared_body_rejected(self):
        with self.assertRaises(network.ResponsePolicyError):
            self.resolve([FakeResponse(body=b"x", headers={"Content-Length": "2"})])

    def test_timeout_during_connect_is_generic(self):
        def failing_factory(_url, _address, _timeout):
            raise socket.timeout("contains implementation detail")

        with self.assertRaises(network.TlsConnectionError) as caught:
            network.resolve_https_source(
                "https://first.example/",
                resolver=public_resolver,
                connection_factory=failing_factory,
            )
        self.assertNotIn("implementation detail", str(caught.exception))

    def test_timeout_during_read(self):
        now = [0.0]

        def clock():
            return now[0]

        def expire():
            now[0] = 31.0

        response = FakeResponse(read_error=socket.timeout(), on_read=expire)
        with self.assertRaises(network.ResponsePolicyError) as caught:
            self.resolve([response], clock=clock)
        self.assertIn("timed out", str(caught.exception))

    def test_deadline_checked_after_successful_incremental_read(self):
        now = [0.0]

        def clock():
            return now[0]

        response = FakeResponse(body=b"data", on_read=lambda: now.__setitem__(0, 31.0))
        with self.assertRaises(network.ResponsePolicyError) as caught:
            self.resolve([response], clock=clock)
        self.assertIn("timed out", str(caught.exception))

    def test_total_deadline_exhaustion_before_dns(self):
        calls = [0.0, 31.0]

        def clock():
            return calls.pop(0) if calls else 31.0

        with self.assertRaises(network.ResponsePolicyError):
            network.resolve_https_source(
                "https://first.example/",
                resolver=public_resolver,
                connection_factory=PlannedTransport([]),
                clock=clock,
            )

    def test_response_and_connection_close_on_failure(self):
        response = FakeResponse(body=b"\xff")
        transport = PlannedTransport([response])
        with self.assertRaises(network.ResponsePolicyError):
            network.resolve_https_source(
                "https://first.example/",
                resolver=public_resolver,
                connection_factory=transport,
            )
        self.assertTrue(response.closed)
        self.assertTrue(transport.connections[0].closed)

    def test_payload_repr_hides_payload_and_content_type(self):
        secret = "DO_NOT_LEAK_PAYLOAD"
        result, _transport = self.resolve(
            [FakeResponse(body=secret.encode(), headers={"Content-Type": "text/plain; token=secret"})]
        )
        self.assertNotIn(secret, repr(result))
        self.assertNotIn("text/plain", repr(result))

    def test_environment_proxy_has_no_effect(self):
        with mock.patch.dict(
            "os.environ",
            {"HTTPS_PROXY": "http://127.0.0.1:9", "NO_PROXY": ""},
            clear=False,
        ):
            result, transport = self.resolve([FakeResponse(body=b"ok")])
        self.assertEqual(result.payload, "ok")
        self.assertEqual(transport.calls[0][1], PUBLIC_V4)

    def test_failed_address_falls_through_without_new_dns(self):
        resolver_calls = []
        factory_calls = []

        def resolver(host, _port, *, timeout):
            resolver_calls.append(host)
            return (PUBLIC_V4, PUBLIC_V4_ALT)

        response = FakeResponse(body=b"ok")

        def factory(url, address, timeout):
            factory_calls.append(address)
            if address == PUBLIC_V4_ALT:
                raise OSError("synthetic")
            return FakeConnection(response)

        result = network.resolve_https_source(
            "https://first.example/", resolver=resolver, connection_factory=factory
        )
        self.assertEqual(result.payload, "ok")
        self.assertEqual(resolver_calls, ["first.example"])
        self.assertEqual(factory_calls, [PUBLIC_V4_ALT, PUBLIC_V4])

    def test_interrupt_during_request_closes_connection_and_does_not_try_next_ip(self):
        calls = []

        class InterruptingConnection(FakeConnection):
            def request(self, method, target, headers=None):
                raise KeyboardInterrupt

        connection = InterruptingConnection(FakeResponse())

        def factory(_url, address, _timeout):
            calls.append(address)
            return connection

        with self.assertRaises(KeyboardInterrupt):
            network.resolve_https_source(
                "https://first.example/",
                resolver=lambda *_args, **_kwargs: (PUBLIC_V4_ALT, PUBLIC_V4),
                connection_factory=factory,
            )
        self.assertEqual(calls, [PUBLIC_V4_ALT])
        self.assertTrue(connection.closed)

    def test_interrupt_during_getresponse_closes_connection(self):
        connection = FakeConnection(FakeResponse())
        with mock.patch.object(connection, "getresponse", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                network.resolve_https_source(
                    "https://first.example/",
                    resolver=public_resolver,
                    connection_factory=lambda *_args: connection,
                )
        self.assertTrue(connection.closed)

    def test_interrupt_during_body_read_closes_response_and_connection(self):
        response = FakeResponse(read_error=KeyboardInterrupt())
        connection = FakeConnection(response)
        with self.assertRaises(KeyboardInterrupt):
            network.resolve_https_source(
                "https://first.example/",
                resolver=public_resolver,
                connection_factory=lambda *_args: connection,
            )
        self.assertTrue(response.closed)
        self.assertTrue(connection.closed)

    def test_cleanup_failure_does_not_replace_active_cancellation(self):
        interrupt = KeyboardInterrupt()
        response = FakeResponse(read_error=interrupt)
        connection = FakeConnection(response)
        with mock.patch.object(response, "close", side_effect=SystemExit(9)):
            with self.assertRaises(KeyboardInterrupt) as caught:
                network.resolve_https_source(
                    "https://first.example/",
                    resolver=public_resolver,
                    connection_factory=lambda *_args: connection,
                )
        self.assertIs(caught.exception, interrupt)
        self.assertTrue(connection.closed)

    def test_cancellation_never_follows_redirect(self):
        resolver_calls = []
        transport = PlannedTransport(
            [FakeResponse(status=302, headers={"Location": "https://second.example/"})]
        )

        def resolver(host, _port, *, timeout):
            resolver_calls.append(host)
            return (PUBLIC_V4,)

        with mock.patch.object(network, "_single_location", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                network.resolve_https_source(
                    "https://first.example/",
                    resolver=resolver,
                    connection_factory=transport,
                )
        self.assertEqual(resolver_calls, ["first.example"])
        self.assertEqual(len(transport.connections), 1)
        self.assertTrue(transport.connections[0].closed)

    def test_base_exceptions_from_resolver_are_not_swallowed(self):
        for exception_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(exception_type=exception_type.__name__):
                with self.assertRaises(exception_type):
                    network.resolve_https_source(
                        "https://first.example/",
                        resolver=lambda *_args, **_kwargs: (_ for _ in ()).throw(exception_type()),
                        connection_factory=PlannedTransport([]),
                    )


if __name__ == "__main__":
    unittest.main()
