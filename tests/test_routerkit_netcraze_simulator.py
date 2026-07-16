import json
import inspect
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
    return manifest_count(1)


def manifest_count(count):
    value = json.loads((FIXTURES / "local-endpoints.json").read_text())
    value["profiles"] = value["profiles"][:count]
    return plan.parse_local_endpoint_manifest(json.dumps(value))


def snapshot(name="empty-clean-state.json"):
    return plan.parse_router_state_snapshot((FIXTURES / name).read_text())


class SimulatorTests(unittest.TestCase):
    def test_clean_create_preserves_default_and_unrelated_objects(self):
        initial = snapshot("foreign-objects.json")
        change = plan.build_change_plan(manifest(), initial)
        result = plan.simulate_change_plan(change, initial)

        self.assertTrue(result.success)
        self.assertTrue(result.default_policy_unchanged)
        self.assertTrue(result.unrelated_objects_unchanged)
        self.assertFalse(result.restored_initial_state)
        self.assertTrue(plan.validate_snapshot_consistency(result.final_state).valid)

    def test_exact_reuse_is_noop(self):
        initial = snapshot("exact-equivalent-state.json")
        change = plan.build_change_plan(manifest(), initial)
        result = plan.simulate_change_plan(change, initial)

        self.assertTrue(result.success)
        self.assertEqual(result.final_state, initial)

    def test_failure_after_each_mutating_stage_rolls_back_in_reverse(self):
        selected = plan.SelectedDeviceRef("Synthetic Tablet", "02:00:5e:00:00:10")
        initial = snapshot()
        change = plan.build_change_plan(manifest(), initial, selected)
        for action_id in ("01:connection", "01:policy", "80:assignment"):
            with self.subTest(action_id=action_id):
                result = plan.simulate_change_plan(
                    change, initial, fail_after=action_id
                )
                self.assertFalse(result.success)
                self.assertTrue(result.rollback_succeeded)
                self.assertTrue(result.restored_initial_state)
                self.assertTrue(result.default_policy_unchanged)

    def test_verification_failure_rolls_back_all_mutations(self):
        initial = snapshot()
        change = plan.build_change_plan(manifest(), initial)
        result = plan.simulate_change_plan(
            change, initial, fail_after="91:verify-policies"
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
            fail_after="01:policy",
            rollback_failure=True,
        )
        self.assertFalse(result.rollback_succeeded)
        self.assertEqual(result.error_category, "rollback_failure")
        self.assertFalse(result.restored_initial_state)

    def test_rerun_after_success_is_idempotent(self):
        initial = snapshot()
        first = plan.build_change_plan(manifest(), initial)
        simulated = plan.simulate_change_plan(first, initial)
        rerun = plan.build_change_plan(manifest(), simulated.final_state)
        self.assertNotIn("create_connection", [item.operation for item in rerun.actions])
        self.assertNotIn("create_policy", [item.operation for item in rerun.actions])

    def test_simulator_api_has_no_independent_manifest_parameter(self):
        self.assertNotIn(
            "manifest", inspect.signature(plan.simulate_change_plan).parameters
        )
        initial = snapshot()
        change = plan.build_change_plan(manifest(), initial)
        with self.assertRaises(TypeError):
            plan.simulate_change_plan(change, initial, manifest())

    def test_plan_is_bound_to_exact_source_snapshot_before_mutation(self):
        source = snapshot()
        different = snapshot("foreign-objects.json")
        change = plan.build_change_plan(manifest(), source)

        result = plan.simulate_change_plan(change, different)

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, "plan_snapshot_mismatch")
        self.assertEqual(result.completed_actions, ())
        self.assertEqual(result.final_state, different)

    def test_plan_integrity_mismatch_matrix_rejects_before_mutation(self):
        initial = snapshot()
        selected = plan.SelectedDeviceRef(
            "Synthetic Tablet", "02:00:5e:00:00:10"
        )
        change = plan.build_change_plan(manifest_count(2), initial, selected)
        first = change.actions[0]
        policy = next(
            item for item in change.actions if item.action_id == "01:policy"
        )
        rollback = change.rollback.actions[0]
        mutations = {
            "desired_profile": replace(
                change,
                desired_profiles=(
                    replace(change.desired_profiles[0], host="::1"),
                )
                + change.desired_profiles[1:],
            ),
            "action_endpoint": replace(
                change,
                actions=(replace(first, endpoint="::1:1082"),)
                + change.actions[1:],
            ),
            "action_proposed": replace(
                change,
                actions=(
                    replace(
                        first,
                        proposed=tuple(
                            sorted(
                                dict(first.proposed, host="::1").items()
                            )
                        ),
                    ),
                )
                + change.actions[1:],
            ),
            "dependency": replace(
                change,
                actions=tuple(
                    replace(
                        item,
                        dependencies=(
                            plan.ObjectReference(
                                "planned_connection", profile_slot=2
                            ),
                        ),
                    )
                    if item.action_id == policy.action_id
                    else item
                    for item in change.actions
                ),
            ),
            "rollback": replace(
                change,
                rollback=plan.RollbackPlan(
                    (replace(rollback, operation="wrong_rollback"),)
                    + change.rollback.actions[1:]
                ),
            ),
            "local_fingerprint": replace(
                change, local_integrity_fingerprint="0" * 64
            ),
            "selected_device": replace(
                change,
                selected_device=replace(
                    selected, mac="00:00:00:00:00:00"
                ),
            ),
            "action_order": replace(
                change,
                actions=(change.actions[1], change.actions[0])
                + change.actions[2:],
            ),
            "missing_action": replace(change, actions=change.actions[:-1]),
            "extra_action": replace(
                change, actions=change.actions + (change.actions[-1],)
            ),
        }
        for label, tampered in mutations.items():
            with self.subTest(label=label):
                result = plan.simulate_change_plan(tampered, initial)
                self.assertFalse(result.success)
                self.assertEqual(result.error_category, "plan_integrity")
                self.assertEqual(result.completed_actions, ())
                self.assertEqual(result.final_state, initial)

    def test_recomputed_tampered_plan_still_fails_exact_replan_validation(self):
        initial = snapshot()
        change = plan.build_change_plan(manifest(), initial)
        first = replace(change.actions[0], observed_name="synthetic-tamper")
        tampered = replace(
            change,
            actions=(first,) + change.actions[1:],
            local_integrity_fingerprint="",
            public_plan_fingerprint="",
        )
        tampered = replace(
            tampered,
            local_integrity_fingerprint=plan._sha256_json(
                plan._local_plan_identity(tampered)
            ),
            public_plan_fingerprint=plan._sha256_json(
                plan._public_plan_identity(tampered)
            ),
        )
        plan.validate_change_plan_integrity(tampered)

        result = plan.simulate_change_plan(tampered, initial)

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, "plan_integrity")
        self.assertEqual(result.completed_actions, ())
        self.assertEqual(result.final_state, initial)

    def test_created_object_semantics_are_verified_after_each_mutation(self):
        initial = snapshot()
        change = plan.build_change_plan(manifest(), initial)
        real_apply = plan._apply_simulated_action

        def create_wrong_host(state, action, change_plan):
            updated = real_apply(state, action, change_plan)
            if action.operation == "create_connection":
                return replace(
                    updated,
                    connections=tuple(
                        replace(item, host="::1")
                        if item.object_id == "simulation:connection:slot-1"
                        else item
                        for item in updated.connections
                    ),
                )
            return updated

        with mock.patch.object(
            plan, "_apply_simulated_action", side_effect=create_wrong_host
        ):
            result = plan.simulate_change_plan(change, initial)

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, "simulation_verification")
        self.assertEqual(result.completed_actions, ())
        self.assertTrue(result.restored_initial_state)

    def test_idempotent_rerun_for_one_two_three_slots_and_mixed_reuse(self):
        for count in (1, 2, 3):
            desired = manifest_count(count)
            initial = snapshot()
            with self.subTest(count=count):
                first = plan.build_change_plan(desired, initial)
                simulated = plan.simulate_change_plan(first, initial)
                self.assertTrue(simulated.success)
                rerun = plan.build_change_plan(desired, simulated.final_state)
                self.assertFalse(rerun.blocked)
                self.assertTrue(
                    all(
                        item.operation
                        in (
                            "reuse_connection",
                            "reuse_policy",
                            "reuse_assignment",
                            "verify",
                        )
                        for item in rerun.actions
                    )
                )

        desired = manifest_count(2)
        mixed_initial = snapshot("exact-equivalent-state.json")
        mixed = plan.build_change_plan(desired, mixed_initial)
        self.assertEqual(
            [item.operation for item in mixed.actions[:2]],
            ["reuse_connection", "create_connection"],
        )
        simulated = plan.simulate_change_plan(mixed, mixed_initial)
        self.assertTrue(simulated.success)
        rerun = plan.build_change_plan(desired, simulated.final_state)
        self.assertTrue(
            all(
                item.operation
                in ("reuse_connection", "reuse_policy", "verify")
                for item in rerun.actions
            )
        )

    def test_blocked_plan_does_not_mutate(self):
        initial = snapshot("connection-name-conflict.json")
        change = plan.build_change_plan(manifest(), initial)
        result = plan.simulate_change_plan(change, initial)
        self.assertEqual(result.error_category, "plan_blocked")
        self.assertEqual(result.final_state, initial)

    def test_orphan_state_after_action_is_rejected_and_rolled_back(self):
        initial = snapshot()
        change = plan.build_change_plan(manifest(), initial)
        real_apply = plan._apply_simulated_action

        def corrupt_after_create(state, action, change_plan):
            updated = real_apply(state, action, change_plan)
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
            result = plan.simulate_change_plan(change, initial)

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, "snapshot_consistency")
        self.assertTrue(result.rollback_succeeded)
        self.assertTrue(result.restored_initial_state)

    def test_removing_a_referenced_connection_during_simulation_is_rejected(self):
        initial = snapshot()
        change = plan.build_change_plan(manifest(), initial)
        real_apply = plan._apply_simulated_action

        def remove_default_connection(state, action, change_plan):
            updated = real_apply(state, action, change_plan)
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
            result = plan.simulate_change_plan(change, initial)

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, "snapshot_consistency")
        self.assertTrue(result.restored_initial_state)

    def test_default_policy_projection_is_compared_not_constant(self):
        initial = snapshot()
        change = plan.build_change_plan(manifest(), initial)
        real_apply = plan._apply_simulated_action

        def mutate_default(state, action, change_plan):
            updated = real_apply(state, action, change_plan)
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
            result = plan.simulate_change_plan(change, initial)

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, "default_policy_invariant")
        self.assertTrue(result.restored_initial_state)

    def test_referenced_default_connection_mutation_is_detected(self):
        initial = snapshot()
        change = plan.build_change_plan(manifest(), initial)
        real_apply = plan._apply_simulated_action

        def mutate_default_connection(state, action, change_plan):
            updated = real_apply(state, action, change_plan)
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
            result = plan.simulate_change_plan(change, initial)

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
                change, initial, fail_after="01:connection"
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
            plan.simulate_change_plan(change, invalid)


if __name__ == "__main__":
    unittest.main()
