import contextlib
import importlib.util
import io
import os
import shlex
import sys
import tempfile
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


def setup_local_commands(profiles, generated, target_root="/opt", include_wizard=True):
    commands = []
    if include_wizard:
        commands.append(
            [
                sys.executable,
                str(ROOT / "scripts" / "routerkit-wizard.py"),
                "--profiles",
                profiles,
                "--no-generator-prompt",
            ]
        )
    commands.extend(
        [
            [
                sys.executable,
                str(ROOT / "scripts" / "generate-xray-profiles.py"),
                "--profiles",
                profiles,
                "--out",
                generated,
            ],
            [
                sys.executable,
                str(ROOT / "scripts" / "routerkit-plan.py"),
                "--generated",
                generated,
                "--target-root",
                target_root,
                "--strict",
            ],
        ]
    )
    return commands


def setup_apply_commands(generated):
    return [
        ["sh", str(ROOT / "scripts" / "preflight.sh")],
        ["sh", str(ROOT / "scripts" / "backup.sh")],
        ["sh", str(ROOT / "scripts" / "install-xray-direct.sh"), generated],
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

    def test_install_without_apply_builds_strict_plan_for_custom_target_root(self):
        args = cli.parse_args(
            ["install", "--generated", "generated", "--target-root", "/tmp/routerkit-test"]
        )

        command = cli.build_command(args, ROOT)

        self.assertEqual(
            command,
            [
                sys.executable,
                str(ROOT / "scripts" / "routerkit-plan.py"),
                "--generated",
                "generated",
                "--target-root",
                "/tmp/routerkit-test",
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

    def test_install_apply_with_opt_target_builds_default_pipeline_in_exact_order(self):
        args = cli.parse_args(
            ["install", "--generated", "generated", "--target-root", "/opt", "--apply"]
        )

        steps = cli.build_install_apply_steps(args, ROOT)

        self.assertEqual([step.command for step in steps], apply_commands())
        self.assertEqual([step.name for step in steps], ["strict plan", "preflight", "backup", "install", "healthcheck"])

    def test_install_apply_rejects_custom_target_root_without_execution(self):
        stderr = io.StringIO()
        with mock.patch.object(cli.subprocess, "run") as run:
            with contextlib.redirect_stderr(stderr):
                code = cli.main(
                    [
                        "--repo-root",
                        str(ROOT),
                        "install",
                        "--generated",
                        "generated",
                        "--target-root",
                        "/tmp/routerkit-test",
                        "--apply",
                    ]
                )

        self.assertEqual(code, 2)
        run.assert_not_called()
        self.assertIn(
            "routerkit: install --apply currently supports only --target-root /opt.",
            stderr.getvalue(),
        )

    def test_install_skip_flags_require_apply_without_execution(self):
        for skip_flag in ("--skip-preflight", "--skip-backup", "--skip-healthcheck"):
            with self.subTest(skip_flag=skip_flag):
                stderr = io.StringIO()
                with mock.patch.object(cli.subprocess, "run") as run:
                    with contextlib.redirect_stderr(stderr):
                        code = cli.main(["--repo-root", str(ROOT), "install", skip_flag])

                self.assertEqual(code, 2)
                run.assert_not_called()
                self.assertIn(
                    "routerkit: --skip-preflight, --skip-backup, and --skip-healthcheck require --apply.",
                    stderr.getvalue(),
                )

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

    def test_install_apply_oserror_on_install_prints_rollback_hint_and_stops(self):
        stderr = io.StringIO()
        with mock.patch.object(
            cli.subprocess,
            "run",
            side_effect=[completed(0), completed(0), completed(0), OSError("install unavailable")],
        ) as run:
            with contextlib.redirect_stderr(stderr):
                code = cli.main(["--repo-root", str(ROOT), "install", "--generated", "generated", "--apply"])

        self.assertEqual(code, 127)
        self.assertEqual([call.args[0] for call in run.call_args_list], apply_commands()[:4])
        output = stderr.getvalue()
        self.assertIn("routerkit: could not run install: install unavailable", output)
        self.assertIn("Rollback hint:", output)
        self.assertIn("Backup was created by the previous safety step.", output)
        self.assertIn("Do not publish backup archives.", output)

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

    def test_install_apply_oserror_on_healthcheck_warns_and_stops(self):
        stderr = io.StringIO()
        steps = cli.build_install_apply_steps(
            cli.parse_args(["install", "--generated", "generated", "--apply"]),
            ROOT,
        )
        steps.append(cli.CommandStep("after healthcheck", ["must-not-run"]))
        with mock.patch.object(
            cli.subprocess,
            "run",
            side_effect=[completed(0), completed(0), completed(0), completed(0), OSError("healthcheck unavailable")],
        ) as run:
            with contextlib.redirect_stderr(stderr):
                code = cli.run_steps(steps)

        self.assertEqual(code, 127)
        self.assertEqual([call.args[0] for call in run.call_args_list], apply_commands())
        output = stderr.getvalue()
        self.assertIn("routerkit: could not run healthcheck: healthcheck unavailable", output)
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


class RouterkitSetupTests(unittest.TestCase):
    def test_missing_profiles_runs_wizard_generate_plan_in_exact_order(self):
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            profiles = str(Path(tmp) / "profiles.json")
            generated = str(Path(tmp) / "generated")
            with mock.patch.object(cli.subprocess, "run", return_value=completed(0)) as run:
                with contextlib.redirect_stdout(stdout):
                    code = cli.main(
                        [
                            "--repo-root",
                            str(ROOT),
                            "setup",
                            "--profiles",
                            profiles,
                            "--generated",
                            generated,
                        ]
                    )

        self.assertEqual(code, 0)
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            setup_local_commands(profiles, generated),
        )
        output = stdout.getvalue()
        self.assertIn("Setup plan completed.", output)
        self.assertIn("No router apply steps were executed.", output)
        self.assertIn("Use --apply to continue through preflight, backup, install, and healthcheck.", output)

    def test_existing_profiles_skips_wizard_and_reuses_path_without_reading_contents(self):
        synthetic_secret = "SYNTHETIC_PRIVATE_VALUE_123"
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            profiles_path = Path(tmp) / "profiles.json"
            profiles_path.write_text(synthetic_secret, encoding="utf-8")
            profiles = str(profiles_path)
            generated = str(Path(tmp) / "generated")
            with mock.patch.object(cli.subprocess, "run", return_value=completed(0)) as run:
                with contextlib.redirect_stdout(stdout):
                    code = cli.main(
                        [
                            "--repo-root",
                            str(ROOT),
                            "setup",
                            "--profiles",
                            profiles,
                            "--generated",
                            generated,
                        ]
                    )

        self.assertEqual(code, 0)
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            setup_local_commands(profiles, generated, include_wizard=False),
        )
        self.assertIn(f"Reusing existing profiles file: {profiles}", stdout.getvalue())
        self.assertNotIn(synthetic_secret, stdout.getvalue())

    def test_force_wizard_runs_wizard_for_existing_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles_path = Path(tmp) / "profiles.json"
            profiles_path.write_text("{}", encoding="utf-8")
            profiles = str(profiles_path)
            generated = str(Path(tmp) / "generated")
            args = cli.parse_args(
                [
                    "setup",
                    "--profiles",
                    profiles,
                    "--generated",
                    generated,
                    "--force-wizard",
                ]
            )
            with mock.patch.object(cli.subprocess, "run", return_value=completed(0)) as run:
                code = cli.run_setup(args, ROOT)

        self.assertEqual(code, 0)
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            setup_local_commands(profiles, generated),
        )

    def test_apply_refusal_stops_before_router_apply(self):
        stdout = io.StringIO()
        input_fn = mock.Mock(return_value="")
        with tempfile.TemporaryDirectory() as tmp:
            profiles_path = Path(tmp) / "profiles.json"
            profiles_path.write_text("{}", encoding="utf-8")
            profiles = str(profiles_path)
            generated = str(Path(tmp) / "generated")
            args = cli.parse_args(
                ["setup", "--profiles", profiles, "--generated", generated, "--apply"]
            )
            with mock.patch.object(cli.subprocess, "run", return_value=completed(0)) as run:
                with contextlib.redirect_stdout(stdout):
                    code = cli.run_setup(args, ROOT, input_fn=input_fn)

        self.assertEqual(code, 1)
        input_fn.assert_called_once_with("Proceed with router apply stages? [y/N]: ")
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            setup_local_commands(profiles, generated, include_wizard=False),
        )
        self.assertIn("Cancelled before router apply.", stdout.getvalue())
        self.assertIn("no router apply stages were started", stdout.getvalue())

    def test_apply_confirmation_runs_full_order_without_duplicate_plan(self):
        stdout = io.StringIO()
        input_fn = mock.Mock(return_value="yes")
        with tempfile.TemporaryDirectory() as tmp:
            profiles_path = Path(tmp) / "profiles.json"
            profiles_path.write_text("{}", encoding="utf-8")
            profiles = str(profiles_path)
            generated = str(Path(tmp) / "generated")
            args = cli.parse_args(
                ["setup", "--profiles", profiles, "--generated", generated, "--apply"]
            )
            with mock.patch.object(cli.subprocess, "run", return_value=completed(0)) as run:
                with contextlib.redirect_stdout(stdout):
                    code = cli.run_setup(args, ROOT, input_fn=input_fn)

        expected = setup_local_commands(profiles, generated, include_wizard=False) + setup_apply_commands(generated)
        self.assertEqual(code, 0)
        self.assertEqual([call.args[0] for call in run.call_args_list], expected)
        self.assertEqual(sum("routerkit-plan.py" in command[1] for command in expected), 1)
        self.assertIn("Setup apply completed.", stdout.getvalue())

    def test_apply_yes_skips_confirmation_but_runs_all_safety_steps(self):
        input_fn = mock.Mock(side_effect=AssertionError("confirmation must not be requested"))
        with tempfile.TemporaryDirectory() as tmp:
            profiles_path = Path(tmp) / "profiles.json"
            profiles_path.write_text("{}", encoding="utf-8")
            profiles = str(profiles_path)
            generated = str(Path(tmp) / "generated")
            args = cli.parse_args(
                ["setup", "--profiles", profiles, "--generated", generated, "--apply", "--yes"]
            )
            with mock.patch.object(cli.subprocess, "run", return_value=completed(0)) as run:
                code = cli.run_setup(args, ROOT, input_fn=input_fn)

        self.assertEqual(code, 0)
        input_fn.assert_not_called()
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            setup_local_commands(profiles, generated, include_wizard=False) + setup_apply_commands(generated),
        )

    def test_yes_without_apply_returns_two_without_execution(self):
        stderr = io.StringIO()
        with mock.patch.object(cli.subprocess, "run") as run:
            with contextlib.redirect_stderr(stderr):
                code = cli.main(["--repo-root", str(ROOT), "setup", "--yes"])

        self.assertEqual(code, 2)
        run.assert_not_called()
        self.assertIn("setup --yes requires --apply", stderr.getvalue())

    def test_apply_rejects_custom_target_root_without_execution(self):
        stderr = io.StringIO()
        with mock.patch.object(cli.subprocess, "run") as run:
            with contextlib.redirect_stderr(stderr):
                code = cli.main(
                    [
                        "--repo-root",
                        str(ROOT),
                        "setup",
                        "--target-root",
                        "/tmp/routerkit-target",
                        "--apply",
                    ]
                )

        self.assertEqual(code, 2)
        run.assert_not_called()
        self.assertIn("setup --apply currently supports only --target-root /opt", stderr.getvalue())

    def test_plan_only_allows_custom_target_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles = str(Path(tmp) / "profiles.json")
            generated = str(Path(tmp) / "generated")
            with mock.patch.object(cli.subprocess, "run", return_value=completed(0)) as run:
                code = cli.main(
                    [
                        "--repo-root",
                        str(ROOT),
                        "setup",
                        "--profiles",
                        profiles,
                        "--generated",
                        generated,
                        "--target-root",
                        "/tmp/routerkit-target",
                    ]
                )

        self.assertEqual(code, 0)
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            setup_local_commands(profiles, generated, target_root="/tmp/routerkit-target"),
        )

    def test_dry_run_missing_profiles_prints_pipeline_without_side_effects(self):
        for dry_run_position in ("global", "subcommand"):
            with self.subTest(dry_run_position=dry_run_position):
                stdout = io.StringIO()
                with tempfile.TemporaryDirectory() as tmp:
                    profiles_path = Path(tmp) / "profiles.json"
                    generated_path = Path(tmp) / "generated"
                    base = [
                        "--repo-root",
                        str(ROOT),
                        "setup",
                        "--profiles",
                        str(profiles_path),
                        "--generated",
                        str(generated_path),
                        "--apply",
                    ]
                    if dry_run_position == "global":
                        argv = ["--repo-root", str(ROOT), "--dry-run"] + base[2:]
                    else:
                        argv = base + ["--dry-run"]
                    with mock.patch.object(cli.subprocess, "run") as run:
                        with mock.patch.object(
                            cli,
                            "confirm_setup_apply",
                            side_effect=AssertionError("input must not be requested"),
                        ):
                            with contextlib.redirect_stdout(stdout):
                                code = cli.main(argv)

                    self.assertEqual(code, 0)
                    run.assert_not_called()
                    self.assertFalse(profiles_path.exists())
                    self.assertFalse(generated_path.exists())
                    output = stdout.getvalue()
                    self.assertIn("Would run setup pipeline:", output)
                    self.assertIn("routerkit-wizard.py", output)
                    self.assertIn("3. python3 scripts/routerkit-plan.py", output)
                    self.assertIn("4. confirmation gate", output)
                    self.assertIn("8. sh scripts/healthcheck.sh", output)

    def test_dry_run_existing_profiles_reuses_without_wizard_or_secret_output(self):
        synthetic_secret = "SYNTHETIC_PRIVATE_VALUE_456"
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            profiles_path = Path(tmp) / "profiles.json"
            profiles_path.write_text(synthetic_secret, encoding="utf-8")
            generated_path = Path(tmp) / "generated"
            with mock.patch.object(cli.subprocess, "run") as run:
                with contextlib.redirect_stdout(stdout):
                    code = cli.main(
                        [
                            "--repo-root",
                            str(ROOT),
                            "setup",
                            "--profiles",
                            str(profiles_path),
                            "--generated",
                            str(generated_path),
                            "--dry-run",
                        ]
                    )

        self.assertEqual(code, 0)
        run.assert_not_called()
        self.assertFalse(generated_path.exists())
        output = stdout.getvalue()
        self.assertIn(f"Reusing existing profiles file: {profiles_path}", output)
        self.assertNotIn("routerkit-wizard.py", output)
        self.assertNotIn(synthetic_secret, output)

    def test_default_apply_dry_run_renders_expected_pipeline(self):
        stdout = io.StringIO()
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                with mock.patch.object(cli.subprocess, "run") as run:
                    with contextlib.redirect_stdout(stdout):
                        code = cli.main(
                            ["--repo-root", str(ROOT), "--dry-run", "setup", "--apply"]
                        )
            finally:
                os.chdir(original_cwd)

        self.assertEqual(code, 0)
        run.assert_not_called()
        self.assertEqual(
            stdout.getvalue(),
            "\n".join(
                [
                    "Would run setup pipeline:",
                    "1. python3 scripts/routerkit-wizard.py --profiles profiles.json --no-generator-prompt",
                    "2. python3 scripts/generate-xray-profiles.py --profiles profiles.json --out generated",
                    "3. python3 scripts/routerkit-plan.py --generated generated --target-root /opt --strict",
                    "4. confirmation gate",
                    "5. sh scripts/preflight.sh",
                    "6. sh scripts/backup.sh",
                    "7. sh scripts/install-xray-direct.sh generated",
                    "8. sh scripts/healthcheck.sh",
                ]
            )
            + "\n",
        )

    def test_wizard_failure_stops_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles = str(Path(tmp) / "profiles.json")
            generated = str(Path(tmp) / "generated")
            with mock.patch.object(cli.subprocess, "run", return_value=completed(17)) as run:
                code = cli.main(
                    [
                        "--repo-root",
                        str(ROOT),
                        "setup",
                        "--profiles",
                        profiles,
                        "--generated",
                        generated,
                    ]
                )

        self.assertEqual(code, 17)
        self.assertEqual([call.args[0] for call in run.call_args_list], setup_local_commands(profiles, generated)[:1])

    def test_generator_failure_stops_before_plan_and_apply(self):
        input_fn = mock.Mock(side_effect=AssertionError("input must not be requested"))
        with tempfile.TemporaryDirectory() as tmp:
            profiles_path = Path(tmp) / "profiles.json"
            profiles_path.write_text("{}", encoding="utf-8")
            profiles = str(profiles_path)
            generated = str(Path(tmp) / "generated")
            args = cli.parse_args(
                ["setup", "--profiles", profiles, "--generated", generated, "--apply"]
            )
            with mock.patch.object(cli.subprocess, "run", return_value=completed(18)) as run:
                code = cli.run_setup(args, ROOT, input_fn=input_fn)

        self.assertEqual(code, 18)
        input_fn.assert_not_called()
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            setup_local_commands(profiles, generated, include_wizard=False)[:1],
        )

    def test_plan_failure_stops_before_confirmation_and_apply(self):
        input_fn = mock.Mock(side_effect=AssertionError("input must not be requested"))
        with tempfile.TemporaryDirectory() as tmp:
            profiles_path = Path(tmp) / "profiles.json"
            profiles_path.write_text("{}", encoding="utf-8")
            profiles = str(profiles_path)
            generated = str(Path(tmp) / "generated")
            args = cli.parse_args(
                ["setup", "--profiles", profiles, "--generated", generated, "--apply"]
            )
            with mock.patch.object(
                cli.subprocess,
                "run",
                side_effect=[completed(0), completed(19)],
            ) as run:
                code = cli.run_setup(args, ROOT, input_fn=input_fn)

        self.assertEqual(code, 19)
        input_fn.assert_not_called()
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            setup_local_commands(profiles, generated, include_wizard=False),
        )

    def test_successful_apply_summary_states_all_non_actions_without_secrets(self):
        synthetic_secret = "SYNTHETIC_PRIVATE_VALUE_789"
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            profiles_path = Path(tmp) / "profiles.json"
            profiles_path.write_text(synthetic_secret, encoding="utf-8")
            args = cli.parse_args(
                [
                    "setup",
                    "--profiles",
                    str(profiles_path),
                    "--generated",
                    str(Path(tmp) / "generated"),
                    "--apply",
                    "--yes",
                ]
            )
            with mock.patch.object(cli.subprocess, "run", return_value=completed(0)):
                with contextlib.redirect_stdout(stdout):
                    code = cli.run_setup(args, ROOT)

        self.assertEqual(code, 0)
        output = stdout.getvalue()
        for expected in (
            "Setup apply completed.",
            "Autostart was not enabled.",
            "Netcraze proxy connections and policies were not changed.",
            "Firewall rules were not changed.",
            "xkeen -start was not called.",
        ):
            self.assertIn(expected, output)
        self.assertNotIn(synthetic_secret, output)


class RouterkitCliParseTests(unittest.TestCase):
    def test_unknown_command_fails_via_argparse(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.parse_args(["unknown"])

    def test_missing_required_generate_args_fails_via_argparse(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.parse_args(["generate", "--profiles", "profiles.json"])


if __name__ == "__main__":
    unittest.main()
