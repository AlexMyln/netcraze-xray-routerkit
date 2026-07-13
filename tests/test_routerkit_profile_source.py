import base64
import contextlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import urllib.request
import urllib.parse
import uuid
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures" / "profile-source"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import routerkit_profile_source as core


def load_cli():
    path = SCRIPTS / "routerkit-profile-source.py"
    name = "routerkit_profile_source_cli"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cli = load_cli()


def fixture_nodes():
    return json.loads((FIXTURES / "nodes.json").read_text(encoding="utf-8"))["nodes"]


def make_link(
    component=None,
    *,
    label=None,
    user_id=None,
    security="reality",
    network="tcp",
    public_key=None,
    short_id=None,
    flow="xtls-rprx-vision",
    port=None,
):
    component = component or fixture_nodes()[0]
    scheme = "vl" + "ess"
    user_id = user_id or str(uuid.uuid4())
    public_key = public_key if public_key is not None else component["key_character"] * 43
    short_id = short_id if short_id is not None else component["short_id_pair"]
    port = component["port"] if port is None else port
    label = component["label"] if label is None else label
    params = [
        f"security={security}",
        f"type={network}",
        "fp=chrome",
        ("pb" + "k") + f"={public_key}",
        ("si" + "d") + f"={short_id}",
        f"sni={component['host']}",
        f"flow={flow}",
        "spx=%2F",
    ]
    return (
        f"{scheme}://{user_id}@{component['host']}:{port}?"
        + "&".join(params)
        + "#"
        + urllib.parse.quote(label, safe="")
    )


class PayloadParsingTests(unittest.TestCase):
    def test_raw_single_link(self):
        link = make_link()
        self.assertEqual(core.extract_vless_links(link), [link])
        self.assertEqual(len(core.parse_compatible_nodes(link)), 1)

    def test_newline_separated_links_preserve_order(self):
        links = [make_link(item) for item in fixture_nodes()[:2]]
        nodes = core.parse_compatible_nodes("\n".join(links))
        self.assertEqual([node.name for node in nodes], ["Example Alpha", "Example Beta"])

    def test_base64_subscription_text(self):
        link = make_link()
        payload = base64.b64encode((link + "\n").encode()).decode()
        self.assertEqual(core.extract_vless_links(payload), [link])

    def test_nested_json(self):
        link = make_link()
        payload = json.dumps({"outer": [{"inner": link}]})
        self.assertEqual(core.extract_vless_links(payload), [link])

    def test_base64_json(self):
        link = make_link()
        encoded = base64.b64encode(json.dumps({"node": link}).encode()).decode()
        self.assertEqual(core.extract_vless_links(encoded), [link])

    def test_malformed_payload_is_rejected_generically(self):
        with self.assertRaises(core.PayloadValidationError) as caught:
            core.parse_compatible_nodes("not a profile payload")
        self.assertNotIn("not a profile", str(caught.exception))

    def test_oversized_payload(self):
        with self.assertRaises(core.PayloadValidationError):
            core.extract_vless_links("x" * 65, max_payload_bytes=64)

    def test_excessive_json_nesting(self):
        value = make_link()
        for _ in range(core.MAX_JSON_DEPTH + 2):
            value = [value]
        with self.assertRaises(core.PayloadValidationError):
            core.extract_vless_links(json.dumps(value))

    def test_candidate_count_limit(self):
        links = [make_link(item) for item in fixture_nodes()]
        with mock.patch.object(core, "MAX_CANDIDATE_LINKS", 2):
            with self.assertRaises(core.PayloadValidationError):
                core.extract_vless_links("\n".join(links))

    def test_incompatible_security(self):
        with self.assertRaises(core.NodeValidationError):
            core.parse_vless(make_link(security="tls"))

    def test_incompatible_network(self):
        with self.assertRaises(core.NodeValidationError):
            core.parse_vless(make_link(network="grpc"))

    def test_raw_network_alias_normalizes_to_tcp(self):
        self.assertEqual(core.parse_vless(make_link(network="raw")).network, "tcp")

    def test_missing_public_key(self):
        with self.assertRaises(core.NodeValidationError):
            core.parse_vless(make_link(public_key=""))

    def test_invalid_port(self):
        with self.assertRaises(core.NodeValidationError):
            core.parse_vless(make_link(port=70000))

    def test_malformed_identifier(self):
        with self.assertRaises(core.NodeValidationError):
            core.parse_vless(make_link(user_id="not-an-identifier"))

    def test_password_in_user_information_is_rejected(self):
        link = make_link()
        identifier, rest = link.split("@", 1)
        with self.assertRaises(core.NodeValidationError):
            core.parse_vless(identifier + ":password@" + rest)

    def test_invalid_short_id(self):
        with self.assertRaises(core.NodeValidationError):
            core.parse_vless(make_link(short_id="xyz"))

    def test_exact_deduplication(self):
        link = make_link()
        self.assertEqual(core.extract_vless_links(link + "\n" + link), [link])

    def test_semantic_deduplication_preserves_first_label(self):
        identifier = str(uuid.uuid4())
        first = make_link(user_id=identifier, label="First")
        second = make_link(user_id=identifier, label="Second")
        nodes = core.parse_compatible_nodes(first + "\n" + second)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].name, "First")


