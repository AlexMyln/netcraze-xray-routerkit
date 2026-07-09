import contextlib
import io
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "routerkit-plan.py"
    spec = importlib.util.spec_from_file_location("routerkit_plan", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


plan_module = load_module()


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def create_generated_dir(root, outbound=None):
    generated = Path(root) / "generated"
    generated.mkdir()

    write_json(
        generated / "03_inbounds.json",
        {
            "inbounds": [
                {
                    "tag": "socks-alpha",
                    "listen": "127.0.0.1",
                    "port": 1082,
                    "protocol": "socks",
                },
                {
                    "tag": "socks-beta",
                    "listen": "::1",
                    "port": 1083,
                    "protocol": "socks",
                },
            ]
        },
    )
    write_json(
        generated / "04_outbounds.json",
        outbound
        or {
            "outbounds": [
                {
                    "tag": "vless-alpha",
                    "protocol": "vless",
                    "settings": {
                        "vnext": [
                            {
                                "address": "example.net",
                                "port": 443,
                                "users": [{"id": "example-user", "encryption": "none"}],
                            }
                        ]
                    },
                },
                {"tag": "direct", "protocol": "freedom"},
            ]
        },
    )
    write_json(
        generated / "05_routing.json",
        {
            "routing": {
                "rules": [
                    {
                        "type": "field",
                        "inboundTag": ["socks-alpha"],
                        "outboundTag": "vless-alpha",
                    }
                ]
            }
        },
    )
    return generated


class RouterkitPlanTests(unittest.TestCase):
    def test_valid_plan_contains_install_profiles_and_routing(self):
        with tempfile.TemporaryDirectory() as tmp:
            generated = create_generated_dir(tmp)

            plan = plan_module.build_plan(generated, Path("/opt"))
            text = plan_module.render_text_plan(plan)

            self.assertFalse(plan["errors"])
            self.assertIn("03_inbounds.json -> /opt/etc/xray/configs/03_inbounds.json", text)
            self.assertIn("templates/S23xray-direct -> /opt/etc/init.d/S23xray-direct", text)
            self.assertIn("tag=socks-alpha listen=127.0.0.1 port=1082 protocol=socks", text)
            self.assertIn("socks-alpha -> vless-alpha", text)
            self.assertIn("/opt/etc/init.d/S24xray", text)

    def test_secret_bearing_outbound_values_are_suppressed(self):
        secret_id = "example.user.identifier.value"
        public_key = "fake.public.key.value.for.tests"
        short_id = "fake.short.id.value"
        spider_x = "fake.spider.path.value"
        outbound = {
            "outbounds": [
                {
                    "tag": "vless-alpha",
                    "protocol": "vless",
                    "settings": {
                        "vnext": [
                            {
                                "address": "example.net",
                                "port": 443,
                                "users": [{"id": secret_id, "encryption": "none"}],
                            }
                        ]
                    },
                    "streamSettings": {
                        "security": "reality",
                        "realitySettings": {
                            "publicKey": public_key,
                            "shortId": short_id,
                            "spiderX": spider_x,
                        },
                    },
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            generated = create_generated_dir(tmp, outbound=outbound)

            text = plan_module.render_text_plan(plan_module.build_plan(generated, Path("/opt")))

            self.assertIn("secret-bearing outbound fields detected and suppressed", text)
            for forbidden in [secret_id, public_key, short_id, spider_x, "example.net"]:
                self.assertNotIn(forbidden, text)

    def test_loopback_validation_accepts_only_loopback_listeners(self):
        allowed = [
            {"tag": "ipv4", "listen": "127.0.0.1"},
            {"tag": "ipv6", "listen": "::1"},
        ]
        self.assertFalse(plan_module.validate_loopback_inbounds(allowed)["errors"])

        for listen in ["0.0.0.0", "192.0.2.10"]:
            with self.subTest(listen=listen):
                result = plan_module.validate_loopback_inbounds([{"tag": "bad", "listen": listen}])
                self.assertTrue(result["errors"])

        missing = plan_module.validate_loopback_inbounds([{"tag": "missing"}])
        self.assertTrue(missing["warnings"])
        self.assertFalse(missing["errors"])

    def test_missing_generated_file_warns_by_default_and_fails_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            generated = Path(tmp) / "generated"
            generated.mkdir()
            write_json(
                generated / "03_inbounds.json",
                {"inbounds": [{"tag": "socks-alpha", "listen": "127.0.0.1"}]},
            )
            write_json(
                generated / "05_routing.json",
                {"routing": {"rules": []}},
            )

            default_plan = plan_module.build_plan(generated, Path("/opt"))
            self.assertFalse(default_plan["errors"])
            self.assertTrue(default_plan["warnings"])
            self.assertIn("04_outbounds.json", plan_module.render_text_plan(default_plan))

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = plan_module.main(["--generated", str(generated), "--strict"])
            self.assertEqual(code, 1)

    def test_json_mode_is_valid_and_secret_safe(self):
        secret_id = "example.user.identifier.value"
        public_key = "fake.public.key.value.for.tests"
        outbound = {
            "outbounds": [
                {
                    "tag": "vless-alpha",
                    "protocol": "vless",
                    "settings": {"vnext": [{"address": "example.com", "users": [{"id": secret_id}]}]},
                    "streamSettings": {"realitySettings": {"publicKey": public_key}},
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            generated = create_generated_dir(tmp, outbound=outbound)
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                code = plan_module.main(["--generated", str(generated), "--json"])

            output = stdout.getvalue()
            data = json.loads(output)

            self.assertEqual(code, 0)
            self.assertEqual(data["outbounds"], [{"tag": "vless-alpha", "protocol": "vless", "secret_fields_suppressed": True}])
            self.assertNotIn(secret_id, output)
            self.assertNotIn(public_key, output)
            self.assertNotIn("example.com", output)


if __name__ == "__main__":
    unittest.main()
