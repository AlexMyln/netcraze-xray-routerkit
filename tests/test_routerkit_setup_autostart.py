import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_cli():
    path = SCRIPTS / "routerkit.py"
    spec = importlib.util.spec_from_file_location("routerkit_setup_autostart_cli", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cli = load_cli()


def workspace_under(root):
    directory = root / "workspace"
    directory.mkdir(mode=0o700)
    return cli.SetupSecretWorkspace(directory, directory / "profiles.json")


def write_profiles(path):
    path.write_text(json.dumps({"profiles": []}), encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)


class SetupAutostartIntegrationTests(unittest.TestCase):
    def _run(self, *, autostart_result=None, apply_result=0, answer="yes", yes=False):
        events = []
        stdout = io.StringIO()
        stderr = io.StringIO()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = workspace_under(Path(temporary.name))
        argv = ["setup", "--primary-index", "1", "--apply", "--enable-autostart"]
        if yes:
            argv.append("--yes")
        args = cli.parse_args(argv)

        def run_steps(steps, **_kwargs):
            events.extend(step.name for step in steps)
            if steps[0].name == "profile source":
                write_profiles(workspace.profiles_path)
            if steps[0].name == "strict plan":
                self.assertFalse(workspace.directory.exists())
            if steps[0].name == "preflight" and apply_result != 0:
                return apply_result
            return 0

        def confirm(prompt):
            events.append("confirmation")
            self.assertEqual(prompt, "Proceed with router apply and autostart stages? [y/N]: ")
            return answer

        def autostart(step):
            events.append(step.name)
            return autostart_result or cli.SetupBootstrapResult(0)

        with mock.patch.object(cli, "create_setup_workspace", return_value=workspace):
            with mock.patch.object(cli, "run_steps", side_effect=run_steps):
                with mock.patch.object(cli, "run_setup_autostart_apply", side_effect=autostart) as run_autostart:
                    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                        code = cli.run_setup(args, ROOT, input_fn=confirm)
        return code, events, stdout.getvalue(), stderr.getvalue(), run_autostart

    def test_option_requires_apply_before_side_effects(self):
        stderr = io.StringIO()
        with mock.patch.object(cli, "create_setup_workspace", side_effect=AssertionError("no workspace")):
            with contextlib.redirect_stderr(stderr):
                code = cli.main(["setup", "--enable-autostart"])
        self.assertEqual(code, 2)
        self.assertIn("setup --enable-autostart requires --apply", stderr.getvalue())

    def test_final_order_and_success_summary(self):
        code, events, stdout, _stderr, run_autostart = self._run()
        self.assertEqual(code, 0)
        self.assertEqual(
            events,
            [
                "profile source",
                "generator",
                "strict plan",
                "confirmation",
                "preflight",
                "backup",
                "install",
                "healthcheck",
                "autostart apply",
            ],
        )
        run_autostart.assert_called_once()
        self.assertIn("Autostart enable requested:", stdout)
        self.assertIn("Autostart enabled and restart-verified.", stdout)
        self.assertIn("No reboot was performed; reboot verification remains #16.", stdout)

    def test_refusal_starts_no_router_or_autostart_stage(self):
        code, events, stdout, _stderr, run_autostart = self._run(answer="no")
        self.assertEqual(code, 1)
        self.assertEqual(events, ["profile source", "generator", "strict plan", "confirmation"])
        run_autostart.assert_not_called()
        self.assertIn("Cancelled before router apply and autostart.", stdout)

    def test_healthcheck_failure_starts_no_autostart(self):
        code, events, stdout, _stderr, run_autostart = self._run(apply_result=31)
        self.assertEqual(code, 31)
        self.assertEqual(events[-1], "healthcheck")
        run_autostart.assert_not_called()
        self.assertNotIn("Setup apply completed.", stdout)

    def test_autostart_failure_blocks_success_summary(self):
        result = cli.SetupBootstrapResult(3, supervision_failed=True)
        code, events, stdout, stderr, _run_autostart = self._run(autostart_result=result)
        self.assertEqual(code, 3)
        self.assertEqual(events[-1], "autostart apply")
        self.assertNotIn("Setup apply completed.", stdout)
        self.assertIn("autostart supervision did not complete cleanly", stderr)
        self.assertIn("Safe disable command", stderr)

    def test_dry_run_adds_abstract_stage_after_healthcheck(self):
        args = cli.parse_args(["setup", "--apply", "--enable-autostart", "--dry-run"])
        output = cli.render_setup_pipeline(args)
        self.assertLess(output.index("healthcheck"), output.index("enable S23xray-direct autostart"))

    def test_install_apply_autostart_pipeline_and_summary(self):
        args = cli.parse_args(["install", "--apply", "--enable-autostart"])
        steps = cli.build_install_apply_steps(args, ROOT)
        self.assertEqual([step.name for step in steps][-2:], ["healthcheck", "autostart apply"])
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            cli.print_apply_summary(steps)
        self.assertIn("Autostart enabled and restart-verified.", stdout.getvalue())

    def test_install_autostart_failure_preserves_child_code_and_blocks_success_summary(self):
        args = cli.parse_args(["install", "--apply", "--enable-autostart"])
        steps = cli.build_install_apply_steps(args, ROOT)
        stdout = io.StringIO()
        stderr = io.StringIO()

        with mock.patch.object(cli, "run_steps", return_value=0):
            with mock.patch.object(
                cli,
                "run_setup_autostart_apply",
                return_value=cli.SetupBootstrapResult(3, supervision_failed=True),
            ):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = cli.run_install_apply_steps(steps)

        self.assertEqual(code, 3)
        self.assertNotIn("Apply summary:", stdout.getvalue())
        self.assertIn("autostart supervision did not complete cleanly", stderr.getvalue())
        self.assertIn("Safe disable command", stderr.getvalue())

    def test_install_autostart_conflicts_with_skip_healthcheck(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = cli.main(["install", "--apply", "--enable-autostart", "--skip-healthcheck"])
        self.assertEqual(code, 2)
        self.assertIn("conflicts with --skip-healthcheck", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
