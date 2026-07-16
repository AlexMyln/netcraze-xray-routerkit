import json
import sys
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
