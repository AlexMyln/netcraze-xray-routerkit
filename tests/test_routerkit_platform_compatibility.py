import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_routerkit_platform_compatibility.py"
SPEC = importlib.util.spec_from_file_location("routerkit_platform_compatibility", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class PlatformCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = ROOT / "hardware" / "routerkit-platform-compatibility.v1.json"
        cls.data = json.loads(cls.path.read_text(encoding="utf-8"))

    def test_manifest_validates_with_historical_packet_identity(self):
        MODULE.validate_manifest(self.data, repository_root=ROOT)

    def test_catalog_coverage_is_exact(self):
        counts = {
            key: sum(model["universe_pass"] == key for model in self.data["models"])
            for key in ("current_netcraze", "current_keenetic", "recent_legacy")
        }
        self.assertEqual(counts, {"current_netcraze": 26, "current_keenetic": 13, "recent_legacy": 8})

    def test_all_models_are_unvalidated_and_sourced(self):
        for model in self.data["models"]:
            self.assertEqual(model["hardware_validation_state"], "not_hardware_tested")
            self.assertEqual(model["retrieval_date"], "2026-08-23")
            self.assertTrue(model["official_sources"])
            self.assertTrue(all(url.startswith("https://") for url in model["official_sources"]))

    def test_unknown_architecture_never_passes(self):
        strong = {"primary_canary_target", "strong_candidate", "hardware_equivalent_candidate"}
        for model in self.data["models"]:
            if model["architecture"] is None:
                self.assertNotIn(model["classification"], strong)
                self.assertFalse(model["xray_artifact_match"])

    def test_unknown_contract_fields_fail_closed(self):
        changed = copy.deepcopy(self.data)
        changed["unexpected"] = True
        with self.assertRaises(MODULE.CompatibilityValidationError):
            MODULE.validate_manifest(changed)

        changed = copy.deepcopy(self.data)
        changed["models"][0]["unexpected"] = True
        with self.assertRaises(MODULE.CompatibilityValidationError):
            MODULE.validate_manifest(changed)

    def test_known_per_model_firmware_requires_matching_channel_source(self):
        changed = copy.deepcopy(self.data)
        target = next(model for model in changed["models"] if model["id"] == "netcraze-nc-1812")
        target["official_sources"] = [url for url in target["official_sources"] if "latest-main-release" not in url]
        with self.assertRaises(MODULE.CompatibilityValidationError):
            MODULE.validate_manifest(changed)

    def test_xray_architecture_map_remains_strict(self):
        xray = self.data["routerkit_requirements"]["xray_bootstrap"]
        self.assertEqual(xray["architecture_token"], "linux-arm64")
        self.assertEqual(set(xray["uname_machines"]), {"aarch64", "arm64"})
        self.assertFalse(xray["other_architectures_supported"])

    def test_usb_2_is_not_rejected_by_bus_version(self):
        viva = next(model for model in self.data["models"] if model["id"] == "netcraze-nc-1913")
        self.assertEqual(viva["usb_ports"], ["USB 2.0"])
        self.assertTrue(viva["usb_storage"])
        self.assertEqual(viva["storage_prerequisite"], "pass")
        self.assertIn("aarch64", " ".join(viva["blocking_reasons"]))

    def test_no_usb_or_modem_only_fails_storage(self):
        for model in self.data["models"]:
            if not model["usb_storage"]:
                self.assertEqual(model["classification"], "unsupported_current_routerkit_path")
                self.assertEqual(model["storage_prerequisite"], "fail")

    def test_main_preview_and_5_1_3_status_are_separate(self):
        policy = self.data["firmware_policy"]
        self.assertEqual(policy["nc_2312_vendor_main"]["display_version"], "5.1.3")
        self.assertEqual(policy["nc_2312_vendor_main"]["state"], "vendor_main")
        self.assertEqual(policy["nc_2312_vendor_preview"]["display_version"], "5.1.4")
        self.assertEqual(policy["nc_2312_vendor_preview"]["state"], "vendor_preview")
        self.assertEqual(policy["historical_requested_5_1_3"]["state"], "historical_requested_point")
        self.assertIsNone(policy["nc_2312_vendor_main"]["exact_build_string"])

    def test_packet_v1_is_preserved_and_packet_v2_is_absent(self):
        packet = self.data["packet_decision"]
        self.assertEqual(packet["sha256"], MODULE.PACKET_V1_SHA256)
        self.assertFalse(packet["packet_v2_created"])
        self.assertFalse((ROOT / "hardware" / "netcraze-canary-packet.v2.json").exists())

    def test_aliases_fail_closed_without_board_evidence(self):
        self.assertTrue(self.data["alias_assessments"])
        self.assertTrue(all(item["classification"] == "insufficient_evidence" for item in self.data["alias_assessments"]))

    def test_required_document_links_exist(self):
        expected = {
            "README.md": "docs/hardware/platform-compatibility.md",
            "README.ru.md": "docs/hardware/platform-compatibility.ru.md",
            "docs/installer-scope.md": "platform-compatibility.md",
            "docs/installer-scope.ru.md": "platform-compatibility.ru.md",
            "docs/hardware/netcraze-hardware-canary.md": "platform-compatibility.md",
            "docs/hardware/netcraze-hardware-canary.ru.md": "platform-compatibility.ru.md",
        }
        for relative, target in expected.items():
            self.assertIn(target, (ROOT / relative).read_text(encoding="utf-8"))

    def test_no_secret_or_live_execution_fields(self):
        text = self.path.read_text(encoding="utf-8").lower()
        for forbidden in ("password", "private_key", "authorization:", "router_address"):
            self.assertNotIn(forbidden, text)
        self.assertFalse(self.data["review_scope"]["hardware_validated"])
        self.assertFalse(self.data["review_scope"]["live_contract_confirmed"])


if __name__ == "__main__":
    unittest.main()
