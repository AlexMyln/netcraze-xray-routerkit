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

STRONG_ID = "netcraze-nc-1812"
PRIMARY_ID = "netcraze-nc-2312"
NETCRAZE_MATRIX_ID = "src-netcraze-package-matrix-2026-08-23"
KEENETIC_MATRIX_ID = "src-keenetic-package-matrix-2026-08-23"
PRIMARY_PRODUCT_ID = "src-netcraze-nc-2312-product-2026-08-23"
STRONG_PRODUCT_ID = "src-netcraze-nc-1812-product-2026-08-23"
SIBLING_PRODUCT_ID = "src-netcraze-nc-1012-product-2026-08-23"
MAIN_SOURCE_ID = "src-netcraze-nc-2312-main-2026-08-23"
PREVIEW_SOURCE_ID = "src-netcraze-nc-2312-preview-2026-08-23"


def model(data, model_id):
    return next(item for item in data["models"] if item["id"] == model_id)


def source(data, source_id):
    return next(item for item in data["sources"] if item["id"] == source_id)


def remove_source(data, source_id):
    data["sources"] = [item for item in data["sources"] if item["id"] != source_id]


def remove_architecture_binding(data):
    del model(data, STRONG_ID)["evidence"]["architecture"]


def bind_architecture_to_product_page(data):
    model(data, STRONG_ID)["evidence"]["architecture"] = [STRONG_PRODUCT_ID]


def bind_architecture_to_other_vendor_matrix(data):
    model(data, STRONG_ID)["evidence"]["architecture"] = [KEENETIC_MATRIX_ID]


def remove_architecture_fact(data):
    source(data, NETCRAZE_MATRIX_ID)["facts"].remove("architecture")


def remove_architecture_source(data):
    remove_source(data, NETCRAZE_MATRIX_ID)


def remove_model_from_architecture_scope(data):
    source(data, NETCRAZE_MATRIX_ID)["model_ids"].remove(STRONG_ID)


def strong_ram_128(data):
    target = model(data, STRONG_ID)
    target["ram_mb"] = 128
    target["ram_assessment"] = "insufficient"


def strong_storage_prerequisite_fail(data):
    model(data, STRONG_ID)["storage_prerequisite"] = "fail"


def strong_management_prerequisite_fail(data):
    model(data, STRONG_ID)["management_prerequisite"] = "fail"


def strong_cli_disabled(data):
    model(data, STRONG_ID)["cli"] = False


def strong_lifecycle_unsupported(data):
    model(data, STRONG_ID)["lifecycle_status"] = "unsupported"


def strong_unresolved_blocker(data):
    model(data, STRONG_ID)["blocking_reasons"] = ["synthetic unresolved static blocker"]


def modem_only_claims_storage(data):
    model(data, "netcraze-nc-2113")["usb_storage"] = True


def no_storage_claims_pass(data):
    model(data, "netcraze-nc-3911")["storage_prerequisite"] = "pass"


def main_uses_preview_source(data):
    data["firmware_policy"]["nc_2312_vendor_main"]["source_id"] = PREVIEW_SOURCE_ID


def preview_uses_main_source(data):
    data["firmware_policy"]["nc_2312_vendor_preview"]["source_id"] = MAIN_SOURCE_ID


def wrong_main_date(data):
    data["firmware_policy"]["nc_2312_vendor_main"]["released_at"] = "2026-08-11"


def wrong_main_version(data):
    data["firmware_policy"]["nc_2312_vendor_main"]["display_version"] = "5.1.4"


def wrong_preview_date(data):
    data["firmware_policy"]["nc_2312_vendor_preview"]["released_at"] = "2026-08-19"


def wrong_preview_version(data):
    data["firmware_policy"]["nc_2312_vendor_preview"]["display_version"] = "5.1.3"


def wrong_firmware_source_model(data):
    source(data, MAIN_SOURCE_ID)["firmware"]["model_id"] = STRONG_ID


def wrong_firmware_source_channel(data):
    source(data, MAIN_SOURCE_ID)["firmware"]["channel"] = "preview"


def remove_referenced_product_source(data):
    remove_source(data, PRIMARY_PRODUCT_ID)


