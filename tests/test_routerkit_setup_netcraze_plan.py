import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
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

    def _run_fixture_setup(self, state_name):
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
        args = cli.parse_args(
            [
                "setup",
                "--generated",
                str(generated),
                "--plan-netcraze",
                "--netcraze-state-file",
                str(state),
            ]
        )

        first_step = [True]

        def successful_steps(_steps, **_kwargs):
            if first_step[0]:
                first_step[0] = False
                workspace.profiles_path.write_text('{"profiles": []}\n', encoding="utf-8")
                os.chmod(workspace.profiles_path, 0o600)
            return 0

        output = io.StringIO()
        errors = io.StringIO()
        with mock.patch.object(cli, "create_setup_workspace", return_value=workspace):
            with mock.patch.object(cli, "run_steps", side_effect=successful_steps):
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    code = cli.run_setup(args, ROOT)
        temporary.cleanup()
        return code, output.getvalue(), errors.getvalue()

    def test_successful_offline_plan_reports_no_write(self):
        code, output, errors = self._run_fixture_setup("empty-clean-state.json")
        self.assertEqual(code, 0, errors)
        self.assertIn("Offline Netcraze connection/policy plan completed", output)
        self.assertIn("No router policy, connection, or device-assignment write occurred", output)

    def test_conflict_stops_before_confirmation(self):
        code, output, errors = self._run_fixture_setup("connection-name-conflict.json")
        self.assertEqual(code, 2)
        self.assertIn("same-name connection", output)
        self.assertIn("stopping before confirmation", errors)


if __name__ == "__main__":
    unittest.main()
