import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import routerkit_artifact_network as network


PINNED = "https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-arm64-v8a.zip"
PUBLIC_ADDRESS = "93.184.216.34"


class Headers:
    def __init__(self, values):
        self.values = {name.lower(): list(items) for name, items in values.items()}

    def get_all(self, name, default=None):
        return self.values.get(name.lower(), default)


class Response:
    def __init__(self, status=200, body=b"archive", headers=None):
        self.status = status
        self.body = body
        self.offset = 0
        self.header_values = {name.lower(): value for name, value in (headers or {}).items()}
        multi = {}
        for name, value in (headers or {}).items():
            multi[name] = value if isinstance(value, list) else [value]
        self.headers = Headers(multi)
        self.closed = False

    def getheader(self, name):
        value = self.header_values.get(name.lower())
        if isinstance(value, list):
            return value[0] if value else None
        return value

    def read1(self, amount):
        chunk = self.body[self.offset : self.offset + amount]
        self.offset += len(chunk)
        return chunk

    def close(self):
        self.closed = True


class Connection:
    def __init__(self, response, requests):
        self.response = response
        self.requests = requests
        self.sock = None

    def request(self, method, target, headers):
        self.requests.append((method, target, dict(headers)))

    def getresponse(self):
        return self.response

    def close(self):
        pass


def factory_for(responses, requests):
    remaining = list(responses)

    def factory(validated, address, timeout):
        if address != PUBLIC_ADDRESS:
            raise AssertionError("unexpected address")
        return Connection(remaining.pop(0), requests)

    return factory


def resolver(hostname, port, timeout):
    return [PUBLIC_ADDRESS]


class ArtifactNetworkTests(unittest.TestCase):
    def download(self, responses, *, source=PINNED, expected=PINNED, maximum=1024):
        requests = []
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        destination = Path(temporary.name) / "archive.zip"
        result = network.download_pinned_archive(
            source,
            destination,
            expected_url=expected,
            resolver=resolver,
            connection_factory=factory_for(responses, requests),
            max_archive_bytes=maximum,
        )
        return result, destination, requests

    def test_streams_exact_manifest_url_without_proxy_or_auth_headers(self):
        body = b"synthetic archive bytes"
        result, destination, requests = self.download(
            [Response(body=body, headers={"Content-Length": str(len(body))})]
        )
        self.assertEqual(destination.read_bytes(), body)
        self.assertEqual(result.sha256, hashlib.sha256(body).hexdigest())
        headers = {name.lower(): value for name, value in requests[0][2].items()}
        for forbidden in ("authorization", "proxy-authorization", "cookie", "referer"):
            self.assertNotIn(forbidden, headers)
        self.assertEqual(headers["accept-encoding"], "identity")

    def test_initial_url_must_match_manifest_exactly(self):
        with self.assertRaises(network.ArtifactNetworkError):
            self.download([Response()], source=PINNED + "?token=secret")

    def test_redirect_to_githubusercontent_subdomain_is_allowed(self):
        redirected = "https://objects.githubusercontent.com/release.zip?token=SYNTHETIC_SECRET_MARKER"
        first = Response(status=302, headers={"Location": redirected})
        result, destination, requests = self.download([first, Response(body=b"ok")])
        self.assertEqual(result.redirect_count, 1)
        self.assertEqual(destination.read_bytes(), b"ok")
        self.assertIn("token=SYNTHETIC_SECRET_MARKER", requests[1][1])

    def test_suffix_confusion_and_redirect_downgrade_are_rejected_without_query_echo(self):
        locations = (
            "https://githubusercontent.com.example.invalid/file?token=SYNTHETIC_SECRET_MARKER",
            "http://objects.githubusercontent.com/file?token=SYNTHETIC_SECRET_MARKER",
        )
        for location in locations:
            with self.subTest(location=location), self.assertRaises(
                network.ArtifactNetworkError
            ) as raised:
                self.download([Response(status=302, headers={"Location": location})])
            self.assertNotIn("SYNTHETIC_SECRET_MARKER", str(raised.exception))

    def test_private_or_mixed_dns_answers_fail_closed(self):
        resolvers = (
            lambda *args, **kwargs: ["127.0.0.1"],
            lambda *args, **kwargs: [PUBLIC_ADDRESS, "10.0.0.1"],
        )
        for injected in resolvers:
            with self.subTest(injected=injected), tempfile.TemporaryDirectory() as tmp:
                destination = Path(tmp) / "archive.zip"
                with self.assertRaises(network.ArtifactNetworkError):
                    network.download_pinned_archive(
                        PINNED,
                        destination,
                        expected_url=PINNED,
                        resolver=injected,
                        connection_factory=factory_for([Response()], []),
                    )
                self.assertFalse(destination.exists())

    def test_content_length_and_streamed_size_bounds_delete_partial_file(self):
        cases = (
            Response(body=b"", headers={"Content-Length": "9"}),
            Response(body=b"123456789"),
        )
        for response in cases:
            with self.subTest(response=response), tempfile.TemporaryDirectory() as tmp:
                destination = Path(tmp) / "archive.zip"
                with self.assertRaises(network.ArtifactNetworkError):
                    network.download_pinned_archive(
                        PINNED,
                        destination,
                        expected_url=PINNED,
                        resolver=resolver,
                        connection_factory=factory_for([response], []),
                        max_archive_bytes=8,
                    )
                self.assertFalse(destination.exists())

    def test_compressed_response_is_rejected(self):
        with self.assertRaises(network.ArtifactNetworkError):
            self.download([Response(headers={"Content-Encoding": "gzip"})])

    def test_multiple_or_control_character_location_is_rejected(self):
        responses = (
            Response(status=302, headers={"Location": ["https://github.com/a", "https://github.com/b"]}),
            Response(status=302, headers={"Location": "https://github.com/a\nInjected: yes"}),
        )
        for response in responses:
            with self.subTest(response=response), self.assertRaises(network.ArtifactNetworkError):
                self.download([response])

    def test_redirect_loop_and_count_are_bounded(self):
        loop = Response(status=302, headers={"Location": PINNED})
        with self.assertRaises(network.ArtifactNetworkError):
            self.download([loop])
        chain = [
            Response(
                status=302,
                headers={"Location": "https://github.com/XTLS/Xray-core/releases/download/v26.3.27/{}.zip".format(index)},
            )
            for index in range(2)
        ]
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(network.ArtifactNetworkError):
            network.download_pinned_archive(
                PINNED,
                Path(tmp) / "archive.zip",
                expected_url=PINNED,
                resolver=resolver,
                connection_factory=factory_for(chain, []),
                max_redirects=1,
            )


if __name__ == "__main__":
    unittest.main()
