import contextlib
import importlib.util
import io
import shlex
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "routerkit.py"
    spec = importlib.util.spec_from_file_location("routerkit_cli", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


cli = load_module()


class RouterkitCliCommandTests(unittest.TestCase):
    def test_wizard_builds_expected_command(self):
        args = cli.parse_args(["wizard"])

        command = cli.build_command(args, ROOT)

        self.assertEqual(
            command,
            [
                sys.executable,
                str(ROOT / "scripts" / "routerkit-wizard.py"),
                "--profiles",
                "profiles.json",
            ],
        )

    def test_generate_builds_expected_command(self):
        args = cli.parse_args(["generate", "--profiles", "profiles.json", "--out", "generated"])

        command = cli.build_command(args, ROOT)

        self.assertEqual(
            command,
            [
                sys.executable,
                str(ROOT / "scripts" / "generate-xray-profiles.py"),
                "--profiles",
                "profiles.json",
                "--out",
                "generated",
            ],
        )

    def test_plan_json_strict_builds_expected_command(self):
        args = cli.parse_args(["plan", "--generated", "generated", "--json", "--strict"])

        command = cli.build_command(args, ROOT)

        self.assertEqual(
            command,
            [
                sys.executable,
                str(ROOT / "scripts" / "routerkit-plan.py"),
                "--generated",
                "generated",
                "--json",
                "--strict",
            ],
        )

    def test_preflight_builds_expected_command(self):
        args = cli.parse_args(["preflight"])

        self.assertEqual(cli.build_command(args, ROOT), ["sh", str(ROOT / "scripts" / "preflight.sh")])

    def test_healthcheck_builds_expected_command(self):
        args = cli.parse_args(["healthcheck"])

        self.assertEqual(cli.build_command(args, ROOT), ["sh", str(ROOT / "scripts" / "healthcheck.sh")])

    def test_backup_builds_expected_command(self):
        args = cli.parse_args(["backup"])

        self.assertEqual(cli.build_command(args, ROOT), ["sh", str(ROOT / "scripts" / "backup.sh")])

    def test_dry_run_prints_command_without_executing(self):
        command = ["definitely-not-a-routerkit-command", "arg with spaces"]
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            code = cli.run_command(command, dry_run=True)

        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue(), shlex.join(command) + "\n")


class RouterkitCliParseTests(unittest.TestCase):
    def test_unknown_command_fails_via_argparse(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.parse_args(["unknown"])

    def test_missing_required_generate_args_fails_via_argparse(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.parse_args(["generate", "--profiles", "profiles.json"])


if __name__ == "__main__":
    unittest.main()
