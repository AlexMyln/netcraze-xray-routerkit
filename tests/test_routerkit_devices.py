import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures" / "devices"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import routerkit_devices as devices


def fixture_text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def mixed_result():
    return devices.parse_fixture_inventory(fixture_text("mixed-inventory.json"))


class DeviceNormalizationTests(unittest.TestCase):
    def test_merges_stable_identity_across_sources_and_preserves_policy(self):
        result = mixed_result()
        tvs = [item for item in result.devices if item.display_name == "Living Room TV"]

        self.assertEqual(len(tvs), 2)
        merged = [item for item in tvs if "2001:db8::10" in item.addresses][0]
        self.assertEqual(merged.stable_identifier, "02:00:5e:00:00:10")
        self.assertEqual(merged.online_state, "online")
        self.assertEqual(merged.connection_type, "wifi")
        self.assertEqual(merged.wifi_band, "5 GHz")
        self.assertEqual(merged.existing_policy, "kids-media")
        self.assertEqual(set(merged.sources), {"dhcp_leases", "wifi_associations"})
        self.assertTrue(merged.selectable)

    def test_same_ip_with_different_stable_ids_stays_split(self):
        result = mixed_result()
        sharing = [item for item in result.devices if "192.0.2.10" in item.addresses]

        self.assertEqual(len(sharing), 2)
        self.assertEqual(
            sorted(item.display_name for item in sharing),
            ["Kitchen Speaker", "Living Room TV"],
        )

    def test_same_name_alone_does_not_merge_devices(self):
        result = mixed_result()
        tvs = [item for item in result.devices if item.display_name == "Living Room TV"]

        self.assertEqual(len(tvs), 2)
        self.assertNotEqual(tvs[0].stable_identifier, tvs[1].stable_identifier)

    def test_multiple_ips_and_fresh_online_evidence_win(self):
        result = mixed_result()
        office = [item for item in result.devices if item.display_name == "Office PC"][0]

        self.assertEqual(office.online_state, "online")
        self.assertEqual(office.connection_type, "ethernet")
        self.assertEqual(office.addresses, ("198.51.100.20", "198.51.100.21"))

    def test_weak_ip_only_identity_is_not_selectable(self):
        result = mixed_result()
        weak = [item for item in result.devices if item.display_name == "Transient Lease"][0]

        self.assertFalse(weak.selectable)
        self.assertEqual(weak.selection_block_reason, "stable identifier unavailable")

    def test_malformed_source_errors_are_sanitized(self):
        result = mixed_result()

        self.assertEqual(result.adapter_state, devices.STATE_MALFORMED_OUTPUT)
        self.assertTrue(any("malformed record skipped" in item for item in result.errors))
        self.assertFalse(any("02:00:5e" in item for item in result.errors))

    def test_conflicting_vendor_identity_blocks_selection(self):
        result = devices.parse_fixture_inventory(fixture_text("conflict-inventory.json"))

        self.assertEqual(len(result.devices), 2)
        self.assertTrue(all(item.conflict for item in result.devices))
        self.assertTrue(all(not item.selectable for item in result.devices))
        self.assertTrue(
            all(item.selection_block_reason == "conflicting stable identity" for item in result.devices)
        )

    def test_sorting_is_deterministic_online_unknown_offline(self):
        result = mixed_result()
        states = [item.online_state for item in result.devices]

        self.assertEqual(states, sorted(states, key={"online": 0, "unknown": 1, "offline": 2}.get))


class DeviceSelectionTests(unittest.TestCase):
    def test_zero_blank_and_eof_mean_no_assignment(self):
        result = mixed_result()

        self.assertFalse(devices.select_device(result, 0).selected)
        self.assertFalse(devices.prompt_for_selection(result, input_fn=lambda prompt: "").selected)

        def eof(_prompt):
            raise EOFError

        self.assertFalse(devices.prompt_for_selection(result, input_fn=eof).selected)

    def test_valid_selection_uses_opaque_token(self):
        result = mixed_result()
        first_selectable = next(index for index, item in enumerate(result.devices, start=1) if item.selectable)

        selection = devices.select_device(result, first_selectable)

        self.assertTrue(selection.selected)
        self.assertTrue(selection.token.startswith("routerkit-device-selection-v1:"))
        self.assertNotIn("02:00:5e", selection.token)

    def test_invalid_or_weak_selection_fails_safely(self):
        result = mixed_result()
        weak_index = next(index for index, item in enumerate(result.devices, start=1) if not item.selectable)

        with self.assertRaises(devices.DeviceSelectionError):
            devices.select_device(result, 99)
        with self.assertRaises(devices.DeviceSelectionError):
            devices.select_device(result, weak_index)


class DevicePrivacyTests(unittest.TestCase):
    def test_full_json_labels_sensitive_fields(self):
        payload = json.loads(devices.render_json(mixed_result()))
        first = payload["devices"][0]

        self.assertEqual(payload["sensitivity"], devices.SENSITIVITY_LOCAL)
        self.assertEqual(first["display_name_sensitivity"], devices.SENSITIVITY_LOCAL)
        self.assertEqual(first["addresses_sensitivity"], devices.SENSITIVITY_LOCAL)
        self.assertIn("stable_identifier_sensitivity", first)

    def test_public_evidence_masks_local_identifiers(self):
        rendered = devices.render_json(
            mixed_result(),
            public_evidence=True,
            redaction_salt="test-salt",
        )
        payload = json.loads(rendered)

        self.assertEqual(payload["sensitivity"], devices.SENSITIVITY_PUBLIC)
        self.assertNotIn("Living Room TV", rendered)
        self.assertNotIn("192.0.2.10", rendered)
        self.assertNotIn("02:00:5e:00:00:10", rendered)
        self.assertNotIn("dev-", rendered)
        self.assertIn("device_count", payload)
        self.assertIn("hmac-sha256:", rendered)
        self.assertTrue(payload["devices"][0]["record_id"].startswith("public-device-"))


class DeviceFixtureSchemaTests(unittest.TestCase):
    def test_unsupported_fields_are_reported_without_raw_output(self):
        result = devices.parse_fixture_inventory(fixture_text("unsupported-field.json"))

        self.assertEqual(result.adapter_state, devices.STATE_MALFORMED_OUTPUT)
        self.assertEqual(result.devices, ())
        self.assertEqual(result.errors, ("malformed record skipped from source synthetic-extra-field",))

    def test_invalid_json_fails_generically(self):
        with self.assertRaises(devices.DeviceDiscoveryError) as caught:
            devices.parse_fixture_inventory("{not json")

        self.assertIn("not valid JSON", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
