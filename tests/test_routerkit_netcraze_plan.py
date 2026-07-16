import json
import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures" / "netcraze"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import routerkit_netcraze_plan as plan
from tests.test_routerkit_device_adapters import find_live_execution_guard_violations


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def one_profile_manifest():
    return plan.parse_local_endpoint_manifest(
        json.dumps(
            {
                "schema": plan.MANIFEST_SCHEMA,
                "profiles": [
                    {
                        "slot": 1,
                        "label": "primary",
                        "listen": "127.0.0.1",
                        "port": 1082,
                        "enabled": True,
                        "protocol": "socks5",
                    }
                ],
            }
        )
    )


class ManifestContractTests(unittest.TestCase):
    def test_valid_manifest_accepts_ipv4_ipv6_and_expected_ports(self):
        manifest = plan.parse_local_endpoint_manifest(fixture("local-endpoints.json"))

        self.assertEqual([item.slot for item in manifest.profiles], [1, 2, 3])
        self.assertEqual([item.port for item in manifest.profiles], [1082, 1083, 1084])
        self.assertEqual(manifest.profiles[1].host, "::1")

    def test_rejects_non_loopback_duplicates_protocol_extra_and_secret_fields(self):
        base = json.loads(fixture("local-endpoints.json"))
        mutations = []
        non_loopback = json.loads(json.dumps(base))
        non_loopback["profiles"][0]["listen"] = "192.0.2.1"
        mutations.append(non_loopback)
        duplicate = json.loads(json.dumps(base))
        duplicate["profiles"][1]["port"] = 1082
        mutations.append(duplicate)
        protocol = json.loads(json.dumps(base))
        protocol["profiles"][0]["protocol"] = "http"
        mutations.append(protocol)
        extra = json.loads(json.dumps(base))
        extra["profiles"][0]["remote_server"] = "secret.example"
        mutations.append(extra)
        raw_label = json.loads(json.dumps(base))
        raw_label["profiles"][0]["label"] = "provider-derived"
        mutations.append(raw_label)
        for value in mutations:
            with self.subTest(value=value), self.assertRaises(plan.NetcrazePlanError):
                plan.parse_local_endpoint_manifest(json.dumps(value))

    def test_owner_only_reader_rejects_public_symlink_hardlink_and_oversize(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "manifest.json"
            source.write_text(fixture("local-endpoints.json"), encoding="utf-8")
            os.chmod(source, 0o600)
            self.assertEqual(len(plan.load_local_endpoint_manifest(source).profiles), 3)
            if os.name == "posix":
                os.chmod(source, 0o644)
                with self.assertRaises(plan.ManifestSchemaError):
                    plan.load_local_endpoint_manifest(source)
                os.chmod(source, 0o600)
            symlink = root / "manifest-link.json"
            symlink.symlink_to(source)
            with self.assertRaises(plan.ManifestSchemaError):
                plan.load_local_endpoint_manifest(symlink)
            hardlink = root / "manifest-hard.json"
            os.link(source, hardlink)
            with self.assertRaises(plan.ManifestSchemaError):
                plan.load_local_endpoint_manifest(source)


class SnapshotContractTests(unittest.TestCase):
    def test_required_valid_fixtures_parse(self):
        names = (
            "empty-clean-state.json",
            "exact-equivalent-state.json",
            "connection-name-conflict.json",
            "policy-name-conflict.json",
            "foreign-objects.json",
            "multiple-profile-slots.json",
            "missing-fallback.json",
            "equivalent-assignment.json",
            "assignment-other-policy.json",
            "assignment-default-policy.json",
            "no-selected-device.json",
            "unknown-default-policy.json",
            "ambiguous-default-policy.json",
            "degraded-snapshot.json",
            "stale-snapshot.json",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(plan.parse_router_state_snapshot(fixture(name)).schema, plan.SNAPSHOT_SCHEMA)

    def test_duplicate_unsupported_malformed_and_fixture_trust_are_rejected(self):
        for name in (
            "duplicate-object-ids.json",
            "duplicate-object-names.json",
            "unsupported-fields.json",
            "malformed-objects.json",
        ):
            with self.subTest(name=name), self.assertRaises(plan.SnapshotSchemaError):
                plan.parse_router_state_snapshot(fixture(name))
        value = json.loads(fixture("empty-clean-state.json"))
        value["connections"] = []
        value["capabilities"]["backup_success"] = True
        with self.assertRaises(plan.SnapshotSchemaError):
            plan.parse_router_state_snapshot(json.dumps(value))

    def test_snapshot_byte_bound_is_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "state.json"
            source.write_bytes(b" " * (plan.MAX_SNAPSHOT_BYTES + 1))
            os.chmod(source, 0o600)
            with self.assertRaises(plan.SnapshotSchemaError):
                plan.load_router_state_snapshot(source)

    def test_cross_object_inconsistencies_are_rejected(self):
        base = json.loads(fixture("empty-clean-state.json"))
        cases = {}

        orphan_policy = json.loads(json.dumps(base))
        orphan_policy["policies"][0]["connection_ref"] = "missing-connection"
        cases["policy_missing_connection"] = orphan_policy

        orphan_assignment = json.loads(json.dumps(base))
        orphan_assignment["assignments"] = [
            {"device_mac": "02:00:5e:00:00:10", "policy_ref": "missing-policy"}
        ]
        cases["assignment_missing_policy"] = orphan_assignment

        missing_default = json.loads(json.dumps(base))
        missing_default["default_policy"]["observed_ref"] = "missing-policy"
        cases["missing_default_policy"] = missing_default

        flag_mismatch = json.loads(json.dumps(base))
        flag_mismatch["policies"][0]["is_default"] = False
        cases["default_flag_mismatch"] = flag_mismatch

        multiple_defaults = json.loads(json.dumps(base))
        multiple_defaults["policies"].append(
            {
                "id": "policy-second-default",
                "name": "Synthetic-Second-Default",
                "connection_ref": "synthetic-uplink",
                "mode": "default",
                "is_default": True,
                "semantic_complete": True,
                "unrelated_rules": True,
            }
        )
        cases["multiple_known_defaults"] = multiple_defaults

        two_policies = json.loads(json.dumps(base))
        two_policies["policies"].append(
            {
                "id": "policy-other",
                "name": "Synthetic-Other",
                "connection_ref": "synthetic-uplink",
                "mode": "proxy_only",
                "is_default": False,
                "semantic_complete": True,
                "unrelated_rules": False,
            }
        )
        two_policies["assignments"] = [
            {"device_mac": "02:00:5e:00:00:10", "policy_ref": "policy-default"},
            {"device_mac": "02:00:5e:00:00:10", "policy_ref": "policy-other"},
        ]
        cases["device_multiple_policies"] = two_policies

        duplicate_pair = json.loads(json.dumps(base))
        duplicate_pair["assignments"] = [
            {"device_mac": "02:00:5e:00:00:10", "policy_ref": "policy-default"},
            {"device_mac": "02:00:5e:00:00:10", "policy_ref": "policy-default"},
        ]
        cases["duplicate_assignment"] = duplicate_pair

        incomplete_default = json.loads(json.dumps(base))
        incomplete_default["policies"][0]["semantic_complete"] = False
        cases["incomplete_default"] = incomplete_default

        impossible_capability = json.loads(json.dumps(base))
        impossible_capability["capabilities"]["connection_inventory"] = "unknown"
        cases["impossible_capability"] = impossible_capability

        fixture_write_contract = json.loads(json.dumps(base))
        fixture_write_contract["capabilities"]["write_contract"] = "documented"
        cases["fixture_claims_write_contract"] = fixture_write_contract

        unknown_documented_default = json.loads(fixture("unknown-default-policy.json"))
        unknown_documented_default["capabilities"]["default_policy_identity"] = "documented"
        cases["unknown_default_documented"] = unknown_documented_default

        for name, value in cases.items():
            with self.subTest(name=name), self.assertRaises(plan.SnapshotSchemaError):
                plan.parse_router_state_snapshot(json.dumps(value))

    def test_unknown_and_ambiguous_defaults_remain_explicit_diagnostics(self):
        unknown = plan.parse_router_state_snapshot(fixture("unknown-default-policy.json"))
        ambiguous = plan.parse_router_state_snapshot(
            fixture("ambiguous-default-policy.json")
        )
        self.assertFalse(plan.validate_snapshot_consistency(unknown).default_identity_proven)
        self.assertFalse(plan.validate_snapshot_consistency(ambiguous).default_identity_proven)
        self.assertEqual(plan.default_policy_projection(unknown).status, "unknown")
        self.assertEqual(plan.default_policy_projection(ambiguous).status, "ambiguous")


class PlanningTests(unittest.TestCase):
    def test_clean_create_is_ordered_deterministic_and_plan_only(self):
        manifest = one_profile_manifest()
        snapshot = plan.parse_router_state_snapshot(fixture("empty-clean-state.json"))
        first = plan.build_change_plan(manifest, snapshot)
        second = plan.build_change_plan(manifest, snapshot)

        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.plan_status, plan.READINESS_HARDWARE)
        self.assertEqual(first.write_readiness, plan.READINESS_BLOCKED)
        self.assertEqual(
            [item.operation for item in first.actions[:2]],
            ["create_connection", "create_policy"],
        )
        self.assertEqual(first.actions[-1].object_type, "default_policy")
        self.assertTrue(first.default_policy_not_targeted)
        self.assertTrue(all(item.target_name != "Synthetic-Default" for item in first.actions))
        policy_action = next(
            item for item in first.actions if item.operation == "create_policy"
        )
        self.assertEqual(policy_action.dependencies[0].kind, "planned_connection")
        self.assertEqual(policy_action.dependencies[0].profile_slot, 1)

    def test_exact_equivalence_reuses_without_name_only_logic(self):
        result = plan.build_change_plan(
            one_profile_manifest(),
            plan.parse_router_state_snapshot(fixture("exact-equivalent-state.json")),
        )
        self.assertEqual([item.operation for item in result.actions[:2]], ["reuse_connection", "reuse_policy"])

    def test_same_name_conflicts_block_every_action(self):
        for name in ("connection-name-conflict.json", "policy-name-conflict.json"):
            result = plan.build_change_plan(
                one_profile_manifest(), plan.parse_router_state_snapshot(fixture(name))
            )
            with self.subTest(name=name):
                self.assertTrue(result.blocked)
                self.assertIn("conflict", [item.operation for item in result.actions])
                self.assertTrue(all(item.readiness == plan.READINESS_BLOCKED for item in result.actions))

    def test_imported_callers_cannot_upgrade_conflicts(self):
        snapshot = plan.parse_router_state_snapshot(fixture("connection-name-conflict.json"))
        blocked = plan.build_change_plan(one_profile_manifest(), snapshot)

        self.assertTrue(blocked.blocked)
        self.assertEqual(blocked.actions[0].operation, "conflict")
        self.assertFalse(hasattr(plan, "AdapterOwnershipProof"))
        self.assertNotIn("ownership_proofs", inspect.signature(plan.build_change_plan).parameters)
        with self.assertRaises(TypeError):
            plan.build_change_plan(
                one_profile_manifest(), snapshot, ownership_proofs=(object(),)
            )

    def test_unknown_degraded_and_stale_snapshots_are_diagnostic_only(self):
        for name in ("unknown-default-policy.json", "ambiguous-default-policy.json", "degraded-snapshot.json", "stale-snapshot.json"):
            result = plan.build_change_plan(
                one_profile_manifest(), plan.parse_router_state_snapshot(fixture(name))
            )
            with self.subTest(name=name):
                self.assertTrue(result.blocked)
                self.assertTrue(result.actions)

    def test_optional_assignment_and_existing_move_boundary(self):
        selected = plan.SelectedDeviceRef("Synthetic Tablet", "02:00:5e:00:00:10")
        clean = plan.build_change_plan(
            one_profile_manifest(),
            plan.parse_router_state_snapshot(fixture("empty-clean-state.json")),
            selected,
        )
        blocked = plan.build_change_plan(
            one_profile_manifest(),
            plan.parse_router_state_snapshot(fixture("assignment-other-policy.json")),
            selected,
        )
        no_selection = plan.build_change_plan(
            one_profile_manifest(), plan.parse_router_state_snapshot(fixture("empty-clean-state.json"))
        )

        self.assertIn("assign_device", [item.operation for item in clean.actions])
        self.assertIn("blocked", [item.operation for item in blocked.actions])
        self.assertNotIn("assign_device", [item.operation for item in no_selection.actions])

        default_blocked = plan.build_change_plan(
            one_profile_manifest(),
            plan.parse_router_state_snapshot(fixture("assignment-default-policy.json")),
            selected,
        )
        assignment = next(
            item for item in default_blocked.actions if item.object_type == "assignment"
        )
        self.assertTrue(default_blocked.blocked)
        self.assertIn("default policy", assignment.reason)

    def test_default_policy_invariant_blocks_unknown_name_and_id_collisions(self):
        unknown = plan.build_change_plan(
            one_profile_manifest(),
            plan.parse_router_state_snapshot(fixture("unknown-default-policy.json")),
        )
        self.assertTrue(unknown.blocked)
        self.assertFalse(unknown.default_policy_not_targeted)

        name_collision = json.loads(fixture("empty-clean-state.json"))
        name_collision["policies"][0]["name"] = "RouterKit-Policy-1082"
        named = plan.build_change_plan(
            one_profile_manifest(),
            plan.parse_router_state_snapshot(json.dumps(name_collision)),
        )
        self.assertTrue(named.blocked)
        self.assertIn("conflict", [item.operation for item in named.actions])

        id_collision = json.loads(fixture("empty-clean-state.json"))
        id_collision["connections"].append(
            {
                "id": "simulation:connection:slot-1",
                "name": "Synthetic-Unrelated",
                "protocol": "http",
                "host": "127.0.0.1",
                "port": 8080,
                "auth_mode": "none",
                "enabled": True,
                "semantic_complete": True,
            }
        )
        collided = plan.build_change_plan(
            one_profile_manifest(),
            plan.parse_router_state_snapshot(json.dumps(id_collision)),
        )
        self.assertTrue(collided.blocked)
        self.assertIn("identity collides", collided.actions[0].reason)

    def test_public_evidence_removes_local_markers_and_warns_not_anonymous(self):
        selected = plan.SelectedDeviceRef("PRIVATE_DEVICE", "02:00:5e:00:00:10")
        result = plan.build_change_plan(
            one_profile_manifest(),
            plan.parse_router_state_snapshot(fixture("empty-clean-state.json")),
            selected,
        )
        public = plan.render_plan_json(result, public_evidence=True)
        local = plan.render_plan_json(result)

        for marker in ("PRIVATE_DEVICE", "02:00:5e", "Synthetic-Default", "synthetic-empty"):
            self.assertNotIn(marker, public)
        self.assertNotIn("planned_connection", public)
        self.assertIn("not an anonymity guarantee", public)
        self.assertIn("PRIVATE_DEVICE", local)
        self.assertIn(plan.SENSITIVITY_LOCAL, local)


class StaticGuardTests(unittest.TestCase):
    def test_planning_module_has_no_live_execution_primitive(self):
        source = (SCRIPTS / "routerkit_netcraze_plan.py").read_text(encoding="utf-8")
        self.assertEqual(find_live_execution_guard_violations(source), [])

    def test_guard_detects_malicious_mutations_and_accepts_safe_control(self):
        for source in (
            "import socket",
            "import subprocess",
            "endpoint = '/rci'",
            "eval('1')",
            "class AdapterOwnershipProof: pass",
            "def from_reviewed_adapter(): pass",
            "def update_owned_connection(): pass",
            "move_owned_assignment = object()",
        ):
            with self.subTest(source=source):
                self.assertTrue(find_live_execution_guard_violations(source))
        self.assertEqual(find_live_execution_guard_violations("import json\nvalue = json.loads('{}')"), [])


if __name__ == "__main__":
    unittest.main()