def add_orphan_authoritative_source(data):
    data["sources"].append(
        {
            "id": "src-netcraze-orphan-test-2026-08-23",
            "vendor": "Netcraze",
            "title": "Synthetic unbound authoritative source",
            "url": "https://netcraze.ru/ru/orphan-authoritative-test",
            "retrieval_date": "2026-08-23",
            "kind": "product_page",
            "model_ids": [PRIMARY_ID],
            "facts": ["identity"],
            "catalog_boundary_only": False,
            "firmware": None,
        }
    )


def duplicate_source_url(data):
    duplicate = copy.deepcopy(data["sources"][0])
    duplicate["id"] = "src-netcraze-duplicate-catalog-2026-08-23"
    data["sources"].append(duplicate)


def mismatch_bound_source_fact(data):
    source(data, STRONG_PRODUCT_ID)["facts"].remove("hardware")


def source_scopes_nonexistent_model(data):
    source(data, STRONG_PRODUCT_ID)["model_ids"].append("netcraze-does-not-exist")


def bind_management_to_sibling(data):
    model(data, STRONG_ID)["evidence"]["management"] = [SIBLING_PRODUCT_ID]


def unknown_architecture_promoted(data):
    target = model(data, STRONG_ID)
    target["architecture"] = None
    target["bitness"] = None
    target["xray_artifact_match"] = False


NEGATIVE_MUTATIONS = [
    ("01_architecture_binding_removed", remove_architecture_binding),
    ("02_architecture_bound_to_product_page", bind_architecture_to_product_page),
    ("03_architecture_bound_to_other_model_scope", bind_architecture_to_other_vendor_matrix),
    ("04_architecture_fact_removed_from_source", remove_architecture_fact),
    ("05_architecture_source_removed", remove_architecture_source),
    ("06_architecture_exact_model_scope_removed", remove_model_from_architecture_scope),
    ("07_strong_ram_128", strong_ram_128),
    ("08_strong_storage_prerequisite_fail", strong_storage_prerequisite_fail),
    ("09_strong_management_prerequisite_fail", strong_management_prerequisite_fail),
    ("10_strong_cli_disabled", strong_cli_disabled),
    ("11_strong_lifecycle_unsupported", strong_lifecycle_unsupported),
    ("12_strong_unresolved_blocker", strong_unresolved_blocker),
    ("13_modem_only_claims_general_storage", modem_only_claims_storage),
    ("14_no_storage_claims_storage_pass", no_storage_claims_pass),
    ("15_main_bound_to_preview_source", main_uses_preview_source),
    ("16_preview_bound_to_main_source", preview_uses_main_source),
    ("17_main_release_date_changed", wrong_main_date),
    ("18_main_version_changed", wrong_main_version),
    ("19_preview_release_date_changed", wrong_preview_date),
    ("20_preview_version_changed", wrong_preview_version),
    ("21_firmware_source_model_changed", wrong_firmware_source_model),
    ("22_firmware_source_channel_changed", wrong_firmware_source_channel),
    ("23_referenced_source_removed", remove_referenced_product_source),
    ("24_orphan_authoritative_source_added", add_orphan_authoritative_source),
    ("25_duplicate_source_url_added", duplicate_source_url),
    ("26_bound_source_fact_mismatch", mismatch_bound_source_fact),
    ("27_source_scopes_nonexistent_model", source_scopes_nonexistent_model),
    ("28_model_binds_source_for_sibling", bind_management_to_sibling),
    ("30_unknown_architecture_promoted", unknown_architecture_promoted),
]


class PlatformCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = ROOT / "hardware" / "routerkit-platform-compatibility.v1.json"
        cls.data = json.loads(cls.path.read_text(encoding="utf-8"))

    def test_manifest_validates_with_historical_packet_identity(self):
        MODULE.validate_manifest(self.data, repository_root=ROOT)

    def test_catalog_coverage_is_exact(self):
        counts = {
            key: sum(item["universe_pass"] == key for item in self.data["models"])
            for key in ("current_netcraze", "current_keenetic", "recent_legacy")
        }
        self.assertEqual(counts, {"current_netcraze": 26, "current_keenetic": 13, "recent_legacy": 8})

    def test_all_models_are_unvalidated_and_source_bound(self):
        for item in self.data["models"]:
            self.assertEqual(item["hardware_validation_state"], "not_hardware_tested")
            self.assertEqual(item["retrieval_date"], "2026-08-23")
            self.assertTrue(item["evidence"])

    def test_source_registry_is_unique_and_machine_readable(self):
        sources = self.data["sources"]
        self.assertEqual(len(sources), len({item["id"] for item in sources}))
        self.assertEqual(len(sources), len({item["url"] for item in sources}))
        self.assertTrue(all(item["facts"] and item["model_ids"] for item in sources if not item["catalog_boundary_only"]))

    def test_strong_models_have_no_static_eligibility_failures(self):
        for item in self.data["models"]:
            if item["classification"] in MODULE.STRONG:
                self.assertEqual(MODULE.static_eligibility_failures(item, item["evidence"]), [])
                self.assertEqual(item["blocking_reasons"], [])

    def test_unknown_architecture_never_passes(self):
        for item in self.data["models"]:
            if item["architecture"] is None:
                self.assertNotIn(item["classification"], MODULE.STRONG)
                self.assertFalse(item["xray_artifact_match"])

    def test_unknown_contract_fields_fail_closed(self):
        changed = copy.deepcopy(self.data)
        changed["unexpected"] = True
        with self.assertRaises(MODULE.CompatibilityValidationError):
            MODULE.validate_manifest(changed)

        changed = copy.deepcopy(self.data)
        changed["models"][0]["unexpected"] = True
        with self.assertRaises(MODULE.CompatibilityValidationError):
            MODULE.validate_manifest(changed)

    def test_xray_architecture_map_remains_strict(self):
        xray = self.data["routerkit_requirements"]["xray_bootstrap"]
        self.assertEqual(xray["architecture_token"], "linux-arm64")
        self.assertEqual(set(xray["uname_machines"]), {"aarch64", "arm64"})
        self.assertFalse(xray["other_architectures_supported"])

    def test_29_usb2_positive_control_is_eligible(self):
        changed = copy.deepcopy(self.data)
        target = model(changed, STRONG_ID)
        target["usb_ports"] = ["USB 2.0"]
        MODULE.validate_manifest(changed)
        self.assertEqual(MODULE.static_eligibility_failures(target, target["evidence"]), [])

    def test_no_usb_or_modem_only_fails_storage(self):
        for item in self.data["models"]:
            if item["usb_usage"] != "general_storage":
                self.assertFalse(item["usb_storage"])
                self.assertEqual(item["storage_prerequisite"], "fail")

    def test_main_preview_tuples_are_distinct_and_exact(self):
        policy = self.data["firmware_policy"]
        main = policy["nc_2312_vendor_main"]
        preview = policy["nc_2312_vendor_preview"]
        self.assertEqual((main["display_version"], main["released_at"], main["channel"]), ("5.1.3", "2026-08-10", "main"))
        self.assertEqual((preview["display_version"], preview["released_at"], preview["channel"]), ("5.1.4", "2026-08-18", "preview"))
        self.assertNotEqual(main["source_id"], preview["source_id"])
        self.assertIsNone(main["exact_build_string"])
        self.assertIsNone(preview["exact_build_string"])

    def test_packet_v1_is_preserved_and_packet_v2_is_absent(self):
        packet = self.data["packet_decision"]
        self.assertEqual(packet["sha256"], MODULE.PACKET_V1_SHA256)
        self.assertFalse(packet["packet_v2_created"])
        self.assertFalse((ROOT / "hardware" / "netcraze-canary-packet.v2.json").exists())

    def test_aliases_fail_closed_without_board_evidence(self):
        self.assertTrue(self.data["alias_assessments"])
        self.assertTrue(all(item["classification"] == "insufficient_evidence" for item in self.data["alias_assessments"]))

    def test_legacy_cutoff_is_dated_and_mechanical(self):
        policy = self.data["catalog_coverage"]["legacy_cutoff"]
        self.assertEqual(policy["policy_version"], "routerkit-recent-legacy-v1")
        self.assertEqual(policy["review_date"], "2026-08-23")
        self.assertEqual(policy["evidence_retrieval_cutoff_date"], "2023-08-23")
        self.assertEqual(policy["lookback_years"], 3)

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


def mutation_test(mutator):
    def test(self):
        changed = copy.deepcopy(self.data)
        mutator(changed)
        with self.assertRaises(MODULE.CompatibilityValidationError):
            MODULE.validate_manifest(changed)

    return test


for mutation_name, mutation in NEGATIVE_MUTATIONS:
    setattr(PlatformCompatibilityTests, f"test_mutation_{mutation_name}", mutation_test(mutation))


if __name__ == "__main__":
    unittest.main()
