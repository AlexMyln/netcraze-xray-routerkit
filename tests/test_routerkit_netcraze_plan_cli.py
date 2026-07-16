import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIRECT = ROOT / "scripts" / "routerkit-netcraze-plan.py"
UNIFIED = ROOT / "scripts" / "routerkit.py"
FIXTURES = ROOT / "tests" / "fixtures" / "netcraze"


def run_cli(entrypoint, *args):
    return subprocess.run(
        [sys.executable, str(entrypoint), *args],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


class NetcrazePlanCliTests(unittest.TestCase):
    def protected_inputs(self, directory, state="empty-clean-state.json"):
        root = Path(directory)
        manifest = root / "manifest.json"
        snapshot = root / "snapshot.json"
        manifest.write_text((FIXTURES / "local-endpoints.json").read_text(), encoding="utf-8")
        snapshot.write_text((FIXTURES / state).read_text(), encoding="utf-8")
        os.chmod(manifest, 0o600)
        os.chmod(snapshot, 0o600)
        return manifest, snapshot

    def test_status_reads_no_inputs_and_direct_unified_match(self):
        direct = run_cli(DIRECT, "status")
        unified = run_cli(UNIFIED, "netcraze-plan", "status")
        self.assertEqual(direct.returncode, 0)
        self.assertEqual(unified.returncode, 0)
        self.assertEqual(direct.stdout, unified.stdout)
        self.assertIn("HARDWARE_WRITE_CONTRACT_PENDING", direct.stdout)

    def test_plan_text_json_and_public_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest, snapshot = self.protected_inputs(directory)
            text = run_cli(
                DIRECT,
                "plan",
                "--manifest-file",
                str(manifest),
                "--state-file",
                str(snapshot),
            )
            local = run_cli(
                DIRECT,
                "plan",
                "--manifest-file",
                str(manifest),
                "--state-file",
                str(snapshot),
                "--json",
            )
            public = run_cli(
                DIRECT,
                "plan",
                "--manifest-file",
                str(manifest),
                "--state-file",
                str(snapshot),
                "--public-evidence",
            )
        self.assertEqual(text.returncode, 0)
        self.assertEqual(local.returncode, 0)
        self.assertEqual(public.returncode, 0)
        self.assertIn("No router connection", text.stdout)
        self.assertEqual(json.loads(local.stdout)["write_readiness"], "blocked")
        self.assertNotIn("synthetic-empty", public.stdout)
        self.assertNotIn("Synthetic-Default", public.stdout)

    def test_simulate_requires_explicit_fixture_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest, snapshot = self.protected_inputs(directory)
            missing = run_cli(
                DIRECT,
                "simulate",
                "--manifest-file",
                str(manifest),
                "--state-file",
                str(snapshot),
            )
            explicit = run_cli(
                DIRECT,
                "simulate",
                "--manifest-file",
                str(manifest),
                "--state-file",
                str(snapshot),
                "--fixture-simulation",
                "--json",
            )
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(explicit.returncode, 0)
        self.assertFalse(json.loads(explicit.stdout)["hardware_proof"])

    def test_apply_like_options_and_modes_are_rejected_before_file_reads(self):
        for args in (
            ("apply",),
            ("plan", "--apply", "--manifest-file", "/missing", "--state-file", "/missing"),
            ("execute",),
            ("commit",),
            ("write",),
        ):
            with self.subTest(args=args):
                result = run_cli(DIRECT, *args)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("Could not read", result.stderr)

    def test_unified_and_direct_plan_parity(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest, snapshot = self.protected_inputs(directory)
            args = (
                "plan",
                "--manifest-file",
                str(manifest),
                "--state-file",
                str(snapshot),
                "--json",
            )
            direct = run_cli(DIRECT, *args)
            unified = run_cli(UNIFIED, "netcraze-plan", *args)
        self.assertEqual((direct.returncode, direct.stdout), (unified.returncode, unified.stdout))

    def test_optional_device_uses_protected_21_inventory_and_explicit_choice(self):
        inventory_value = {
            "schema": "routerkit.devices.fixture.v1",
            "sources": [
                {
                    "name": "synthetic-netcraze-cli",
                    "kind": "dhcp_leases",
                    "state": "supported",
                    "confidence": "fixture",
                    "records": [
                        {
                            "source_record_id": "tablet",
                            "display_name": "Synthetic Tablet",
                            "addresses": ["192.0.2.40"],
                            "stable_identifier": "02:00:5e:00:00:40",
                            "stable_identifier_type": "mac",
                            "online_state": "online",
                            "connection_type": "wifi",
                        }
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            manifest, snapshot = self.protected_inputs(directory)
            inventory = Path(directory) / "devices.json"
            inventory.write_text(json.dumps(inventory_value), encoding="utf-8")
            os.chmod(inventory, 0o600)
            result = run_cli(
                UNIFIED,
                "netcraze-plan",
                "plan",
                "--manifest-file",
                str(manifest),
                "--state-file",
                str(snapshot),
                "--device-inventory-file",
                str(inventory),
                "--device-choice",
                "1",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Synthetic Tablet", result.stdout)
        self.assertIn("assign_device", result.stdout)
        self.assertNotIn("routerkit-device-selection-v1", result.stdout)

    def test_invalid_device_handoff_stops_before_plan_output(self):
        inventory_value = {
            "schema": "routerkit.devices.fixture.v1",
            "sources": [
                {
                    "name": "synthetic-invalid-netcraze-cli",
                    "kind": "dhcp_leases",
                    "state": "supported",
                    "confidence": "fixture",
                    "records": [
                        {
                            "source_record_id": "invalid-tablet",
                            "display_name": "PRIVATE_INVALID_DEVICE",
                            "addresses": ["192.0.2.41"],
                            "stable_identifier": "00:00:00:00:00:00",
                            "stable_identifier_type": "mac",
                            "online_state": "online",
                            "connection_type": "wifi",
                        }
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            manifest, snapshot = self.protected_inputs(directory)
            inventory = Path(directory) / "devices.json"
            inventory.write_text(json.dumps(inventory_value), encoding="utf-8")
            os.chmod(inventory, 0o600)
            result = run_cli(
                UNIFIED,
                "netcraze-plan",
                "plan",
                "--manifest-file",
                str(manifest),
                "--state-file",
                str(snapshot),
                "--device-inventory-file",
                str(inventory),
                "--device-choice",
                "1",
            )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("assign_device", result.stdout)
        self.assertNotIn("selected_device_present", result.stdout)
        self.assertNotIn("00:00:00:00:00:00", result.stderr)
        self.assertNotIn("PRIVATE_INVALID_DEVICE", result.stderr)

    def test_device_file_and_choice_must_be_paired_before_reads(self):
        result = run_cli(
            DIRECT,
            "plan",
            "--manifest-file",
            "/missing/manifest",
            "--state-file",
            "/missing/state",
            "--device-choice",
            "0",
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Could not read", result.stderr)

    def test_conflict_returns_two_with_diagnostic_output(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest, snapshot = self.protected_inputs(directory, "connection-name-conflict.json")
            result = run_cli(
                DIRECT,
                "plan",
                "--manifest-file",
                str(manifest),
                "--state-file",
                str(snapshot),
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("same-name connection", result.stdout)


if __name__ == "__main__":
    unittest.main()
