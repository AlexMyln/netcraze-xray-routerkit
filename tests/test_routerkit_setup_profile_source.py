import contextlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import routerkit_private_io as private_io


def load_cli():
    path = SCRIPTS / "routerkit.py"
    name = "routerkit_setup_integration_cli"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cli = load_cli()


def completed(returncode, stdout=None, stderr=None):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def synthetic_link(label="Synthetic", key_character="A", short_id="00"):
    scheme = "vl" + "ess"
    user_id = str(uuid.UUID(int=12345))
    query = "&".join(
        [
            "security=reality",
            "type=tcp",
            "fp=chrome",
            ("pb" + "k") + "=" + key_character * 43,
            ("si" + "d") + "=" + short_id,
            "sni=example.net",
            "flow=xtls-rprx-vision",
        ]
    )
    return f"{scheme}://{user_id}@example.net:443?{query}#{label}"


def profiles_document(link=None):
    return {
        "profiles": [
            {
                "name": "primary",
                "port": 1082,
                "vless": link or synthetic_link(),
                "select": {
                    "index": 0,
                    "require_security": "reality",
                    "require_network": "tcp",
                },
            }
        ]
    }


def private_file(path, text):
    path.write_text(text, encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    return path


def workspace_under(root):
    directory = root / "workspace"
    directory.mkdir(mode=0o700)
    if os.name == "posix":
        directory.chmod(0o700)
    return cli.SetupSecretWorkspace(directory, directory / "profiles.json")


class SetupArgumentModeTests(unittest.TestCase):
    def test_default_source_mode_and_hidden_input(self):
        args = cli.parse_args(["setup"])
        cli.validate_setup_args(args)
        self.assertEqual(cli.setup_profile_mode(args), "source")
        self.assertIsNone(args.source_env)
        self.assertIsNone(args.source_file)

    def test_source_environment_and_file_modes(self):
        for option, value in (("--source-env", "SAFE_ENV"), ("--source-file", "/private/source")):
            with self.subTest(option=option):
                args = cli.parse_args(["setup", option, value])
                cli.validate_setup_args(args)
                self.assertEqual(cli.setup_profile_mode(args), "source")

    def test_primary_with_zero_one_or_two_fallbacks(self):
        forms = (
            ["--primary-index", "1"],
            ["--primary-index", "1", "--fallback-index", "2"],
            [
                "--primary-index",
                "1",
                "--fallback-index",
                "2",
                "--fallback-index",
                "3",
            ],
        )
        for form in forms:
            with self.subTest(form=form):
                cli.validate_setup_args(cli.parse_args(["setup"] + form))

    def test_invalid_index_combinations_are_rejected(self):
        forms = (
            ["--fallback-index", "2"],
            ["--primary-index", "1", "--fallback-index", "1"],
            [
                "--primary-index",
                "1",
                "--fallback-index",
                "2",
                "--fallback-index",
                "3",
                "--fallback-index",
                "4",
            ],
        )
        for form in forms:
            with self.subTest(form=form), self.assertRaises(cli.RouterkitCliError):
                cli.validate_setup_args(cli.parse_args(["setup"] + form))

    def test_explicit_reuse_and_deprecated_alias_select_reuse(self):
        for option in ("--reuse-profiles", "--profiles"):
            with self.subTest(option=option):
                args = cli.parse_args(["setup", option, "/private/profiles"])
                cli.validate_setup_args(args)
                self.assertEqual(cli.setup_profile_mode(args), "reuse")

    def test_legacy_and_deprecated_alias_select_legacy(self):
        for option in ("--legacy-wizard", "--force-wizard"):
            with self.subTest(option=option):
                args = cli.parse_args(["setup", option])
                cli.validate_setup_args(args)
                self.assertEqual(cli.setup_profile_mode(args), "legacy")

    def test_mode_conflicts_are_rejected(self):
        forms = (
            ["--reuse-profiles", "a", "--source-env", "SAFE_ENV"],
            ["--reuse-profiles", "a", "--primary-index", "1"],
            ["--reuse-profiles", "a", "--legacy-wizard"],
            ["--legacy-wizard", "--source-file", "a"],
            ["--legacy-wizard", "--primary-index", "1"],
            ["--profiles", "a", "--reuse-profiles", "b"],
            ["--force-wizard", "--legacy-wizard"],
        )
        for form in forms:
            with self.subTest(form=form), self.assertRaises(cli.RouterkitCliError):
                cli.validate_setup_args(cli.parse_args(["setup"] + form))

    def test_yes_and_apply_target_restrictions_remain(self):
        for form in (
            ["--yes"],
            ["--apply", "--target-root", "/tmp/not-opt"],
        ):
            with self.subTest(form=form), self.assertRaises(cli.RouterkitCliError):
                cli.validate_setup_args(cli.parse_args(["setup"] + form))

    def test_invalid_combination_fails_before_workspace_or_execution(self):
        stderr = io.StringIO()
        with mock.patch.object(cli, "create_setup_workspace", side_effect=AssertionError("no workspace")):
            with mock.patch.object(cli.subprocess, "run", side_effect=AssertionError("no subprocess")):
                with contextlib.redirect_stderr(stderr):
                    code = cli.main(["setup", "--reuse-profiles", "private", "--primary-index", "1"])
        self.assertEqual(code, 2)
        self.assertIn("conflicts", stderr.getvalue())


class SetupDelegationTests(unittest.TestCase):
    def test_profile_source_command_uses_python_and_internal_yes(self):
        args = cli.parse_args(
            [
                "setup",
                "--source-env",
                "SAFE_ENV",
                "--primary-index",
                "1",
                "--fallback-index",
                "2",
                "--fallback-index",
                "3",
            ]
        )
        output = Path("/private/workspace/profiles.json")
        step = cli.build_profile_source_step(args, ROOT, output)
        self.assertEqual(
            step.command,
            [
                sys.executable,
                str(SCRIPTS / "routerkit-profile-source.py"),
                "--output",
                str(output),
                "--yes",
                "--source-env",
                "SAFE_ENV",
                "--primary-index",
                "1",
                "--fallback-index",
                "2",
                "--fallback-index",
                "3",
            ],
        )

    def test_setup_yes_is_not_forwarded_as_source_semantics(self):
        args = cli.parse_args(["setup", "--apply", "--yes"])
        step = cli.build_profile_source_step(args, ROOT, Path("/private/profiles.json"))
        self.assertEqual(step.command.count("--yes"), 1)

    def test_profile_source_output_is_visible_and_generator_is_suppressed(self):
        source = cli.CommandStep("profile source", ["source"])
        generator = cli.CommandStep("generator", ["generator"], suppress_output=True)
        with mock.patch.object(cli.subprocess, "run", return_value=completed(0)) as run:
            cli.run_steps([source, generator])
        self.assertNotIn("stdout", run.call_args_list[0].kwargs)
        self.assertIs(run.call_args_list[1].kwargs["stdout"], cli.subprocess.PIPE)

    def test_profile_source_failure_and_oserror_preserve_codes(self):
        step = cli.CommandStep("profile source", ["source"])
        with mock.patch.object(cli.subprocess, "run", return_value=completed(29)):
            self.assertEqual(cli.run_steps([step]), 29)
        with mock.patch.object(cli.subprocess, "run", side_effect=OSError("unavailable")):
            self.assertEqual(cli.run_steps([step]), 127)


class SetupPrivateWorkspaceTests(unittest.TestCase):
    def test_workspace_is_unique_private_and_cleanup_removes_it(self):
        first = cli.create_setup_workspace()
        second = cli.create_setup_workspace()
        try:
            self.assertNotEqual(first.directory, second.directory)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(first.directory.stat().st_mode), 0o700)
            private_file(first.profiles_path, "{}")
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(first.profiles_path.stat().st_mode), 0o600)
        finally:
            first.cleanup()
            second.cleanup()
        self.assertFalse(first.directory.exists())
        self.assertFalse(second.directory.exists())

    def test_secure_reuse_copy_preserves_original_and_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = private_file(root / "input.json", '{"profiles": []}\n')
            workspace = workspace_under(root)
            cli.secure_copy_reuse_profiles(original, workspace.profiles_path)
            self.assertEqual(workspace.profiles_path.read_text(encoding="utf-8"), original.read_text(encoding="utf-8"))
            self.assertTrue(original.exists())
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(workspace.profiles_path.stat().st_mode), 0o600)
            workspace.cleanup()
            self.assertTrue(original.exists())

    def test_reuse_rejects_symlink_directory_permissions_size_and_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = private_file(root / "valid.json", "{}")
            symlink = root / "link.json"
            symlink.symlink_to(valid)
            bad_encoding = root / "encoding.json"
            bad_encoding.write_bytes(b"\xff")
            bad_encoding.chmod(0o600)
            oversized = private_file(root / "oversized.json", "12345")
            candidates = [(symlink, cli.MAX_REUSE_PROFILES_BYTES), (root, cli.MAX_REUSE_PROFILES_BYTES), (bad_encoding, cli.MAX_REUSE_PROFILES_BYTES), (oversized, 4)]
            if os.name == "posix":
                public = private_file(root / "public.json", "{}")
                public.chmod(0o644)
                candidates.append((public, cli.MAX_REUSE_PROFILES_BYTES))
            for candidate, maximum in candidates:
                with self.subTest(candidate=candidate.name), mock.patch.object(cli, "MAX_REUSE_PROFILES_BYTES", maximum):
                    with self.assertRaises(cli.PrivateFileError):
                        cli.secure_copy_reuse_profiles(candidate, root / (candidate.name + ".copy"))

    def test_reuse_rejects_path_descriptor_identity_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = private_file(root / "profiles.json", "{}")
            with mock.patch.object(private_io, "_same_identity", return_value=False):
                with self.assertRaises(private_io.PrivateFileError):
                    cli.secure_copy_reuse_profiles(source, root / "copy.json")

    def test_generator_receives_private_copy_not_original_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = private_file(root / "operator.json", json.dumps(profiles_document()))
            workspace = workspace_under(root)
            args = cli.parse_args(["setup", "--reuse-profiles", str(original), "--generated", str(root / "generated")])

            def runner(command, **kwargs):
                if "generate-xray-profiles.py" in command[1]:
                    self.assertEqual(command[command.index("--profiles") + 1], str(workspace.profiles_path))
                    self.assertNotIn(str(original), command)
                    return completed(0, "secret", "secret")
                self.assertFalse(workspace.directory.exists())
                return completed(0)

            with mock.patch.object(cli, "create_setup_workspace", return_value=workspace):
                with mock.patch.object(cli.subprocess, "run", side_effect=runner):
                    self.assertEqual(cli.run_setup(args, ROOT), 0)
            self.assertTrue(original.exists())
            self.assertFalse(workspace.directory.exists())

    def test_source_failure_cleans_workspace_and_stops_later_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = workspace_under(Path(directory))
            args = cli.parse_args(["setup", "--primary-index", "1"])
            with mock.patch.object(cli, "create_setup_workspace", return_value=workspace):
                with mock.patch.object(cli.subprocess, "run", return_value=completed(27)) as run:
                    self.assertEqual(cli.run_setup(args, ROOT), 27)
            self.assertEqual(len(run.call_args_list), 1)
            self.assertFalse(workspace.directory.exists())

    def test_source_oserror_returns_127_and_cleans_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = workspace_under(Path(directory))
            args = cli.parse_args(["setup", "--primary-index", "1"])
            with mock.patch.object(cli, "create_setup_workspace", return_value=workspace):
                with mock.patch.object(cli.subprocess, "run", side_effect=OSError("PRIVATE_MARKER")):
                    self.assertEqual(cli.run_setup(args, ROOT), 127)
            self.assertFalse(workspace.directory.exists())

    def test_generator_failure_cleans_workspace_and_preserves_code(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = workspace_under(Path(directory))
            args = cli.parse_args(["setup", "--primary-index", "1"])

            def runner(command, **kwargs):
                if "routerkit-profile-source.py" in command[1]:
                    private_file(workspace.profiles_path, json.dumps(profiles_document()))
                    return completed(0)
                return completed(31, "secret", "secret")

            with mock.patch.object(cli, "create_setup_workspace", return_value=workspace):
                with mock.patch.object(cli.subprocess, "run", side_effect=runner) as run:
                    self.assertEqual(cli.run_setup(args, ROOT), 31)
            self.assertEqual(len(run.call_args_list), 2)
            self.assertFalse(workspace.directory.exists())

    def test_cleanup_failure_blocks_plan_and_preserves_earlier_failure(self):
        for child_code, expected in ((0, 1), (37, 37)):
            with self.subTest(child_code=child_code), tempfile.TemporaryDirectory() as directory:
                workspace = workspace_under(Path(directory))
                args = cli.parse_args(["setup", "--primary-index", "1"])

                def runner(command, **kwargs):
                    if "routerkit-profile-source.py" in command[1]:
                        private_file(workspace.profiles_path, json.dumps(profiles_document()))
                        return completed(0)
                    return completed(child_code, "secret", "secret")

                with mock.patch.object(cli, "create_setup_workspace", return_value=workspace):
                    with mock.patch.object(workspace, "cleanup", side_effect=cli.SetupCleanupError):
                        with mock.patch.object(cli.subprocess, "run", side_effect=runner) as run:
                            self.assertEqual(cli.run_setup(args, ROOT), expected)
                self.assertEqual(len(run.call_args_list), 2)

    def test_legacy_wizard_uses_private_cwd_and_no_generator_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = workspace_under(Path(directory))
            step = cli.build_legacy_wizard_step(ROOT, workspace)
            self.assertEqual(step.cwd, workspace.directory)
            self.assertIn("--no-generator-prompt", step.command)
            self.assertEqual(step.command[step.command.index("--profiles") + 1], "profiles.json")
            self.assertNotIn(str(workspace.directory), step.command)
            workspace.cleanup()

    def test_legacy_wizard_failure_cleans_workspace_and_stops_generator(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = workspace_under(Path(directory))
            args = cli.parse_args(["setup", "--legacy-wizard"])
            with mock.patch.object(cli, "create_setup_workspace", return_value=workspace):
                with mock.patch.object(cli.subprocess, "run", return_value=completed(43)) as run:
                    self.assertEqual(cli.run_setup(args, ROOT), 43)
            self.assertEqual(len(run.call_args_list), 1)
            self.assertFalse(workspace.directory.exists())


class SetupDryRunTests(unittest.TestCase):
    def test_global_and_subcommand_dry_run_forms_are_side_effect_free(self):
        forms = (["--dry-run", "setup"], ["setup", "--dry-run"])
        for argv in forms:
            with self.subTest(argv=argv):
                with mock.patch.object(cli, "create_setup_workspace", side_effect=AssertionError("no workspace")):
                    with mock.patch.object(cli.subprocess, "run", side_effect=AssertionError("no subprocess")):
                        self.assertEqual(cli.main(argv), 0)

    def test_dry_run_has_no_reads_prompts_subprocess_workspace_or_paths(self):
        markers = ("PRIVATE_ENV_NAME", "/protected/source-marker", "/protected/reuse-marker")
        forms = (
            ["--source-env", markers[0]],
            ["--source-file", markers[1]],
            ["--reuse-profiles", markers[2]],
            ["--legacy-wizard"],
        )
        for form in forms:
            with self.subTest(form=form):
                stdout = io.StringIO()
                args = cli.parse_args(["setup"] + form + ["--dry-run"])
                with mock.patch.object(cli, "create_setup_workspace", side_effect=AssertionError("no workspace")):
                    with mock.patch.object(cli.subprocess, "run", side_effect=AssertionError("no subprocess")):
                        with mock.patch.object(Path, "lstat", side_effect=AssertionError("no stat")):
                            with mock.patch("builtins.input", side_effect=AssertionError("no prompt")):
                                with contextlib.redirect_stdout(stdout):
                                    self.assertEqual(cli.run_setup(args, ROOT), 0)
                output = stdout.getvalue()
                for marker in markers:
                    self.assertNotIn(marker, output)

    def test_source_reuse_and_legacy_render_abstract_stages(self):
        expected = (
            (["setup", "--dry-run"], "acquire profile source"),
            (["setup", "--reuse-profiles", "/private/input", "--dry-run"], "securely copy explicit private profiles file"),
            (["setup", "--legacy-wizard", "--dry-run"], "run legacy profiles wizard"),
        )
        for argv, marker in expected:
            with self.subTest(argv=argv):
                output = cli.render_setup_pipeline(cli.parse_args(argv))
                self.assertIn(marker, output)
                self.assertNotIn("routerkit-profile-source.py", output)

    def test_apply_rendering_confirmation_and_order(self):
        with_confirmation = cli.render_setup_pipeline(cli.parse_args(["setup", "--apply", "--dry-run"]))
        without_confirmation = cli.render_setup_pipeline(cli.parse_args(["setup", "--apply", "--yes", "--dry-run"]))
        self.assertIn("8. confirmation gate", with_confirmation)
        self.assertIn("9. preflight", with_confirmation)
        self.assertIn("12. healthcheck", with_confirmation)
        self.assertNotIn("confirmation gate", without_confirmation)
        self.assertIn("8. preflight", without_confirmation)
        self.assertIn("11. healthcheck", without_confirmation)

    def test_generated_and_target_options_are_rendered_without_source_path(self):
        output = cli.render_setup_pipeline(
            cli.parse_args(
                [
                    "setup",
                    "--source-file",
                    "/protected/source-marker",
                    "--generated",
                    "local-output",
                    "--target-root",
                    "/tmp/plan-root",
                    "--dry-run",
                ]
            )
        )
        self.assertIn("generated output: local-output", output)
        self.assertIn("target root: /tmp/plan-root", output)
        self.assertNotIn("/protected/source-marker", output)

    def test_accidental_current_profiles_file_does_not_select_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            private_file(Path(directory) / "profiles.json", "PRIVATE_MARKER")
            old_cwd = Path.cwd()
            try:
                os.chdir(directory)
                args = cli.parse_args(["setup", "--dry-run"])
                self.assertEqual(cli.setup_profile_mode(args), "source")
                self.assertNotIn("reuse", cli.render_setup_pipeline(args))
            finally:
                os.chdir(old_cwd)


class SetupExecutionOrderTests(unittest.TestCase):
    def _successful_runner(self, workspace, calls):
        def runner(command, **kwargs):
            calls.append(command)
            if "routerkit-profile-source.py" in command[1]:
                private_file(workspace.profiles_path, json.dumps(profiles_document()))
            elif "routerkit-plan.py" in command[1]:
                self.assertFalse(workspace.directory.exists())
            return completed(0, "PRIVATE_GENERATOR_MARKER", "PRIVATE_GENERATOR_MARKER")

        return runner

    def test_plan_only_order_summary_and_secret_suppression(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            workspace = workspace_under(Path(directory))
            args = cli.parse_args(["setup", "--primary-index", "1"])
            calls = []
            with mock.patch.object(cli, "create_setup_workspace", return_value=workspace):
                with mock.patch.object(cli.subprocess, "run", side_effect=self._successful_runner(workspace, calls)):
                    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                        self.assertEqual(cli.run_setup(args, ROOT), 0)
        self.assertEqual([Path(command[1]).name for command in calls], ["routerkit-profile-source.py", "generate-xray-profiles.py", "routerkit-plan.py"])
        output = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn("PRIVATE_GENERATOR_MARKER", output)
        self.assertIn("Private setup profiles were removed.", output)
        self.assertIn("No router apply steps were executed.", output)

    def test_plan_failure_stops_confirmation_and_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = workspace_under(Path(directory))
            args = cli.parse_args(["setup", "--primary-index", "1", "--apply"])
            input_fn = mock.Mock(side_effect=AssertionError("no confirmation"))

            def runner(command, **kwargs):
                if "routerkit-profile-source.py" in command[1]:
                    private_file(workspace.profiles_path, json.dumps(profiles_document()))
                    return completed(0)
                if "routerkit-plan.py" in command[1]:
                    return completed(41)
                return completed(0)

            with mock.patch.object(cli, "create_setup_workspace", return_value=workspace):
                with mock.patch.object(cli.subprocess, "run", side_effect=runner) as run:
                    self.assertEqual(cli.run_setup(args, ROOT, input_fn=input_fn), 41)
            input_fn.assert_not_called()
            self.assertEqual(len(run.call_args_list), 3)

    def test_apply_refusal_starts_no_router_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = workspace_under(Path(directory))
            args = cli.parse_args(["setup", "--primary-index", "1", "--apply"])
            calls = []
            with mock.patch.object(cli, "create_setup_workspace", return_value=workspace):
                with mock.patch.object(cli.subprocess, "run", side_effect=self._successful_runner(workspace, calls)):
                    self.assertEqual(cli.run_setup(args, ROOT, input_fn=lambda _prompt: ""), 1)
            self.assertEqual(len(calls), 3)

    def test_apply_yes_skips_confirmation_and_keeps_router_order(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = workspace_under(Path(directory))
            args = cli.parse_args(["setup", "--primary-index", "1", "--apply", "--yes"])
            calls = []
            with mock.patch.object(cli, "create_setup_workspace", return_value=workspace):
                with mock.patch.object(cli.subprocess, "run", side_effect=self._successful_runner(workspace, calls)):
                    self.assertEqual(cli.run_setup(args, ROOT, input_fn=lambda _prompt: (_ for _ in ()).throw(AssertionError)), 0)
            self.assertEqual(
                [Path(command[1]).name for command in calls],
                [
                    "routerkit-profile-source.py",
                    "generate-xray-profiles.py",
                    "routerkit-plan.py",
                    "preflight.sh",
                    "backup.sh",
                    "install-xray-direct.sh",
                    "healthcheck.sh",
                ],
            )


class SetupOfflineIntegrationTests(unittest.TestCase):
    def test_protected_source_runs_generation_and_plan_without_persistent_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = private_file(root / "source.txt", synthetic_link())
            generated = root / "generated"
            workspace_path = root / "private-workspace"

            def create_workspace():
                workspace_path.mkdir(mode=0o700)
                return cli.SetupSecretWorkspace(workspace_path, workspace_path / "profiles.json")

            args = cli.parse_args(
                ["setup", "--source-file", str(source), "--primary-index", "1", "--generated", str(generated)]
            )
            with mock.patch.object(cli, "create_setup_workspace", side_effect=create_workspace):
                self.assertEqual(cli.run_setup(args, ROOT), 0)
            self.assertEqual(sorted(path.name for path in generated.iterdir()), ["03_inbounds.json", "04_outbounds.json", "05_routing.json"])
            self.assertFalse(workspace_path.exists())
            self.assertFalse((root / "profiles.json").exists())

    def test_environment_source_and_indexes_work_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = root / "generated"
            payload = "\n".join((synthetic_link("One", "A", "00"), synthetic_link("Two", "B", "11")))
            args = cli.parse_args(
                ["setup", "--source-env", "ROUTERKIT_TEST_SOURCE", "--primary-index", "1", "--fallback-index", "2", "--generated", str(generated)]
            )
            with mock.patch.dict(os.environ, {"ROUTERKIT_TEST_SOURCE": payload}):
                self.assertEqual(cli.run_setup(args, ROOT), 0)
            self.assertTrue((generated / "03_inbounds.json").exists())

    def test_explicit_reuse_reaches_generator_through_private_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = private_file(root / "operator.json", json.dumps(profiles_document()))
            generated = root / "generated"
            args = cli.parse_args(["setup", "--reuse-profiles", str(original), "--generated", str(generated)])
            self.assertEqual(cli.run_setup(args, ROOT), 0)
            self.assertTrue(original.exists())
            self.assertTrue((generated / "05_routing.json").exists())


if __name__ == "__main__":
    unittest.main()
