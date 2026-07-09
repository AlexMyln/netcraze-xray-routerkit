import contextlib
import importlib.util
import io
import shlex
import sys
from types import SimpleNamespace
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "routerkit.py"
    module_name = "routerkit_cli"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


cli = load_module()


def completed(returncode):
    return SimpleNamespace(returncode=returncode)


def apply_commands():
    return [
        [
            sys.executable,
            str(ROOT / "scripts" / "routerkit-plan.py"),
            "--generated",
            "generated",
            "--target-root",
            "/opt",
            "--strict",
        ],
        ["sh", str(ROOT / "scripts" / "preflight.sh")],
        ["sh", str(ROOT / "scripts" / "backup.sh")],
        ["sh", str(ROOT / "scripts" / "install-xray-direct.sh"), "generated"],
        ["sh", str(ROOT / "scripts" / "healthcheck.sh")],
    ]


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

    def test_install_without_apply_prints_plan_only_notice(self):
        stdout = io.StringIO()
        with mock.patch.object(cli.subprocess, "run", return_value=completed(0)) as run:
            with contextlib.redirect_stdout(stdout):
                code = cli.main(["--repo-root", str(ROOT), "install", "--generated", "generated"])

        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.args[0], apply_commands()[0])
        self.assertIn("Install is running in plan-only mode.", stdout.getvalue())
        self.assertIn("Use --apply to install generated configs and S23xray-direct.", stdout.getvalue())

    def test_install_apply_builds_default_pipeline_in_exact_order(self):
        args = cli.parse_args(["install", "--generated", "generated", "--apply"])

        steps = cli.build_install_apply_steps(args, ROOT)

        self.assertEqual([step.command for step in steps], apply_commands())
        self.assertEqual([step.name for step in steps], ["strict plan", "preflight", "backup", "install", "healthcheck"])

    def test_install_apply_skip_preflight_omits_preflight_only(self):
        args = cli.parse_args(["install", "--generated", "generated", "--apply", "--skip-preflight"])

        steps = cli.build_install_apply_steps(args, ROOT)

        self.assertEqual([step.name for step in steps], ["strict plan", "backup", "install", "healthcheck"])

    def test_install_apply_skip_backup_omits_backup_and_summary_marks_skipped(self):
        args = cli.parse_args(["install", "--generated", "generated", "--apply", "--skip-backup"])
        stdout = io.StringIO()

        steps = cli.build_install_apply_steps(args, ROOT)
        with contextlib.redirect_stdout(stdout):
            cli.print_apply_summary(steps)

        self.assertEqual([step.name for step in steps], ["strict plan", "preflight", "install", "healthcheck"])
        self.assertIn("Backup was skipped; rollback files may not be available.", stdout.getvalue())

    def test_install_apply_skip_healthcheck_omits_healthcheck_only(self):
        args = cli.parse_args(["install", "--generated", "generated", "--apply", "--skip-healthcheck"])

        steps = cli.build_install_apply_steps(args, ROOT)

        self.assertEqual([step.name for step in steps], ["strict plan", "preflight", "backup", "install"])

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
        for argv in (
            ["--repo-root", str(ROOT), "--dry-run", "install", "--generated", "generated", "--apply"],
            ["--repo-root", str(ROOT), "install", "--generated", "generated", "--apply", "--dry-run"],
        ):
            with self.subTest(argv=argv):
                stdout = io.StringIO()
                with mock.patch.object(cli.subprocess, "run", side_effect=AssertionError("must not execute")):
                    with contextlib.redirect_stdout(stdout):
                        code = cli.main(argv)

                self.assertEqual(code, 0)
                self.assertEqual(
                    stdout.getvalue(),
                    "\n".join(
                        [
                            "Would run install apply pipeline:",
                            "1. python3 scripts/routerkit-plan.py --generated generated --target-root /opt --strict",
                            "2. sh scripts/preflight.sh",
                            "3. sh scripts/backup.sh",
                            "4. sh scripts/install-xray-direct.sh generated",
                            "5. sh scripts/healthcheck.sh",
                        ]
                    )
                    + "\n",
                )

    def test_install_apply_stops_on_failed_preflight(self):
        stderr = io.StringIO()
        with mock.patch.object(cli.subprocess, "run", side_effect=[completed(0), completed(12)]) as run:
            with contextlib.redirect_stderr(stderr):
                code = cli.main(["--repo-root", str(ROOT), "install", "--generated", "generated", "--apply"])

        self.assertEqual(code, 12)
        self.assertEqual([call.args[0] for call in run.call_args_list], apply_commands()[:2])
        self.assertIn("preflight failed with exit code 12", stderr.getvalue())
        self.assertNotIn("Rollback hint", stderr.getvalue())

    def test_install_apply_stops_on_failed_install_with_rollback_hint(self):
        stderr = io.StringIO()
        with mock.patch.object(
            cli.subprocess,
            "run",
            side_effect=[completed(0), completed(0), completed(0), completed(23)],
        ) as run:
            with contextlib.redirect_stderr(stderr):
                code = cli.main(["--repo-root", str(ROOT), "install", "--generated", "generated", "--apply"])

        self.assertEqual(code, 23)
        self.assertEqual([call.args[0] for call in run.call_args_list], apply_commands()[:4])
        output = stderr.getvalue()
        self.assertIn("install failed with exit code 23", output)
        self.assertIn("Rollback hint:", output)
        self.assertIn("Backup was created by the previous safety step.", output)
        self.assertIn("Use the backup output/path printed by scripts/backup.sh above.", output)
        self.assertIn("Do not publish backup archives.", output)

    def test_install_apply_failed_install_after_skipped_backup_reports_missing_rollback_files(self):
        stderr = io.StringIO()
        with mock.patch.object(
            cli.subprocess,
            "run",
            side_effect=[completed(0), completed(0), completed(23)],
        ):
            with contextlib.redirect_stderr(stderr):
                code = cli.main(
                    [
                        "--repo-root",
                        str(ROOT),
                        "install",
                        "--generated",
                        "generated",
                        "--apply",
                        "--skip-backup",
                    ]
                )

        self.assertEqual(code, 23)
        self.assertIn("Backup was skipped; rollback files may not be available.", stderr.getvalue())

    def test_install_apply_failed_healthcheck_warns_after_install(self):
        stderr = io.StringIO()
        with mock.patch.object(
            cli.subprocess,
            "run",
            side_effect=[completed(0), completed(0), completed(0), completed(0), completed(31)],
        ) as run:
            with contextlib.redirect_stderr(stderr):
                code = cli.main(["--repo-root", str(ROOT), "install", "--generated", "generated", "--apply"])

        self.assertEqual(code, 31)
        self.assertEqual([call.args[0] for call in run.call_args_list], apply_commands())
        output = stderr.getvalue()
        self.assertIn("healthcheck failed with exit code 31", output)
        self.assertIn("Install may have completed but healthcheck failed.", output)
        self.assertIn("Use the backup created before apply if rollback is needed.", output)
        self.assertIn("Do not publish backup archives.", output)

    def test_install_apply_success_prints_summary(self):
        stdout = io.StringIO()
        with mock.patch.object(
            cli.subprocess,
            "run",
            side_effect=[completed(0), completed(0), completed(0), completed(0), completed(0)],
        ) as run:
            with contextlib.redirect_stdout(stdout):
                code = cli.main(["--repo-root", str(ROOT), "install", "--generated", "generated", "--apply"])

        self.assertEqual(code, 0)
        self.assertEqual([call.args[0] for call in run.call_args_list], apply_commands())
        output = stdout.getvalue()
        self.assertIn("Strict plan passed.", output)
        self.assertIn("Preflight passed.", output)
        self.assertIn("Backup completed.", output)
        self.assertIn("Install completed.", output)
        self.assertIn("Healthcheck passed.", output)
        self.assertIn("Autostart was not enabled.", output)
        self.assertIn("Web UI policies were not changed.", output)
        self.assertIn("Firewall rules were not changed.", output)
        self.assertIn("xkeen -start was not called.", output)

    def test_install_enable_autostart_fails_safely_without_execution(self):
        stderr = io.StringIO()
        with mock.patch.object(cli.subprocess, "run", side_effect=AssertionError("must not execute")):
            with contextlib.redirect_stderr(stderr):
                code = cli.main(["--repo-root", str(ROOT), "install", "--enable-autostart"])

        self.assertEqual(code, 2)
        self.assertIn("Autostart enabling will be added", stderr.getvalue())

    def test_install_apply_enable_autostart_fails_safely_without_execution(self):
        stderr = io.StringIO()
        with mock.patch.object(cli.subprocess, "run", side_effect=AssertionError("must not execute")):
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
