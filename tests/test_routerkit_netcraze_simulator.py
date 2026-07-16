import json
import sys
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


def manifest():
    value = json.loads((FIXTURES / "local-endpoints.json").read_text())
    value["profiles"] = value["profiles"][:1]
    return plan.parse_local_endpoint_manifest(json.dumps(value))


def snapshot(name="empty-clean-state.json"):
    return plan.parse_router_state_snapshot((FIXTURES / name).read_text())


class SimulatorTests(unittest.TestCase):
    def test_clean_create_preserves_default_and_unrelated_objects(self):
        initial = snapshot("foreign-objects.json")
        change = plan.build_change_plan(manifest(), initial)
        result = plan.simulate_change_plan(change, initial, manifest())

        self.assertTrue(result.success)
        self.assertTrue(result.default_policy_unchanged)
        self.assertTrue(result.unrelated_objects_unchanged)
        self.assertFalse(result.restored_initial_state)
        self.assertTrue(plan.validate_snapshot_consistency(result.final_state).valid)

    def test_exact_reuse_is_noop(self):
        initial = snapshot("exact-equivalent-state.json")
        change = plan.build_change_plan(manifest(), initial)
        result = plan.simulate_change_plan(change, initial, manifest())

        self.assertTrue(result.success)
        self.assertEqual(result.final_state, initial)

    def test_failure_after_each_mutating_stage_rolls_back_in_reverse(self):
        selected = plan.SelectedDeviceRef("Synthetic Tablet", "02:00:5e:00:00:10")
        initial = snapshot()
        change = plan.build_change_plan(manifest(), initial, selected)
        for action_id in ("01:connection", "01:policy", "80:assignment"):
            with self.subTest(action_id=action_id):
                result = plan.simulate_change_plan(
                    change, initial, manifest(), fail_after=action_id
                )
                self.assertFalse(result.success)
                self.assertTrue(result.rollback_succeeded)
                self.assertTrue(result.restored_initial_state)
                self.assertTrue(result.default_policy_unchanged)

    def test_verification_failure_rolls_back_all_mutations(self):
        initial = snapshot()
        change = plan.build_change_plan(manifest(), initial)
        result = plan.simulate_change_plan(
            change, initial, manifest(), fail_after="91:verify-policies"
        )
        self.assertFalse(result.success)
        self.assertEqual(
            [item.operation for item in result.rollback_actions],
            ["remove_created_policy", "remove_created_connection"],
        )
        self.assertTrue(result.restored_initial_state)

    def test_rollback_failure_is_represented(self):
        initial = snapshot()
        change = plan.build_change_plan(manifest(), initial)
        result = plan.simulate_change_plan(
            change,
            initial,
            manifest(),
            fail_after="01:policy",
            rollback_failure=True,
        )
        self.assertFalse(result.rollback_succeeded)
        self.assertEqual(result.error_category, "rollback_failure")
        self.assertFalse(result.restored_initial_state)

    def test_rerun_after_success_is_idempotent(self):
        initial = snapshot()
        first = plan.build_change_plan(manifest(), initial)
        simulated = plan.simulate_change_plan(first, initial, manifest())
        rerun = plan.build_change_plan(manifest(), simulated.final_state)
        self.assertNotIn("create_connection", [item.operation for item in rerun.actions])
        self.assertNotIn("create_policy", [item.operation for item in rerun.actions])

    def test_blocked_plan_does_not_mutate(self):
        initial = snapshot("connection-name-conflict.json")
        change = plan.build_change_plan(manifest(), initial)
        result = plan.simulate_change_plan(change, initial, manifest())
        self.assertEqual(result.error_category, "plan_blocked")
        self.assertEqual(result.final_state, initial)

    def test_orphan_state_after_action_is_rejected_and_rolled_back(self):
        initial = snapshot()
        change = plan.build_change_plan(manifest(), initial)
        real_apply = plan._apply_simulated_action

        def corrupt_after_create(state, action, change_plan, profiles):
            updated = real_apply(state, action, change_plan, profiles)
            if action.operation == "create_connection":
                default = updated.policies[0]
                return replace(
                    updated,
                    policies=(replace(default, connection_ref="missing-connection"),),
                )
            return updated

        with mock.patch.object(
            plan, "_apply_simulated_action", side_effect=corrupt_after_create
        ):
            result = plan.simulate_change_plan(change, initial, manifest())

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, "snapshot_consistency")
        self.assertTrue(result.rollback_succeeded)
        self.assertTrue(result.restored_initial_state)

    def test_removing_a_referenced_connection_during_simulation_is_rejected(self):
        initial = snapshot()
        change = plan.build_change_plan(manifest(), initial)
        real_apply = plan._apply_simulated_action

        def remove_default_connection(state, action, change_plan, profiles):
            updated = real_apply(state, action, change_plan, profiles)
            if action.operation == "create_connection":
                return replace(
                    updated,
                    connections=tuple(
                        item
                        for item in updated.connections
                        if item.object_id != "synthetic-uplink"
                    ),
                )
            return updated

        with mock.patch.object(
            plan, "_apply_simulated_action", side_effect=remove_default_connection
        ):
            result = plan.simulate_change_plan(change, initial, manifest())

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, "snapshot_consistency")
        self.assertTrue(result.restored_initial_state)

    def test_default_policy_projection_is_compared_not_constant(self):
        initial = snapshot()
        change = plan.build_change_plan(manifest(), initial)
        real_apply = plan._apply_simulated_action

        def mutate_default(state, action, change_plan, profiles):
            updated = real_apply(state, action, change_plan, profiles)
            if action.operation == "create_connection":
                policies = tuple(
                    replace(item, name="Mutated-Default")
                    if item.object_id == updated.default_policy_ref
                    else item
                    for item in updated.policies
                )
                return replace(updated, policies=policies)
            return updated

        with mock.patch.object(plan, "_apply_simulated_action", side_effect=mutate_default):
            result = plan.simulate_change_plan(change, initial, manifest())

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, "default_policy_invariant")
        self.assertTrue(result.restored_initial_state)

    def test_referenced_default_connection_mutation_is_detected(self):
        initial = snapshot()
        change = plan.build_change_plan(manifest(), initial)
        real_apply = plan._apply_simulated_action

        def mutate_default_connection(state, action, change_plan, profiles):
            updated = real_apply(state, action, change_plan, profiles)
            if action.operation == "create_connection":
                connections = tuple(
                    replace(item, enabled=False)
                    if item.object_id == "synthetic-uplink"
                    else item
                    for item in updated.connections
                )
                return replace(updated, connections=connections)
            return updated

        with mock.patch.object(
            plan, "_apply_simulated_action", side_effect=mutate_default_connection
        ):
            result = plan.simulate_change_plan(change, initial, manifest())

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, "default_policy_invariant")
        self.assertTrue(result.restored_initial_state)

    def test_rollback_state_is_revalidated(self):
        initial = snapshot()
        change = plan.build_change_plan(manifest(), initial)

        def corrupt_restore(state):
            policies = tuple(
                replace(item, connection_ref="missing-connection")
                if item.object_id == state.default_policy_ref
                else item
                for item in state.policies
            )
            return replace(state, policies=policies)

        with mock.patch.object(
            plan, "_restore_simulated_state", side_effect=corrupt_restore
        ):
            result = plan.simulate_change_plan(
                change, initial, manifest(), fail_after="01:connection"
            )

        self.assertFalse(result.success)
        self.assertFalse(result.rollback_succeeded)
        self.assertEqual(result.error_category, "rollback_validation_failure")
        self.assertFalse(result.default_policy_unchanged)

    def test_invalid_caller_constructed_snapshot_cannot_enter_simulation(self):
        initial = snapshot()
        invalid = replace(
            initial,
            policies=(
                replace(initial.policies[0], connection_ref="missing-connection"),
            ),
        )
        change = plan.build_change_plan(manifest(), initial)
        with self.assertRaises(plan.SnapshotSchemaError):
            plan.simulate_change_plan(change, invalid, manifest())


if __name__ == "__main__":
    unittest.main()
