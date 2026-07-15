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


def supported_inventory_text(source_name="synthetic-supported", extra_record=None):
    records = [
        {
            "source_record_id": "dhcp-tv",
            "display_name": "Living Room TV",
            "addresses": ["192.0.2.10"],
            "stable_identifier": "02:00:5e:00:00:10",
            "stable_identifier_type": "mac",
            "online_state": "online",
            "connection_type": "wifi",
        }
    ]
    if extra_record is not None:
        records.append(extra_record)
    return json.dumps(
        {
            "schema": devices.FIXTURE_SCHEMA,
            "sources": [
                {
                    "name": source_name,
                    "kind": "dhcp_leases",
                    "state": devices.STATE_SUPPORTED,
                    "confidence": "fixture",
                    "records": records,
                }
            ],
        }
    )


def supported_result():
    return devices.parse_fixture_inventory(supported_inventory_text())


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

    def test_untrusted_identity_types_are_displayable_but_not_selectable(self):
        result = devices.parse_fixture_inventory(
            json.dumps(
                {
                    "schema": devices.FIXTURE_SCHEMA,
                    "sources": [
                        {
                            "name": "synthetic-identity",
                            "kind": "dhcp_leases",
                            "state": devices.STATE_SUPPORTED,
                            "records": [
                                {
                                    "source_record_id": "unknown",
                                    "display_name": "Unknown Identity",
                                    "addresses": ["192.0.2.80"],
                                    "stable_identifier": "opaque-device-id",
                                    "stable_identifier_type": "unknown",
                                },
                                {
                                    "source_record_id": "vendor-only",
                                    "display_name": "Vendor Only",
                                    "vendor_record_id": "vendor-123",
                                },
                                {
                                    "source_record_id": "ip-only",
                                    "display_name": "IP Only",
                                    "addresses": ["192.0.2.81"],
                                },
                            ],
                        }
                    ],
                }
            )
        )

        by_name = {item.display_name: item for item in result.devices}
        self.assertFalse(by_name["Unknown Identity"].selectable)
        self.assertEqual(
            by_name["Unknown Identity"].selection_block_reason,
            "assignment-stable identifier unavailable",
        )
        self.assertFalse(by_name["Vendor Only"].selectable)
        self.assertEqual(
            by_name["Vendor Only"].selection_block_reason,
            "assignment-stable identifier unavailable",
        )
        self.assertFalse(by_name["IP Only"].selectable)
        self.assertEqual(by_name["IP Only"].selection_block_reason, "stable identifier unavailable")

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
        result = supported_result()
        first_selectable = next(index for index, item in enumerate(result.devices, start=1) if item.selectable)

        selection = devices.select_device(result, first_selectable, token_factory=lambda _bytes: "unit-test-token")

        self.assertTrue(selection.selected)
        self.assertTrue(selection.token.startswith("routerkit-device-selection-v1:"))
        self.assertIn("unit-test-token", selection.token)
        self.assertNotIn("02:00:5e", selection.token)

    def test_invalid_or_weak_selection_fails_safely(self):
        result = mixed_result()
        weak_index = next(index for index, item in enumerate(result.devices, start=1) if not item.selectable)

        with self.assertRaises(devices.DeviceSelectionError):
            devices.select_device(result, 99)
        with self.assertRaises(devices.DeviceSelectionError):
            devices.select_device(result, weak_index)

    def test_degraded_inventory_blocks_nonzero_selection(self):
        result = mixed_result()
        self.assertEqual(result.adapter_state, devices.STATE_MALFORMED_OUTPUT)
        selectable = next(index for index, item in enumerate(result.devices, start=1) if item.selectable)

        with self.assertRaises(devices.DeviceSelectionError) as caught:
            devices.select_device(result, selectable)

        self.assertIn("not complete and trusted", str(caught.exception))
        self.assertFalse(devices.select_device(result, 0).selected)

    def test_skipped_conflicting_record_blocks_selection_through_readiness(self):
        result = devices.parse_fixture_inventory(
            supported_inventory_text(
                source_name="synthetic-hidden-conflict",
                extra_record={
                    "source_record_id": "hidden-conflict",
                    "display_name": "Hidden Conflict",
                    "addresses": ["192.0.2.11"],
                    "stable_identifier": "02:00:5e:00:00:10",
                    "stable_identifier_type": "mac",
                    "unsupported_marker": "skipped",
                },
            )
        )
        selectable = next(index for index, item in enumerate(result.devices, start=1) if item.selectable)

        self.assertEqual(result.adapter_state, devices.STATE_MALFORMED_OUTPUT)
        with self.assertRaises(devices.DeviceSelectionError):
            devices.select_device(result, selectable)


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

    def test_public_evidence_omits_source_names_and_raw_errors(self):
        marker = "UNIQUE-SOURCE-MARKER"
        result = devices.parse_fixture_inventory(
            json.dumps(
                {
                    "schema": devices.FIXTURE_SCHEMA,
                    "sources": [
                        {
                            "name": marker,
                            "kind": "dhcp_leases",
                            "state": devices.STATE_PERMISSION_DENIED,
                            "confidence": "fixture",
                            "records": [],
                        }
                    ],
                }
            )
        )
        rendered = devices.render_json(result, public_evidence=True, redaction_salt="salt")
        payload = json.loads(rendered)

        self.assertNotIn(marker, rendered)
        self.assertNotIn("source ", rendered)
        self.assertEqual(payload["sources"][0]["kind"], "dhcp_leases")
        self.assertEqual(payload["errors"], [{"code": devices.STATE_PERMISSION_DENIED, "count": 1}])


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

    def test_source_count_bounds_are_enforced(self):
        source = {
            "name": "s",
            "kind": "dhcp_leases",
            "state": devices.STATE_SUPPORTED,
            "records": [],
        }
        exact = {"schema": devices.FIXTURE_SCHEMA, "sources": []}
        for index in range(devices.MAX_SOURCE_COUNT):
            item = dict(source)
            item["name"] = "s-%d" % index
            exact["sources"].append(item)

        devices.parse_fixture_inventory(json.dumps(exact))

        too_many = json.loads(json.dumps(exact))
        too_many["sources"].append(dict(source, name="too-many"))
        with self.assertRaises(devices.DeviceDiscoveryError):
            devices.parse_fixture_inventory(json.dumps(too_many))

    def test_record_and_address_bounds_are_enforced(self):
        record = {
            "source_record_id": "r",
            "addresses": ["192.0.2.1"],
        }
        exact_records = [dict(record, source_record_id="r-%d" % index) for index in range(devices.MAX_RECORDS_PER_SOURCE)]
        devices.parse_fixture_inventory(
            json.dumps(
                {
                    "schema": devices.FIXTURE_SCHEMA,
                    "sources": [
                        {
                            "name": "records",
                            "kind": "dhcp_leases",
                            "state": devices.STATE_SUPPORTED,
                            "records": exact_records,
                        }
                    ],
                }
            )
        )

        too_many_records = list(exact_records) + [dict(record, source_record_id="overflow")]
        with self.assertRaises(devices.DeviceDiscoveryError):
            devices.parse_fixture_inventory(
                json.dumps(
                    {
                        "schema": devices.FIXTURE_SCHEMA,
                        "sources": [
                            {
                                "name": "records",
                                "kind": "dhcp_leases",
                                "state": devices.STATE_SUPPORTED,
                                "records": too_many_records,
                            }
                        ],
                    }
                )
            )

        exact_addresses = ["192.0.2.%d" % (index + 1) for index in range(devices.MAX_ADDRESSES_PER_RECORD)]
        devices.parse_fixture_inventory(supported_inventory_text(extra_record=dict(record, addresses=exact_addresses)))
        with self.assertRaises(devices.DeviceDiscoveryError):
            devices.parse_fixture_inventory(
                supported_inventory_text(extra_record=dict(record, addresses=exact_addresses + ["198.51.100.1"]))
            )

    def test_text_bounds_and_enumerated_source_values_are_enforced(self):
        devices.parse_fixture_inventory(supported_inventory_text(source_name="s" * devices.MAX_SOURCE_NAME_LENGTH))
        with self.assertRaises(devices.DeviceDiscoveryError):
            devices.parse_fixture_inventory(supported_inventory_text(source_name="s" * (devices.MAX_SOURCE_NAME_LENGTH + 1)))

        invalid_state = json.loads(supported_inventory_text())
        invalid_state["sources"][0]["state"] = "surprising_state"
        with self.assertRaises(devices.DeviceDiscoveryError):
            devices.parse_fixture_inventory(json.dumps(invalid_state))

        invalid_confidence = json.loads(supported_inventory_text())
        invalid_confidence["sources"][0]["confidence"] = "surprising_confidence"
        with self.assertRaises(devices.DeviceDiscoveryError):
            devices.parse_fixture_inventory(json.dumps(invalid_confidence))


if __name__ == "__main__":
    unittest.main()
