import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "routerkit_routing.py"
    spec = importlib.util.spec_from_file_location("routerkit_routing", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


routing = load_module()


class DomainValidationTests(unittest.TestCase):
    def test_normalizes_domain_prefix_case_and_trailing_dot(self):
        self.assertEqual(
            routing.normalize_domain_suffix(" domain:WWW.Example.COM. "),
            "www.example.com",
        )

    def test_rejects_urls_wildcards_and_ip_addresses(self):
        for value in (
            "https://example.com",
            "*.example.com",
            "example.com/path",
            "1.1.1.1",
            "2001:db8::1",
            "localhost",
        ):
            with self.subTest(value=value), self.assertRaises(routing.RoutingError):
                routing.normalize_domain_suffix(value)


class ServicePackTests(unittest.TestCase):
    def test_kinopoisk_pack_is_hardware_proven_set(self):
        self.assertEqual(
            routing.load_service_pack(ROOT, "kinopoisk"),
            (
                "kinopoisk.ru",
                "kinopoisk-ru.clstorage.net",
                "ott.yandex.ru",
                "ott.yandex.net",
                "strm.yandex.ru",
                "yastatic.net",
                "yndx.net",
                "yccdn.ru",
            ),
        )

    def test_unknown_service_fails_closed(self):
        with self.assertRaises(routing.RoutingError):
            routing.load_service_pack(ROOT, "does-not-exist")


class OverrideStateTests(unittest.TestCase):
    def test_mutations_are_idempotent_and_deterministic(self):
        empty = routing.RoutingOverrides()
        first = routing.mutate_overrides(
            empty,
            ROOT,
            add_services=["kinopoisk", "kinopoisk"],
            add_domains=["example.org", "EXAMPLE.ORG."],
        )
        second = routing.mutate_overrides(
            first,
            ROOT,
            add_services=["kinopoisk"],
            add_domains=["example.org"],
        )
        self.assertEqual(first, second)
        self.assertEqual(first.direct_services, ("kinopoisk",))
        self.assertEqual(first.direct_domains, ("example.org",))

    def test_expansion_keeps_service_domains_before_custom_domains(self):
        state = routing.RoutingOverrides(("kinopoisk",), ("example.org",))
        expanded = routing.expand_direct_domains(ROOT, state)
        self.assertEqual(expanded[-1], "example.org")
        self.assertEqual(expanded[0], "kinopoisk.ru")
        self.assertEqual(len(expanded), 9)

    def test_state_schema_rejects_unknown_fields_and_duplicates(self):
        with self.assertRaises(routing.RoutingError):
            routing.validate_overrides_value(
                {
                    "schema": routing.OVERRIDES_SCHEMA,
                    "direct_services": [],
                    "direct_domains": [],
                    "unexpected": True,
                }
            )
        with self.assertRaises(routing.RoutingError):
            routing.validate_overrides_value(
                {
                    "schema": routing.OVERRIDES_SCHEMA,
                    "direct_services": ["kinopoisk", "kinopoisk"],
                    "direct_domains": [],
                }
            )


class RoutingDocumentTests(unittest.TestCase):
    def setUp(self):
        self.tags = ("socks-primary", "socks-fallback-1", "socks-fallback-2")
        self.base = {
            "routing": {
                "domainStrategy": "AsIs",
                "rules": [
                    {
                        "type": "field",
                        "inboundTag": ["socks-primary"],
                        "outboundTag": "vless-primary",
                    },
                    {
                        "type": "field",
                        "inboundTag": ["socks-fallback-1"],
                        "outboundTag": "vless-fallback-1",
                    },
                    {
                        "type": "field",
                        "inboundTag": ["socks-fallback-2"],
                        "outboundTag": "vless-fallback-2",
                    },
                ],
            }
        }
        self.domains = routing.load_service_pack(ROOT, "kinopoisk")

    def test_direct_rule_is_inserted_before_vless_catchalls(self):
        updated, changed = routing.reconcile_routing_document(
            self.base,
            self.tags,
            current_domains=(),
            desired_domains=self.domains,
        )
        self.assertTrue(changed)
        rules = updated["routing"]["rules"]
        self.assertEqual(rules[0]["outboundTag"], "direct")
        self.assertEqual(rules[0]["domain"][0], "domain:kinopoisk.ru")
        self.assertEqual(rules[1]["outboundTag"], "vless-primary")

    def test_rerun_is_noop(self):
        once, _ = routing.reconcile_routing_document(
            self.base,
            self.tags,
            current_domains=(),
            desired_domains=self.domains,
        )
        twice, changed = routing.reconcile_routing_document(
            once,
            self.tags,
            current_domains=self.domains,
            desired_domains=self.domains,
        )
        self.assertFalse(changed)
        self.assertEqual(once, twice)

    def test_remove_managed_rule_restores_base(self):
        once, _ = routing.reconcile_routing_document(
            self.base,
            self.tags,
            current_domains=(),
            desired_domains=self.domains,
        )
        removed, changed = routing.reconcile_routing_document(
            once,
            self.tags,
            current_domains=self.domains,
            desired_domains=(),
        )
        self.assertTrue(changed)
        self.assertEqual(removed, self.base)

    def test_unmanaged_first_direct_rule_is_rejected(self):
        foreign = {
            "routing": {
                "domainStrategy": "AsIs",
                "rules": [
                    {
                        "type": "field",
                        "inboundTag": list(self.tags),
                        "domain": ["domain:foreign.example"],
                        "outboundTag": "direct",
                    }
                ]
                + self.base["routing"]["rules"],
            }
        }
        with self.assertRaises(routing.RoutingError):
            routing.reconcile_routing_document(
                foreign,
                self.tags,
                current_domains=(),
                desired_domains=self.domains,
            )

    def test_known_live_rule_can_be_inferred_as_kinopoisk_service(self):
        current, _ = routing.reconcile_routing_document(
            self.base,
            self.tags,
            current_domains=(),
            desired_domains=self.domains,
        )
        inferred = routing.infer_known_service_state(ROOT, current, self.tags)
        self.assertEqual(inferred, routing.RoutingOverrides(("kinopoisk",), ()))


class ActiveShapeTests(unittest.TestCase):
    def test_extracts_loopback_socks_tags_and_ports(self):
        value = {
            "inbounds": [
                {"tag": "socks-primary", "listen": "127.0.0.1", "port": 1082, "protocol": "socks"},
                {"tag": "socks-fallback-1", "listen": "127.0.0.1", "port": 1083, "protocol": "socks"},
                {"tag": "api", "listen": "127.0.0.1", "port": 10085, "protocol": "dokodemo-door"},
            ]
        }
        self.assertEqual(
            routing.extract_routerkit_inbounds(value),
            ("socks-primary", "socks-fallback-1"),
        )
        self.assertEqual(routing.extract_routerkit_ports(value), (1082, 1083))

    def test_requires_single_direct_freedom_outbound(self):
        routing.validate_direct_outbound(
            {"outbounds": [{"tag": "direct", "protocol": "freedom"}]}
        )
        with self.assertRaises(routing.RoutingError):
            routing.validate_direct_outbound(
                {
                    "outbounds": [
                        {"tag": "direct", "protocol": "freedom"},
                        {"tag": "direct", "protocol": "freedom"},
                    ]
                }
            )


if __name__ == "__main__":
    unittest.main()
