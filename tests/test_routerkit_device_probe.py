import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts" / "probe-device-discovery-readonly.sh"


class DeviceProbePacketTests(unittest.TestCase):
    def test_probe_prints_contract_pending_without_router_commands(self):
        completed = subprocess.run(
            ["sh", str(PROBE), "--print-contract-pending"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        self.assertEqual(completed.returncode, 0)
        self.assertIn("SOFTWARE_CORE_READY_HARDWARE_CONTRACT_PENDING", completed.stdout)
        self.assertEqual(completed.stderr, "")

    def test_probe_has_empty_router_command_allowlist(self):
        text = PROBE.read_text(encoding="utf-8")
        forbidden = (
            "ndmc ",
            "/rci",
            "curl ",
            "wget ",
            "ssh ",
            "reboot",
            "iptables",
            "nft ",
            "service ",
            "xkeen",
        )

        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main()
