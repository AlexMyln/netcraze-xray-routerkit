import contextlib
import importlib.util
import io
import shlex
import sys
import unittest
from unittest import mock
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

    def test_install_without_apply_builds_strict_plan_command(self):
        args = cli.parse_args(["install", "--generated", "generated", "--target-root", "/opt"])

        command = cli.build_command(args, ROOT)

        self.assertEqual(
            command,
            [
                sys.executable,
                str(ROOT / "scripts" / "routerkit-plan.py"),
                "--generated",
                "generated",
                "--target-root",
                "/opt",
                "--strict",
            ],
        )

    def test_install_apply_builds_install_command(self):
        args = cli.parse_args(["install", "--generated", "generated", "--apply"])

        command = cli.build_command(args, ROOT)

        self.assertEqual(command, ["sh", str(ROOT / "scripts" / "install-xray-direct.sh"), "generated"])

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

    def test_install_apply_dry_run_prints_command_without_executing(self):
        stdout = io.StringIO()
        with mock.patch.object(cli.subprocess, "run", side_effect=AssertionError("must not execute")):
            with contextlib.redirect_stdout(stdout):
                code = cli.main(["--repo-root", str(ROOT), "install", "--generated", "generated", "--apply", "--dry-run"])

        expected = ["sh", str(ROOT / "scripts" / "install-xray-direct.sh"), "generated"]
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue(), shlex.join(expected) + "\n")

    def test_install_enable_autostart_fails_safely_without_execution(self):
        stderr = io.StringIO()
        with mock.patch.object(cli, "run_command", side_effect=AssertionError("must not execute")):
            with contextlib.redirect_stderr(stderr):
                code = cli.main(["--repo-root", str(ROOT), "install", "--enable-autostart"])

        self.assertEqual(code, 2)
        self.assertIn("Autostart enabling will be added", stderr.getvalue())

    def test_install_apply_enable_autostart_fails_safely_without_execution(self):
        stderr = io.StringIO()
        with mock.patch.object(cli, "run_command", side_effect=AssertionError("must not execute")):
            with contextlib.redirect_stderr(stderr):
                code = cli.main(
                    ["--repo-root", str(ROOT), "install", "--generated", "generated", "--apply", "--enable-autostart"]
                )

        self.assertEqual(code, 2)
        self.assertIn("Autostart enabling will be added", stderr.getvalue())


class RouterkitCliParseTests(unittest.TestCase):
    def test_unknown_command_fails_via_argparse(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.parse_args(["unknown"])

    def test_missing_required_generate_args_fails_via_argparse(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.parse_args(["generate", "--profiles", "profiles.json"])


if __name__ == "__main__":
    unittest.main()
