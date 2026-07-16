import ast
import json
import inspect
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures" / "netcraze"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import routerkit_netcraze_plan as plan
import routerkit_devices as devices
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


def find_simulator_api_signature_violations(source):
    tree = ast.parse(source)
    definitions = [
        item
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == "simulate_change_plan"
    ]
    if len(definitions) != 1:
        return ["simulate_change_plan must have exactly one top-level definition"]
    if not isinstance(definitions[0], ast.FunctionDef):
        return ["simulate_change_plan must be a synchronous function"]

    arguments = definitions[0].args
    violations = []
    if arguments.posonlyargs:
        violations.append("positional-only parameters are forbidden")
    if [item.arg for item in arguments.args] != ["plan", "snapshot"]:
        violations.append("positional parameters must be exactly plan and snapshot")
    if arguments.vararg is not None:
        violations.append("variadic positional parameters are forbidden")
    if arguments.kwarg is not None:
        violations.append("variadic keyword parameters are forbidden")
    if [item.arg for item in arguments.kwonlyargs] != [
        "fail_after",
        "rollback_failure",
    ]:
        violations.append(
            "keyword-only parameters must be exactly fail_after and rollback_failure"
        )
    if len(arguments.kw_defaults) != 2:
        violations.append("exactly two keyword-only defaults are required")
    else:
        fail_after_default, rollback_failure_default = arguments.kw_defaults
        if not (
            isinstance(fail_after_default, ast.Constant)
            and fail_after_default.value is None
        ):
            violations.append("fail_after must default to None")
        if not (
            isinstance(rollback_failure_default, ast.Constant)
            and rollback_failure_default.value is False
        ):
            violations.append("rollback_failure must default to False")
    return violations


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

        self.assertEqual(
            first.local_integrity_fingerprint,
            second.local_integrity_fingerprint,
        )
        self.assertEqual(
            first.public_plan_fingerprint,
            second.public_plan_fingerprint,
        )
        self.assertEqual(first.desired_profiles, manifest.profiles)
        self.assertEqual(
            first.source_snapshot_fingerprint,
            plan.snapshot_semantic_fingerprint(snapshot),
        )
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

    def test_selected_device_boundary_rejects_invalid_direct_construction(self):
        manifest = one_profile_manifest()
        snapshot = plan.parse_router_state_snapshot(
            fixture("empty-clean-state.json")
        )
        invalid_macs = (
            "00:00:00:00:00:00",
            "ff:ff:ff:ff:ff:ff",
            "01:00:5e:00:00:01",
            "33:33:00:00:00:01",
            "02:00:5e:00:00",
            "02:00:5e:00:00:gg",
            "",
            123,
            None,
            "02:00:5e:00:00:10\n",
        )
        for mac in invalid_macs:
            selected = plan.SelectedDeviceRef("Synthetic Tablet", mac)
            with self.subTest(mac=repr(mac)):
                with mock.patch.object(
                    plan,
                    "_action",
                    side_effect=AssertionError("actions must not be built"),
                ):
                    with self.assertRaisesRegex(
                        plan.NetcrazePlanError,
                        "^Selected device reference is invalid\\.$",
                    ):
                        plan.build_change_plan(manifest, snapshot, selected)

        for display_name in ("", " \t ", "bad\nname", "x" * (plan.MAX_TEXT + 1), 7, None):
            selected = plan.SelectedDeviceRef(
                display_name, "02:00:5e:00:00:10"
            )
            with self.subTest(display_name=repr(display_name)):
                with self.assertRaisesRegex(
                    plan.NetcrazePlanError,
                    "^Selected device reference is invalid\\.$",
                ):
                    plan.build_change_plan(manifest, snapshot, selected)

    def test_selected_device_boundary_normalizes_valid_global_and_local_macs(self):
        snapshot = plan.parse_router_state_snapshot(
            fixture("empty-clean-state.json")
        )
        for supplied, expected in (
            ("00:11:22:33:44:55", "00:11:22:33:44:55"),
            ("02:00:5e:00:00:10", "02:00:5e:00:00:10"),
            ("02-00-5E-00-00-10", "02:00:5e:00:00:10"),
        ):
            with self.subTest(supplied=supplied):
                result = plan.build_change_plan(
                    one_profile_manifest(),
                    snapshot,
                    plan.SelectedDeviceRef(" Synthetic Tablet ", supplied),
                )
                self.assertEqual(result.selected_device.display_name, "Synthetic Tablet")
                self.assertEqual(result.selected_device.mac, expected)
                self.assertIn(
                    "assign_device", [item.operation for item in result.actions]
                )

    def test_device_discovery_and_planner_share_one_mac_trust_helper(self):
        self.assertIs(
            plan.normalize_trusted_device_mac,
            devices.normalize_trusted_device_mac,
        )
        self.assertEqual(
            devices.normalize_trusted_device_mac("02-00-5E-00-00-10"),
            plan.normalize_selected_device_ref(
                plan.SelectedDeviceRef(
                    "Synthetic Tablet", "02-00-5E-00-00-10"
                )
            ).mac,
        )

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
        self.assertIn("public_plan_fingerprint", public)
        self.assertNotIn("local_integrity_fingerprint", public)
        self.assertNotIn(result.local_integrity_fingerprint, public)
        self.assertNotIn(result.source_snapshot_fingerprint, public)
        self.assertIn("permits correlation", public)
        self.assertIn("PRIVATE_DEVICE", local)
        self.assertIn("local_integrity_fingerprint", local)
        self.assertIn("source_snapshot_fingerprint", local)
        self.assertIn(plan.SENSITIVITY_LOCAL, local)

    def test_local_fingerprint_identity_covers_all_plan_semantics(self):
        snapshot = plan.parse_router_state_snapshot(
            fixture("empty-clean-state.json")
        )
        base = plan.build_change_plan(
            one_profile_manifest(),
            snapshot,
            plan.SelectedDeviceRef("Synthetic Tablet", "02:00:5e:00:00:10"),
        )
        baseline = plan._sha256_json(plan._local_plan_identity(base))
        first_profile = base.desired_profiles[0]
        first_action = base.actions[0]
        first_policy = next(
            item for item in base.actions if item.action_id == "01:policy"
        )
        mutations = {
            "desired_host": replace(
                base,
                desired_profiles=(replace(first_profile, host="::1"),),
            ),
            "desired_port": replace(
                base,
                desired_profiles=(replace(first_profile, port=1083),),
            ),
            "desired_enabled": replace(
                base,
                desired_profiles=(replace(first_profile, enabled=False),),
            ),
            "desired_protocol": replace(
                base,
                desired_profiles=(replace(first_profile, protocol="http"),),
            ),
            "desired_auth": replace(
                base,
                desired_profiles=(replace(first_profile, auth_mode="password"),),
            ),
            "action_operation": replace(
                base,
                actions=(replace(first_action, operation="reuse_connection"),)
                + base.actions[1:],
            ),
            "dependency": replace(
                base,
                actions=tuple(
                    replace(
                        item,
                        dependencies=(
                            plan.ObjectReference(
                                "existing_connection", value="different"
                            ),
                        ),
                    )
                    if item.action_id == first_policy.action_id
                    else item
                    for item in base.actions
                ),
            ),
            "selected_mac": replace(
                base,
                selected_device=replace(
                    base.selected_device, mac="02:00:5e:00:00:11"
                ),
            ),
            "source_connection": replace(
                base, source_snapshot_fingerprint="1" * 64
            ),
            "rollback": replace(
                base,
                rollback=plan.RollbackPlan(
                    (
                        replace(
                            base.rollback.actions[0],
                            operation="different_rollback",
                        ),
                    )
                    + base.rollback.actions[1:]
                ),
            ),
        }
        for label, changed in mutations.items():
            with self.subTest(label=label):
                self.assertNotEqual(
                    plan._sha256_json(plan._local_plan_identity(changed)),
                    baseline,
                )

        snapshot_mutations = {
            "connection_semantics": replace(
                snapshot,
                connections=(
                    replace(snapshot.connections[0], enabled=False),
                ),
            ),
            "policy_semantics": replace(
                snapshot,
                policies=(replace(snapshot.policies[0], mode="changed"),),
            ),
            "assignment": replace(
                snapshot,
                assignments=(
                    plan.ExistingDeviceAssignment(
                        "02:00:5e:00:00:10", snapshot.policies[0].object_id
                    ),
                ),
            ),
            "default_name": replace(
                snapshot,
                policies=(
                    replace(snapshot.policies[0], name="Changed Default"),
                ),
            ),
        }
        source_baseline = plan.snapshot_semantic_fingerprint(snapshot)
        for label, changed in snapshot_mutations.items():
            with self.subTest(label=label):
                self.assertNotEqual(
                    plan.snapshot_semantic_fingerprint(changed),
                    source_baseline,
                )

    def test_public_fingerprint_covers_every_default_projection_field(self):
        base = plan.build_change_plan(
            one_profile_manifest(),
            plan.parse_router_state_snapshot(fixture("empty-clean-state.json")),
        )
        projection = base.source_default_policy_projection
        baseline = plan._sha256_json(plan._public_plan_identity(base))
        policy_values = list(projection.policy)
        connection_values = list(projection.connection)
        mutations = {
            "status": replace(projection, status="changed"),
            "default_ref": replace(
                projection, default_policy_ref="changed-default-ref"
            ),
        }
        for index, label in enumerate(
            (
                "policy_id",
                "policy_name",
                "policy_connection_ref",
                "policy_mode",
                "policy_semantic_complete",
                "policy_unrelated_rules",
                "policy_observed_default",
            )
        ):
            changed = list(policy_values)
            changed[index] = (
                not changed[index]
                if isinstance(changed[index], bool)
                else "%s-changed" % changed[index]
            )
            mutations[label] = replace(projection, policy=tuple(changed))
        for index, label in enumerate(
            (
                "connection_id",
                "connection_name",
                "connection_protocol",
                "connection_host",
                "connection_port",
                "connection_auth",
                "connection_enabled",
                "connection_semantic_complete",
            )
        ):
            changed = list(connection_values)
            if isinstance(changed[index], bool):
                changed[index] = not changed[index]
            elif isinstance(changed[index], int):
                changed[index] += 1
            else:
                changed[index] = "%s-changed" % changed[index]
            mutations[label] = replace(
                projection, connection=tuple(changed)
            )

        for label, changed_projection in mutations.items():
            with self.subTest(label=label):
                changed = replace(
                    base,
                    source_default_policy_projection=changed_projection,
                )
                self.assertNotEqual(
                    plan._sha256_json(plan._public_plan_identity(changed)),
                    baseline,
                )


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

    def test_simulator_api_signature_guard_rejects_hidden_channels(self):
        def source_for(signature):
            return "def simulate_change_plan({}):\n    pass\n".format(signature)

        def async_source_for(signature):
            return "async def simulate_change_plan({}):\n    pass\n".format(
                signature
            )

        safe_control = (
            "plan, snapshot, *, fail_after=None, rollback_failure=False"
        )
        safe_sync_source = source_for(safe_control)
        safe_async_source = async_source_for(safe_control)
        mutations = {
            "kwargs": safe_control + ", **kwargs",
            "varargs": (
                "plan, snapshot, *args, fail_after=None, "
                "rollback_failure=False"
            ),
            "varargs_and_kwargs": (
                "plan, snapshot, *args, fail_after=None, "
                "rollback_failure=False, **kwargs"
            ),
            "manifest_keyword_only": safe_control + ", manifest=None",
            "profiles_keyword_only": safe_control + ", profiles=None",
            "options_keyword_only": safe_control + ", options=None",
            "renamed_fail_after": (
                "plan, snapshot, *, failure_after=None, "
                "rollback_failure=False"
            ),
            "renamed_rollback_failure": (
                "plan, snapshot, *, fail_after=None, rollback_failed=False"
            ),
            "missing_fail_after_default": (
                "plan, snapshot, *, fail_after, rollback_failure=False"
            ),
            "missing_rollback_failure_default": (
                "plan, snapshot, *, fail_after=None, rollback_failure"
            ),
            "changed_rollback_failure_default": (
                "plan, snapshot, *, fail_after=None, rollback_failure=True"
            ),
            "third_positional_manifest": (
                "plan, snapshot, manifest, *, fail_after=None, "
                "rollback_failure=False"
            ),
            "hidden_positional_only": (
                "hidden, /, plan, snapshot, *, fail_after=None, "
                "rollback_failure=False"
            ),
            "reordered_keyword_only": (
                "plan, snapshot, *, rollback_failure=False, fail_after=None"
            ),
        }

        self.assertEqual(
            find_simulator_api_signature_violations(safe_sync_source),
            [],
        )
        for label, signature in mutations.items():
            with self.subTest(label=label):
                self.assertTrue(
                    find_simulator_api_signature_violations(
                        source_for(signature)
                    )
                )

        definition_mutations = {
            "absent_definition": "def unrelated():\n    pass\n",
            "sync_plus_async_duplicate": (
                safe_sync_source + "\n" + async_source_for("")
            ),
            "async_duplicate_plus_sync": (
                async_source_for("") + "\n" + safe_sync_source
            ),
            "two_synchronous_definitions": (
                safe_sync_source + "\n" + safe_sync_source
            ),
            "two_asynchronous_definitions": (
                safe_async_source + "\n" + safe_async_source
            ),
            "sync_plus_two_async_duplicates": (
                safe_sync_source
                + "\n"
                + safe_async_source
                + "\n"
                + safe_async_source
            ),
        }
        for label, source in definition_mutations.items():
            with self.subTest(label=label):
                self.assertEqual(
                    find_simulator_api_signature_violations(source),
                    [
                        "simulate_change_plan must have exactly one "
                        "top-level definition"
                    ],
                )

        async_only_mutations = {
            "exact_signature": safe_async_source,
            "kwargs": async_source_for(safe_control + ", **kwargs"),
            "hidden_manifest": async_source_for(
                safe_control + ", manifest=None"
            ),
        }
        for label, source in async_only_mutations.items():
            with self.subTest(label=label):
                self.assertEqual(
                    find_simulator_api_signature_violations(source),
                    ["simulate_change_plan must be a synchronous function"],
                )

        nested_controls = (
            (
                "def unrelated():\n"
                "    def simulate_change_plan():\n"
                "        pass\n\n"
                + safe_sync_source
            ),
            (
                "def unrelated():\n"
                "    async def simulate_change_plan():\n"
                "        pass\n\n"
                + safe_sync_source
            ),
        )
        for source in nested_controls:
            with self.subTest(source=source):
                self.assertEqual(
                    find_simulator_api_signature_violations(source),
                    [],
                )

    def test_simulator_runtime_signature_and_rejected_calls_are_exact(self):
        signature = inspect.signature(plan.simulate_change_plan)
        self.assertEqual(
            [
                (item.name, item.kind, item.default)
                for item in signature.parameters.values()
            ],
            [
                (
                    "plan",
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.empty,
                ),
                (
                    "snapshot",
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.empty,
                ),
                ("fail_after", inspect.Parameter.KEYWORD_ONLY, None),
                ("rollback_failure", inspect.Parameter.KEYWORD_ONLY, False),
            ],
        )
        self.assertFalse(
            any(
                item.kind
                in (
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                )
                for item in signature.parameters.values()
            )
        )

        snapshot = plan.parse_router_state_snapshot(
            fixture("empty-clean-state.json")
        )
        change = plan.build_change_plan(one_profile_manifest(), snapshot)
        snapshot_fingerprint = plan.snapshot_semantic_fingerprint(snapshot)
        rejected_calls = {
            "manifest_keyword": lambda: plan.simulate_change_plan(
                change, snapshot, manifest=object()
            ),
            "expanded_manifest_keyword": lambda: plan.simulate_change_plan(
                change, snapshot, **{"manifest": object()}
            ),
            "legacy_manifest_keyword": lambda: plan.simulate_change_plan(
                change, snapshot, legacy_manifest=object()
            ),
            "options_keyword": lambda: plan.simulate_change_plan(
                change, snapshot, options={"manifest": object()}
            ),
            "third_positional": lambda: plan.simulate_change_plan(
                change, snapshot, object()
            ),
            "four_positional": lambda: plan.simulate_change_plan(
                change, snapshot, None, False
            ),
        }
        with mock.patch.object(
            plan,
            "validate_snapshot_consistency",
            side_effect=AssertionError("simulator body must not be entered"),
        ):
            for label, rejected_call in rejected_calls.items():
                with self.subTest(label=label), self.assertRaises(TypeError):
                    rejected_call()
        self.assertEqual(
            plan.snapshot_semantic_fingerprint(snapshot),
            snapshot_fingerprint,
        )

    def test_simulator_exact_signature_preserves_supported_calls(self):
        snapshot = plan.parse_router_state_snapshot(
            fixture("empty-clean-state.json")
        )
        change = plan.build_change_plan(one_profile_manifest(), snapshot)
        snapshot_fingerprint = plan.snapshot_semantic_fingerprint(snapshot)

        for label, result in (
            ("default", plan.simulate_change_plan(change, snapshot)),
            (
                "explicit_fail_after",
                plan.simulate_change_plan(change, snapshot, fail_after=None),
            ),
            (
                "explicit_rollback_failure",
                plan.simulate_change_plan(
                    change, snapshot, rollback_failure=False
                ),
            ),
        ):
            with self.subTest(label=label):
                self.assertTrue(result.success)

        failure = plan.simulate_change_plan(
            change,
            snapshot,
            fail_after="01:connection",
        )
        self.assertFalse(failure.success)
        self.assertTrue(failure.rollback_succeeded)
        self.assertTrue(failure.restored_initial_state)

        rollback_failure = plan.simulate_change_plan(
            change,
            snapshot,
            fail_after="01:connection",
            rollback_failure=True,
        )
        self.assertFalse(rollback_failure.success)
        self.assertFalse(rollback_failure.rollback_succeeded)
        self.assertEqual(rollback_failure.error_category, "rollback_failure")
        self.assertEqual(
            plan.snapshot_semantic_fingerprint(snapshot),
            snapshot_fingerprint,
        )

    def test_semantic_binding_guard_covers_simulator_selected_input_and_public_evidence(self):
        source = (SCRIPTS / "routerkit_netcraze_plan.py").read_text(
            encoding="utf-8"
        )
        self.assertEqual(find_simulator_api_signature_violations(source), [])
        tree = ast.parse(source)
        functions = {
            item.name: item
            for item in tree.body
            if isinstance(item, ast.FunctionDef)
        }
        simulator = functions["simulate_change_plan"]
        simulator_calls = {
            node.func.id
            for node in ast.walk(simulator)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("validate_change_plan_integrity", simulator_calls)
        self.assertIn("snapshot_semantic_fingerprint", simulator_calls)

        for function_name in (
            "build_change_plan",
            "selected_device_from_device_selection",
        ):
            calls = {
                node.func.id
                for node in ast.walk(functions[function_name])
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            self.assertIn("normalize_selected_device_ref", calls)

        renderer = functions["render_plan_json"]
        public_branch = next(
            node
            for node in renderer.body
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "public_evidence"
        )
        public_attributes = {
            node.attr
            for statement in public_branch.body
            for node in ast.walk(statement)
            if isinstance(node, ast.Attribute)
        }
        self.assertIn("public_plan_fingerprint", public_attributes)
        self.assertNotIn("local_integrity_fingerprint", public_attributes)
        self.assertNotIn("source_snapshot_fingerprint", public_attributes)


if __name__ == "__main__":
    unittest.main()