class SecretSafeSummaryTests(unittest.TestCase):
    def node(self, label):
        return core.parse_vless(make_link(label=label))

    def test_normal_label(self):
        self.assertEqual(core.safe_node_summary(self.node("Example Region"), 1)["label"], "Example Region")

    def test_empty_label_uses_fallback(self):
        self.assertEqual(core.safe_node_summary(self.node(""), 2)["label"], "Node 2")

    def test_control_characters_are_stripped(self):
        label = "Region" + chr(7) + " Name"
        self.assertEqual(core.safe_node_summary(self.node(label), 1)["label"], "Region Name")

    def test_ansi_escape_is_stripped(self):
        label = chr(27) + "[31mRegion" + chr(27) + "[0m"
        self.assertEqual(core.safe_node_summary(self.node(label), 1)["label"], "Region")

    def test_uri_like_label_uses_fallback(self):
        self.assertEqual(core.safe_node_summary(self.node("see https://example.invalid"), 1)["label"], "Node 1")

    def test_uuid_like_label_uses_fallback(self):
        self.assertEqual(core.safe_node_summary(self.node(str(uuid.uuid4())), 1)["label"], "Node 1")

    def test_long_token_label_uses_fallback(self):
        self.assertEqual(core.safe_node_summary(self.node("Z" * 40), 1)["label"], "Node 1")

    def test_credential_like_label_uses_fallback(self):
        self.assertEqual(core.safe_node_summary(self.node("user@example.invalid"), 1)["label"], "Node 1")

    def test_synthetic_marker_absent_from_text_and_json(self):
        marker = "DO_NOT_" + "LEAK_SECRET_VALUE"
        node = self.node(marker)
        summary = core.safe_node_summary(node, 1)
        text = f"{summary} {json.dumps(summary)}"
        self.assertNotIn(marker, text)

    def test_repr_hides_secret_fields(self):
        marker = "DO_NOT_" + "LEAK_SECRET_VALUE"
        node = self.node(marker)
        rendered = repr(node)
        for value in (node.raw_link, node.uuid, node.host, node.sni, node.pbk, node.sid, node.spx, marker):
            self.assertNotIn(value, rendered)


class SelectionAndOutputTests(unittest.TestCase):
    def setUp(self):
        self.nodes = [core.parse_vless(make_link(item)) for item in fixture_nodes()]

    def test_primary_only(self):
        selected = core.select_nodes(self.nodes, 1)
        self.assertIs(selected.primary, self.nodes[0])
        self.assertEqual(selected.fallbacks, ())

    def test_primary_and_two_fallbacks(self):
        selected = core.select_nodes(self.nodes, 2, [1, 3])
        self.assertEqual(selected.fallbacks, (self.nodes[0], self.nodes[2]))

    def test_primary_and_one_fallback(self):
        selected = core.select_nodes(self.nodes, 1, [2])
        self.assertEqual(selected.fallbacks, (self.nodes[1],))

    def test_duplicate_and_primary_repeated_are_rejected(self):
        for fallbacks in ([2, 2], [1]):
            with self.subTest(fallbacks=fallbacks):
                with self.assertRaises(core.SelectionError):
                    core.select_nodes(self.nodes, 1, fallbacks)

    def test_more_than_two_fallbacks_is_rejected(self):
        with self.assertRaises(core.SelectionError):
            core.select_nodes(self.nodes, 1, [2, 3, 2])

    def test_out_of_range_is_rejected(self):
        with self.assertRaises(core.SelectionError):
            core.select_nodes(self.nodes, 4)

    def test_document_has_deterministic_names_ports_and_schema(self):
        document = core.build_profiles_document(core.select_nodes(self.nodes, 1, [2, 3]))
        profiles = document["profiles"]
        self.assertEqual([item["name"] for item in profiles], ["primary", "fallback-1", "fallback-2"])
        self.assertEqual([item["port"] for item in profiles], [1082, 1083, 1084])
        self.assertTrue(all(item["select"]["index"] == 0 for item in profiles))

    def test_private_atomic_write_and_overwrite(self):
        document = core.build_profiles_document(core.select_nodes(self.nodes, 1))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "profiles.json"
            core.write_private_json(output, document)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(list(Path(directory).glob(".profiles.json.*")), [])
            with self.assertRaises(core.OutputExistsError):
                core.write_private_json(output, document)
            core.write_private_json(output, document, overwrite=True)
            self.assertEqual(list(Path(directory).glob(".profiles.json.*")), [])


