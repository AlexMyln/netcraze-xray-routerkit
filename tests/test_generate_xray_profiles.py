import base64
import importlib.util
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "generate-xray-profiles.py"
    spec = importlib.util.spec_from_file_location("generate_xray_profiles", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


generator = load_module()


def fake_vless_link(name="Example", host="example.net", security="reality", network="tcp", user_id=None):
    scheme = "vl" + "ess"
    user_id = user_id or str(uuid.uuid4())
    query_parts = [
        f"security={security}",
        f"type={network}",
        "fp=chrome",
        ("pb" + "k") + "=" + "A" * 43,
        ("si" + "d") + "=00",
        f"sni={host}",
        "flow=xtls-rprx-vision",
    ]
    query = "&".join(query_parts)
    return f"{scheme}://{user_id}@{host}:443?{query}#{name}"


class ExtractVlessLinksTests(unittest.TestCase):
    def test_extracts_plain_text_link(self):
        link = fake_vless_link()

        self.assertEqual(generator.extract_vless_links(f"node: {link}"), [link])

    def test_extracts_base64_subscription_link(self):
        link = fake_vless_link()
        encoded = base64.b64encode((link + "\n").encode("utf-8")).decode("ascii")

        self.assertEqual(generator.extract_vless_links(encoded), [link])

    def test_extracts_json_link(self):
        link = fake_vless_link()
        text = json.dumps({"profiles": [{"url": link}]})

        self.assertEqual(generator.extract_vless_links(text), [link])


class ParseVlessTests(unittest.TestCase):
    def test_parses_expected_fields(self):
        node = generator.parse_vless(fake_vless_link())

        self.assertEqual(node["host"], "example.net")
        self.assertEqual(node["port"], 443)
        self.assertEqual(node["security"], "reality")
        self.assertEqual(node["network"], "tcp")
        self.assertEqual(node["sni"], "example.net")
        self.assertEqual(node["fp"], "chrome")
        self.assertEqual(node["pbk"], "A" * 43)
        self.assertEqual(node["sid"], "00")
        self.assertEqual(node["flow"], "xtls-rprx-vision")


class SelectNodeTests(unittest.TestCase):
    def setUp(self):
        self.links = [
            fake_vless_link(name="Alpha", host="alpha.example.net"),
            fake_vless_link(name="Beta", host="beta.example.com"),
            fake_vless_link(name="Grpc", network="grpc"),
        ]

    def test_selects_by_index(self):
        node = generator.select_node(self.links, {"index": 1})

        self.assertEqual(node["name"], "Beta")

    def test_selects_by_name_contains(self):
        node = generator.select_node(self.links, {"name_contains": "alp"})

        self.assertEqual(node["host"], "alpha.example.net")

    def test_selects_by_host_contains(self):
        node = generator.select_node(self.links, {"host_contains": "example.com"})

        self.assertEqual(node["name"], "Beta")

    def test_requires_security_and_network(self):
        node = generator.select_node(
            self.links,
            {
                "require_security": "reality",
                "require_network": "tcp",
                "host_contains": "alpha",
            },
        )

        self.assertEqual(node["network"], "tcp")
        self.assertEqual(node["security"], "reality")


class BuildConfigTests(unittest.TestCase):
    def test_build_inbounds_use_loopback_socks_ports(self):
        profiles = [{"name": "alpha", "port": 1082}, {"name": "beta", "port": 1083}]

        inbounds = generator.build_inbounds(profiles)["inbounds"]

        self.assertEqual([inbound["listen"] for inbound in inbounds], ["127.0.0.1", "127.0.0.1"])
        self.assertEqual([inbound["port"] for inbound in inbounds], [1082, 1083])
        self.assertEqual([inbound["protocol"] for inbound in inbounds], ["socks", "socks"])

    def test_build_routing_maps_inbound_to_expected_outbound(self):
        profiles = [{"name": "alpha", "port": 1082}, {"name": "beta", "port": 1083}]

        rules = generator.build_routing(profiles)["routing"]["rules"]

        self.assertEqual(
            rules,
            [
                {"type": "field", "inboundTag": ["socks-alpha"], "outboundTag": "vless-alpha"},
                {"type": "field", "inboundTag": ["socks-beta"], "outboundTag": "vless-beta"},
            ],
        )

    def test_profiles_document_from_shared_core_generates_three_fragments(self):
        import routerkit_profile_source as core

        nodes = [core.parse_vless(fake_vless_link(name=name, host=f"{name}.example")) for name in ("one", "two", "three")]
        document = core.build_profiles_document(core.select_nodes(nodes, 1, [2, 3]))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profiles = root / "profiles-input.json"
            output = root / "generated"
            core.write_private_json(profiles, document)
            old_argv = sys.argv
            try:
                sys.argv = ["generate-xray-profiles.py", "--profiles", str(profiles), "--out", str(output)]
                self.assertEqual(generator.main(), 0)
            finally:
                sys.argv = old_argv
            self.assertEqual(
                sorted(path.name for path in output.iterdir()),
                ["03_inbounds.json", "04_outbounds.json", "05_routing.json"],
            )

    def test_generator_reexports_shared_parser(self):
        import routerkit_profile_source as core

        self.assertIs(generator.parse_vless, core.parse_vless)


if __name__ == "__main__":
    unittest.main()
