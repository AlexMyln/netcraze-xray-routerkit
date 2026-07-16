import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts" / "probe-netcraze-policy-contract.sh"


class NetcrazeProbeTests(unittest.TestCase):
    def test_shell_syntax_and_contract_status(self):
        syntax = subprocess.run(["sh", "-n", str(PROBE)], check=False)
        result = subprocess.run(
            ["sh", str(PROBE), "--print-contract-pending"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(syntax.returncode, 0)
        self.assertEqual(result.returncode, 0)
        self.assertIn("SOFTWARE_PLAN_CORE_READY_HARDWARE_WRITE_CONTRACT_PENDING", result.stdout)

    def test_default_and_unknown_options_are_inert(self):
        for args in ((), ("--unknown",)):
            result = subprocess.run(
                ["sh", str(PROBE), *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)

    def test_no_network_command_or_hidden_switch_in_script(self):
        source = PROBE.read_text(encoding="utf-8")
        forbidden = (
            "curl ",
            "wget ",
            "ssh ",
            "telnet ",
            "/rci",
            "ROUTERKIT_ENABLE",
            "eval ",
            "source ",
            "tee ",
        )
        for token in forbidden:
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