class CliSafetyTests(unittest.TestCase):
    def run_cli(self, argv, *, stdin_values=()):
        stdout = io.StringIO()
        stderr = io.StringIO()
        values = iter(stdin_values)
        with mock.patch("builtins.input", side_effect=lambda _prompt="": next(values)):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = cli.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_hidden_input_does_not_echo_payload(self):
        link = make_link(label="Hidden Example")
        with mock.patch.object(cli.getpass, "getpass", return_value=link):
            code, stdout, stderr = self.run_cli(["--list"])
        self.assertEqual(code, 0)
        self.assertNotIn(link, stdout + stderr)

    def test_environment_source_is_secret_safe(self):
        link = make_link(label="Environment Example")
        with mock.patch.dict(os.environ, {"ROUTERKIT_TEST_SOURCE": link}):
            code, stdout, stderr = self.run_cli(["--source-env", "ROUTERKIT_TEST_SOURCE", "--list"])
        self.assertEqual(code, 0)
        self.assertNotIn(link, stdout + stderr)

    def test_file_source_list_and_dry_run_create_no_output(self):
        link = make_link()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.txt"
            output = Path(directory) / "profiles.json"
            source.write_text(link, encoding="utf-8")
            code, _, _ = self.run_cli(["--source-file", str(source), "--list", "--output", str(output)])
            self.assertEqual(code, 0)
            self.assertFalse(output.exists())
            code, _, _ = self.run_cli([
                "--source-file", str(source), "--primary-index", "1", "--dry-run", "--output", str(output)
            ])
            self.assertEqual(code, 0)
            self.assertFalse(output.exists())

    def test_https_and_other_url_schemes_return_two_without_network(self):
        for value, message in (("https://example.invalid/source", cli.HTTPS_MESSAGE), ("http://example.invalid/source", cli.UNSUPPORTED_URL_MESSAGE)):
            with self.subTest(value=value):
                with mock.patch.object(cli.getpass, "getpass", return_value=value):
                    with mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network attempted")):
                        code, stdout, stderr = self.run_cli(["--list"])
                self.assertEqual(code, 2)
                self.assertIn(message, stderr)
                self.assertNotIn(value, stdout + stderr)

    def test_json_listing_does_not_expose_marker(self):
        marker = "DO_NOT_" + "LEAK_SECRET_VALUE"
        link = make_link(label=marker)
        with mock.patch.object(cli.getpass, "getpass", return_value=link):
            code, stdout, stderr = self.run_cli(["--list", "--json"])
        self.assertEqual(code, 0)
        json.loads(stdout)
        self.assertNotIn(marker, stdout + stderr)

    def test_json_without_list_is_cli_error(self):
        with mock.patch.object(cli.getpass, "getpass") as hidden:
            code, _, _ = self.run_cli(["--json"])
        self.assertEqual(code, 2)
        hidden.assert_not_called()

    def test_no_raw_source_value_argument_exists(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                cli.build_parser().parse_args(["--source-value", "not-a-real-payload"])
        self.assertEqual(caught.exception.code, 2)

    def test_fallback_without_primary_is_rejected_before_input(self):
        with mock.patch.object(cli.getpass, "getpass") as hidden:
            code, _, _ = self.run_cli(["--fallback-index", "2"])
        self.assertEqual(code, 2)
        hidden.assert_not_called()

    def test_cancellation_writes_nothing(self):
        link = make_link()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "profiles.json"
            with mock.patch.object(cli.getpass, "getpass", return_value=link):
                code, _, _ = self.run_cli(["--output", str(output)], stdin_values=["q"])
            self.assertEqual(code, 1)
            self.assertFalse(output.exists())

    def test_write_and_force_rules(self):
        link = make_link()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "profiles.json"
            with mock.patch.object(cli.getpass, "getpass", return_value=link):
                code, _, _ = self.run_cli(["--primary-index", "1", "--yes", "--output", str(output)])
            self.assertEqual(code, 0)
            with mock.patch.object(cli.getpass, "getpass", return_value=link):
                code, _, _ = self.run_cli(["--primary-index", "1", "--yes", "--output", str(output)])
            self.assertEqual(code, 2)
            with mock.patch.object(cli.getpass, "getpass", return_value=link):
                code, _, _ = self.run_cli(["--primary-index", "1", "--yes", "--force", "--output", str(output)])
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
