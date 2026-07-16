import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures" / "netcraze"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import routerkit as cli


class SetupNetcrazePlanTests(unittest.TestCase):
    def test_default_setup_path_is_unchanged(self):
        args = cli.parse_args(["setup", "--dry-run"])
        rendered = cli.render_setup_pipeline(args)
        self.assertFalse(args.plan_netcraze)
        self.assertNotIn("Netcraze connection/policy plan", rendered)

    def test_flags_require_each_other(self):
        with self.assertRaises(cli.RouterkitCliError):
            cli.validate_setup_args(cli.parse_args(["setup", "--plan-netcraze"]))
        with self.assertRaises(cli.RouterkitCliError):
            cli.validate_setup_args(
                cli.parse_args(["setup", "--netcraze-state-file", "state.json"])
            )

    def test_dry_run_renders_stage_after_optional_selection_without_reads_or_children(self):
        args = cli.parse_args(
            [
                "setup",
                "--dry-run",
                "--discover-devices",
                "--device-inventory-file",
                "/missing/devices.json",
                "--plan-netcraze",
                "--netcraze-state-file",
                "/missing/state.json",
            ]
        )
        with mock.patch.object(cli, "load_router_state_snapshot", side_effect=AssertionError("read")):
            with mock.patch.object(cli, "load_local_endpoint_manifest", side_effect=AssertionError("read")):
                with mock.patch.object(cli.subprocess, "run", side_effect=AssertionError("child")):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(cli.run_setup(args, ROOT), 0)
        rendered = output.getvalue()
        self.assertLess(rendered.index("device discovery/selection"), rendered.index("Netcraze connection/policy plan"))

    def _run_fixture_setup(
        self,
        state_name,
        *,
        extra_args=(),
        answer="yes",
        selected_device=None,
    ):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        workspace = cli.SetupSecretWorkspace(root / "workspace", root / "workspace" / "profiles.json")
        workspace.directory.mkdir(mode=0o700)
        generated = root / "generated"
        generated.mkdir()
        manifest = generated / "routerkit-local-endpoints.json"
        manifest.write_text((FIXTURES / "local-endpoints.json").read_text(), encoding="utf-8")
        os.chmod(manifest, 0o600)
        state = root / "state.json"
        state.write_text((FIXTURES / state_name).read_text(), encoding="utf-8")
        os.chmod(state, 0o600)
        argv = [
            "setup",
            "--generated",
            str(generated),
            "--plan-netcraze",
            "--netcraze-state-file",
            str(state),
        ]
        argv.extend(extra_args)
        args = cli.parse_args(argv)

        first_step = [True]

        def successful_steps(_steps, **_kwargs):
            if first_step[0]:
                first_step[0] = False
                workspace.profiles_path.write_text('{"profiles": []}\n', encoding="utf-8")
                os.chmod(workspace.profiles_path, 0o600)
            return 0

        output = io.StringIO()
        errors = io.StringIO()
        prompts = []

        def input_value(prompt):
            prompts.append(prompt)
            return answer

        with mock.patch.object(cli, "create_setup_workspace", return_value=workspace):
            with mock.patch.object(cli, "run_steps", side_effect=successful_steps):
                with mock.patch.object(
                    cli,
                    "run_setup_selection_stage",
                    return_value=(0, selected_device),
                ):
                    with mock.patch.object(
                        cli,
                        "run_setup_bootstrap_apply",
                        return_value=cli.SetupBootstrapResult(0),
                    ):
                        with mock.patch.object(
                            cli,
                            "run_setup_autostart_apply",
                            return_value=cli.SetupBootstrapResult(0),
                        ):
                            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                                code = cli.run_setup(args, ROOT, input_fn=input_value)
        temporary.cleanup()
        return code, output.getvalue(), errors.getvalue(), prompts

    def test_successful_offline_plan_reports_no_write(self):
        code, output, errors, prompts = self._run_fixture_setup("empty-clean-state.json")
        self.assertEqual(code, 0, errors)
        self.assertEqual(prompts, [])
        self.assertIn("Offline Netcraze connection/policy plan completed", output)
        self.assertIn("No router policy, connection, or device-assignment write occurred", output)

    def test_conflict_stops_before_confirmation(self):
        code, output, errors, prompts = self._run_fixture_setup("connection-name-conflict.json")
        self.assertEqual(code, 2)
        self.assertEqual(prompts, [])
        self.assertIn("same-name connection", output)
        self.assertIn("stopping before confirmation", errors)

    def test_combined_apply_confirmation_explicitly_excludes_netcraze(self):
        code, output, errors, prompts = self._run_fixture_setup(
            "empty-clean-state.json", extra_args=("--apply",)
        )
        self.assertEqual(code, 0, errors)
        self.assertEqual(
            prompts, ["Netcraze actions excluded. Proceed with router apply stages? [y/N]: "]
        )
        self.assertIn("Netcraze plan boundary:", output)
        self.assertIn("OFFLINE PREVIEW ONLY", output)
        self.assertIn("This confirmation will NOT create or change Netcraze", output)
        self.assertIn("Netcraze actions were excluded from this RouterKit apply.", output)

    def test_yes_path_prints_exclusion_immediately_before_apply(self):
        code, output, errors, prompts = self._run_fixture_setup(
            "empty-clean-state.json", extra_args=("--apply", "--yes")
        )
        self.assertEqual(code, 0, errors)
        self.assertEqual(prompts, [])
        self.assertIn("Netcraze plan boundary:", output)
        self.assertIn("Apply covers only the existing RouterKit", output)

    def test_exclusion_survives_bootstrap_autostart_and_device_selection(self):
        code, output, errors, prompts = self._run_fixture_setup(
            "empty-clean-state.json",
            extra_args=(
                "--apply",
                "--yes",
                "--bootstrap-apply",
                "--enable-autostart",
                "--discover-devices",
                "--device-inventory-file",
                "/synthetic/devices.json",
                "--device-choice",
                "0",
            ),
        )
        self.assertEqual(code, 0, errors)
        self.assertEqual(prompts, [])
        self.assertIn("Bootstrap apply requested:", output)
        self.assertIn("Autostart enable requested:", output)
        self.assertIn("Netcraze plan boundary:", output)
        self.assertIn("no selected device", output)

    def test_cancellation_states_all_boundaries(self):
        code, output, _errors, prompts = self._run_fixture_setup(
            "empty-clean-state.json", extra_args=("--apply",), answer="no"
        )
        self.assertEqual(code, 1)
        self.assertTrue(prompts)
        self.assertIn("no RouterKit apply stages were started", output)
        self.assertIn("The Netcraze plan was preview-only.", output)
        self.assertIn("No Netcraze write was possible.", output)

    def test_malformed_state_stops_before_confirmation(self):
        code, _output, errors, prompts = self._run_fixture_setup(
            "malformed-objects.json", extra_args=("--apply",)
        )
        self.assertEqual(code, 2)
        self.assertEqual(prompts, [])
        self.assertIn("Netcraze offline plan failed", errors)

    def test_invalid_selected_device_handoff_stops_before_plan_and_confirmation(self):
        selection = SimpleNamespace(
            selected=True,
            device=SimpleNamespace(
                selectable=True,
                stable_identifier_type="mac",
                stable_identifier="00:00:00:00:00:00",
                display_name="PRIVATE_INVALID_DEVICE",
            ),
        )
        code, output, errors, prompts = self._run_fixture_setup(
            "empty-clean-state.json",
            extra_args=(
                "--apply",
                "--discover-devices",
                "--device-inventory-file",
                "/synthetic/devices.json",
                "--device-choice",
                "1",
            ),
            selected_device=selection,
        )
        self.assertEqual(code, 2)
        self.assertEqual(prompts, [])
        self.assertNotIn("assign_device", output)
        self.assertNotIn("00:00:00:00:00:00", errors)
        self.assertNotIn("PRIVATE_INVALID_DEVICE", errors)
        self.assertIn("Selected device reference is invalid.", errors)


if __name__ == "__main__":
    unittest.main()
