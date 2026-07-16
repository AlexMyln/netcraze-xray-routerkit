import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures" / "devices"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import routerkit_devices as devices


def private_inventory_copy(directory, text=None):
    destination = Path(directory) / "inventory.json"
    if text is None:
        text = (FIXTURES / "mixed-inventory.json").read_text(encoding="utf-8")
    destination.write_text(text, encoding="utf-8")
    if os.name == "posix":
        destination.chmod(0o600)
    return destination


def supported_inventory_text():
    return json.dumps(
        {
            "schema": devices.FIXTURE_SCHEMA,
            "sources": [
                {
                    "name": "synthetic-supported",
                    "kind": "dhcp_leases",
                    "state": devices.STATE_SUPPORTED,
                    "records": [
                        {
                            "source_record_id": "dhcp-tv",
                            "display_name": "Living Room TV",
                            "addresses": ["192.0.2.10"],
                            "stable_identifier": "02:00:5e:00:00:10",
                            "stable_identifier_type": "mac",
                            "online_state": "online",
                            "connection_type": "wifi",
                        }
                    ],
                }
            ],
        }
    )


class DevicesCliTests(unittest.TestCase):
    def run_cli(self, argv, input_fn=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        code = devices.main(
            argv,
            input_fn=input_fn or (lambda prompt: "0"),
            output=stdout,
            error=stderr,
        )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_status_without_inventory_is_contract_pending(self):
        code, stdout, stderr = self.run_cli(["status", "--json"])
        payload = json.loads(stdout)

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(payload["adapter_state"], devices.STATE_CONTRACT_UNVERIFIED)

    def test_discover_without_inventory_does_not_touch_router(self):
        code, stdout, stderr = self.run_cli(["discover"])

        self.assertEqual(code, 3)
        self.assertEqual(stdout, "")
        self.assertIn("contract is pending", stderr)

    def test_discover_fixture_text_and_json(self):
        with tempfile.TemporaryDirectory() as directory:
            inventory = private_inventory_copy(directory)
            code, stdout, stderr = self.run_cli(["discover", "--inventory-file", str(inventory)])

            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")
            self.assertIn("Known local devices:", stdout)
            self.assertIn("0. Do not assign a device now", stdout)

            code, stdout, stderr = self.run_cli(["discover", "--inventory-file", str(inventory), "--json"])
            payload = json.loads(stdout)
            self.assertEqual(code, 0)
            self.assertEqual(payload["schema"], devices.DISCOVERY_SCHEMA)

    def test_select_fixture_zero_and_json_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            inventory = private_inventory_copy(directory)

            code, stdout, stderr = self.run_cli(["select", "--inventory-file", str(inventory), "--choice", "0"])
            self.assertEqual(code, 0)
            self.assertIn("no device assignment", stdout)
            self.assertEqual(stderr, "")

        with tempfile.TemporaryDirectory() as directory:
            inventory = private_inventory_copy(directory, supported_inventory_text())
            code, stdout, stderr = self.run_cli(["select", "--inventory-file", str(inventory), "--choice", "1", "--json"])
            payload = json.loads(stdout)
            self.assertEqual(code, 0)
            self.assertTrue(payload["selected"])
            self.assertNotIn("selection_token", payload)
            self.assertEqual(payload["selection_handle"], "internal-only")

    def test_degraded_nonzero_selection_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            inventory = private_inventory_copy(directory)

            code, stdout, stderr = self.run_cli(["select", "--inventory-file", str(inventory), "--choice", "1"])

            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertIn("not complete and trusted", stderr)

    def test_select_invalid_index_fails_before_assignment(self):
        with tempfile.TemporaryDirectory() as directory:
            inventory = private_inventory_copy(directory, supported_inventory_text())

            code, stdout, stderr = self.run_cli(["select", "--inventory-file", str(inventory), "--choice", "99"])

            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertIn("out of range", stderr)

    def test_public_evidence_json_masks_fixture_values(self):
        with tempfile.TemporaryDirectory() as directory:
            inventory = private_inventory_copy(directory)

            code, stdout, stderr = self.run_cli(
                [
                    "discover",
                    "--inventory-file",
                    str(inventory),
                    "--public-evidence",
                    "--redaction-salt",
                    "cli-test",
                ]
            )

            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")
            self.assertNotIn("Living Room TV", stdout)
            self.assertNotIn("192.0.2.10", stdout)

    def test_argument_matrix_rejects_invalid_combinations_before_inventory_read(self):
        missing = "/definitely/missing/inventory.json"
        cases = [
            ["status", "--inventory-file", missing],
            ["status", "--public-evidence"],
            ["status", "--redaction-salt", "salt"],
            ["status", "--choice", "0"],
            ["discover", "--choice", "0", "--inventory-file", missing],
            ["discover", "--redaction-salt", "salt", "--inventory-file", missing],
            ["select", "--public-evidence", "--inventory-file", missing],
            ["select", "--redaction-salt", "salt", "--inventory-file", missing],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                code, stdout, stderr = self.run_cli(argv)
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertNotIn("missing", stderr)

    def test_public_evidence_implies_json(self):
        with tempfile.TemporaryDirectory() as directory:
            inventory = private_inventory_copy(directory)

            code, stdout, stderr = self.run_cli(["discover", "--inventory-file", str(inventory), "--public-evidence"])

            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")
            self.assertEqual(json.loads(stdout)["sensitivity"], devices.SENSITIVITY_PUBLIC)


if __name__ == "__main__":
    unittest.main()
