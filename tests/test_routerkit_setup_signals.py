import contextlib
import importlib.util
import io
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SYNTHETIC_SECRET = "SYNTHETIC_SETUP_SECRET_MARKER"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_cli():
    path = SCRIPTS / "routerkit.py"
    name = "routerkit_setup_signal_cli"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cli = load_cli()


def _wait_for(path, timeout=6.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError("timed out waiting for synthetic process marker")


def _process_exists(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_remains(pid, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_exists(pid):
            return False
        time.sleep(0.02)
    return _process_exists(pid)


def _helper_main(stage, scenario, root, profiles):
    root = Path(root)
    profiles = Path(profiles)
    if stage == "source":
        (root / "source.pid").write_text(str(os.getpid()), encoding="ascii")
        if scenario == "source-block":
            time.sleep(60)
            return 0
        profiles.write_text('{"profiles": [{"secret": "' + SYNTHETIC_SECRET + '"}]}\n', encoding="utf-8")
        profiles.chmod(0o600)
        return 0

    if stage == "generator":
        if scenario == "ignore-term":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        descendant = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        (root / "descendant.pid").write_text(str(descendant.pid), encoding="ascii")
        (root / "generator.pid").write_text(str(os.getpid()), encoding="ascii")
        time.sleep(60)
        return 0

    if stage == "plan":
        (root / "plan-started").write_text("unexpected", encoding="ascii")
        return 0

    raise AssertionError(stage)


def _wrapper_main(scenario, root):
    root = Path(root)
    workspace_path = root / "workspace"
    workspace_path.mkdir(mode=0o700)
    workspace = cli.SetupSecretWorkspace(workspace_path, workspace_path / "profiles.json")
    real_cleanup = workspace.cleanup

    if scenario == "repeated-term":
        def delayed_cleanup():
            (root / "cleanup-started").write_text("yes", encoding="ascii")
            time.sleep(0.5)
            real_cleanup()
        workspace.cleanup = delayed_cleanup
    elif scenario == "cleanup-failure":
        def failed_cleanup():
            raise cli.SetupCleanupError("synthetic cleanup failure")
        workspace.cleanup = failed_cleanup

    def step(stage, name, *, suppress_output=False):
        return cli.CommandStep(
            name,
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--helper",
                stage,
                scenario,
                str(root),
                str(workspace.profiles_path),
            ],
            suppress_output=suppress_output,
        )

    args = cli.parse_args(["setup", "--primary-index", "1", "--generated", str(root / "generated")])
    def create_workspace():
        if scenario == "creation-gap":
            (root / "workspace-created").write_text("yes", encoding="ascii")
            time.sleep(0.5)
        return workspace

    with mock.patch.object(cli, "create_setup_workspace", side_effect=create_workspace):
        with mock.patch.object(
            cli,
            "build_profile_source_step",
            side_effect=lambda *_args, **_kwargs: step("source", "profile source"),
        ):
            with mock.patch.object(
                cli,
                "build_generator_step",
                side_effect=lambda *_args, **_kwargs: step(
                    "generator", "generator", suppress_output=True
                ),
            ):
                with mock.patch.object(
                    cli,
                    "build_strict_plan_step",
                    side_effect=lambda *_args, **_kwargs: step("plan", "strict plan"),
                ):
                    try:
                        return cli.run_setup(args, ROOT)
                    except KeyboardInterrupt:
                        return 130


@unittest.skipUnless(os.name == "posix", "process signal tests require POSIX")
class SetupSignalProcessTests(unittest.TestCase):
    def _run_scenario(self, scenario, signum, ready_name, *, repeat=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--wrapper", scenario, str(root)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                _wait_for(root / ready_name)
                process.send_signal(signum)
                if repeat:
                    _wait_for(root / "cleanup-started")
                    process.send_signal(signum)
                stdout, stderr = process.communicate(timeout=9)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=3)

            output = stdout + stderr
            child_pids = []
            for name in ("source.pid", "generator.pid", "descendant.pid"):
                path = root / name
                if path.exists():
                    child_pids.append(int(path.read_text(encoding="ascii")))
            return {
                "returncode": process.returncode,
                "output": output,
                "workspace_exists": (root / "workspace").exists(),
                "profiles_exists": (root / "workspace" / "profiles.json").exists(),
                "plan_started": (root / "plan-started").exists(),
                "children_alive": [pid for pid in child_pids if _process_remains(pid)],
            }

    def _assert_controlled_cleanup(self, result, expected_code):
        self.assertEqual(result["returncode"], expected_code)
        self.assertFalse(result["workspace_exists"])
        self.assertFalse(result["profiles_exists"])
        self.assertFalse(result["plan_started"])
        self.assertEqual(result["children_alive"], [])
        self.assertNotIn(SYNTHETIC_SECRET, result["output"])
        self.assertNotIn("Traceback", result["output"])
        self.assertNotIn("Setup plan completed", result["output"])

    def test_sigterm_during_source_before_profiles_publication(self):
        result = self._run_scenario("source-block", signal.SIGTERM, "source.pid")
        self._assert_controlled_cleanup(result, 128 + signal.SIGTERM)

    def test_sigterm_between_workspace_creation_and_handler_installation_is_deferred(self):
        result = self._run_scenario("creation-gap", signal.SIGTERM, "workspace-created")
        self._assert_controlled_cleanup(result, 128 + signal.SIGTERM)

    def test_sigterm_during_generator_removes_profiles_and_process_group(self):
        result = self._run_scenario("generator", signal.SIGTERM, "generator.pid")
        self._assert_controlled_cleanup(result, 128 + signal.SIGTERM)

    @unittest.skipUnless(hasattr(signal, "SIGHUP"), "SIGHUP is unavailable")
    def test_sighup_during_generator_removes_profiles_and_process_group(self):
        result = self._run_scenario("generator", signal.SIGHUP, "generator.pid")
        self._assert_controlled_cleanup(result, 128 + signal.SIGHUP)

    def test_sigint_keeps_interactive_cancellation_and_cleans_workspace(self):
        result = self._run_scenario("generator", signal.SIGINT, "generator.pid")
        self._assert_controlled_cleanup(result, 130)

    def test_repeated_sigterm_during_cleanup_is_deferred(self):
        result = self._run_scenario(
            "repeated-term", signal.SIGTERM, "generator.pid", repeat=True
        )
        self._assert_controlled_cleanup(result, 128 + signal.SIGTERM)

    def test_child_ignoring_sigterm_is_forcibly_stopped_and_reaped(self):
        started = time.monotonic()
        result = self._run_scenario("ignore-term", signal.SIGTERM, "generator.pid")
        elapsed = time.monotonic() - started
        self._assert_controlled_cleanup(result, 128 + signal.SIGTERM)
        self.assertLess(elapsed, 7.0)

    def test_cleanup_failure_after_sigterm_blocks_plan_and_is_secret_safe(self):
        result = self._run_scenario("cleanup-failure", signal.SIGTERM, "generator.pid")
        self.assertEqual(result["returncode"], 128 + signal.SIGTERM)
        self.assertTrue(result["workspace_exists"])
        self.assertTrue(result["profiles_exists"])
        self.assertFalse(result["plan_started"])
        self.assertEqual(result["children_alive"], [])
        self.assertIn("cleanup failed", result["output"])
        self.assertIn("Remove it manually:", result["output"])
        self.assertNotIn(SYNTHETIC_SECRET, result["output"])
        self.assertNotIn("Traceback", result["output"])


class SetupSignalUnitTests(unittest.TestCase):
    def _workspace(self, root):
        directory = root / "workspace"
        directory.mkdir(mode=0o700, parents=True)
        return cli.SetupSecretWorkspace(directory, directory / "profiles.json")

    def _run_with_fake_steps(self, workspace, results):
        calls = []

        def fake_run_steps(steps, **kwargs):
            calls.append((steps[0].name, kwargs.get("setup_lifecycle")))
            if steps[0].name == "profile source" and results[0] == 0:
                workspace.profiles_path.write_text('{"profiles": []}\n', encoding="utf-8")
                workspace.profiles_path.chmod(0o600)
            return results[len(calls) - 1]

        args = cli.parse_args(["setup", "--primary-index", "1"])
        with mock.patch.object(cli, "create_setup_workspace", return_value=workspace):
            with mock.patch.object(cli, "run_steps", side_effect=fake_run_steps):
                return cli.run_setup(args, ROOT), calls

    def test_handlers_restore_after_success_and_source_failure(self):
        handled = cli.SetupSignalLifecycle.handled_signals()
        previous = {signum: signal.getsignal(signum) for signum in handled}
        custom = lambda _signum, _frame: None
        try:
            for signum in handled:
                signal.signal(signum, custom)
            for results, expected in (([0, 0, 0], 0), ([29], 29)):
                with self.subTest(results=results), tempfile.TemporaryDirectory() as directory:
                    workspace = self._workspace(Path(directory))
                    code, calls = self._run_with_fake_steps(workspace, results)
                    self.assertEqual(code, expected)
                    self.assertTrue(calls)
                    for signum in handled:
                        self.assertIs(signal.getsignal(signum), custom)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)

    def test_handlers_restore_after_controlled_termination_and_state_is_not_reused(self):
        signum = signal.SIGTERM
        previous = signal.getsignal(signum)
        custom = lambda _signum, _frame: None
        try:
            signal.signal(signum, custom)
            with tempfile.TemporaryDirectory() as directory:
                first = self._workspace(Path(directory) / "first")
                args = cli.parse_args(["setup", "--primary-index", "1"])

                def terminate(_steps, **_kwargs):
                    os.kill(os.getpid(), signum)
                    raise AssertionError("signal handler did not transfer control")

                with mock.patch.object(cli, "create_setup_workspace", return_value=first):
                    with mock.patch.object(cli, "run_steps", side_effect=terminate):
                        self.assertEqual(cli.run_setup(args, ROOT), 128 + signum)
                self.assertIs(signal.getsignal(signum), custom)

                second_root = Path(directory) / "second"
                second_root.mkdir()
                second = self._workspace(second_root)
                code, _calls = self._run_with_fake_steps(second, [0, 0, 0])
                self.assertEqual(code, 0)
                self.assertIs(signal.getsignal(signum), custom)
        finally:
            signal.signal(signum, previous)

    def test_signal_exit_code_and_earlier_nonzero_precedence(self):
        self.assertEqual(
            cli.setup_termination_exit_code(cli.SetupTermination(signal.SIGTERM)),
            143,
        )
        self.assertEqual(
            cli.setup_termination_exit_code(
                cli.SetupTermination(signal.SIGTERM, prior_exit_code=37)
            ),
            37,
        )

    def test_known_child_failure_precedes_signal_result(self):
        class KnownFailureProcess:
            pid = 999999

            def __init__(self):
                self.returncode = None

            def communicate(self, timeout=None):
                del timeout
                self.returncode = 37
                os.kill(os.getpid(), signal.SIGTERM)
                raise AssertionError("signal handler did not transfer control")

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                del timeout
                return self.returncode

            def terminate(self):
                return None

            def kill(self):
                return None

        lifecycle = cli.SetupSignalLifecycle()
        lifecycle.install()
        try:
            step = cli.CommandStep("profile source", ["synthetic"])
            with mock.patch.object(cli.subprocess, "Popen", return_value=KnownFailureProcess()):
                with self.assertRaises(cli.SetupTermination) as raised:
                    cli.run_steps([step], setup_lifecycle=lifecycle)
            self.assertEqual(cli.setup_termination_exit_code(raised.exception), 37)
            self.assertIsNone(lifecycle.active_child)
        finally:
            lifecycle.restore()

    def test_active_child_reference_is_cleared_after_reap(self):
        lifecycle = cli.SetupSignalLifecycle()
        lifecycle.install()
        try:
            step = cli.CommandStep("synthetic", [sys.executable, "-c", "raise SystemExit(0)"])
            self.assertEqual(cli.run_steps([step], setup_lifecycle=lifecycle), 0)
            self.assertIsNone(lifecycle.active_child)
        finally:
            lifecycle.restore()

    def test_platform_without_sighup_skips_it(self):
        with mock.patch.object(cli.signal, "SIGHUP", None):
            self.assertNotIn(None, cli.SetupSignalLifecycle.handled_signals())
            self.assertEqual(cli.SetupSignalLifecycle.handled_signals(), (signal.SIGTERM,))

    def test_non_main_thread_fails_closed_before_children(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self._workspace(Path(directory))
            args = cli.parse_args(["setup", "--primary-index", "1"])
            result = []

            def target():
                with mock.patch.object(cli, "create_setup_workspace", return_value=workspace):
                    with mock.patch.object(
                        cli,
                        "run_steps",
                        side_effect=AssertionError("no child should start"),
                    ):
                        result.append(cli.run_setup(args, ROOT))

            thread = threading.Thread(target=target)
            thread.start()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result, [1])
            self.assertFalse(workspace.directory.exists())

    def test_dry_run_installs_no_handlers_or_children(self):
        args = cli.parse_args(["setup", "--dry-run"])
        with mock.patch.object(cli.signal, "signal", side_effect=AssertionError("no handler")):
            with mock.patch.object(cli.subprocess, "Popen", side_effect=AssertionError("no child")):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.run_setup(args, ROOT), 0)


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--helper":
    raise SystemExit(_helper_main(*sys.argv[2:]))
elif __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--wrapper":
    raise SystemExit(_wrapper_main(sys.argv[2], sys.argv[3]))
elif __name__ == "__main__":
    unittest.main()
