import contextlib
import importlib.util
import io
import json
import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_cli():
    path = SCRIPTS / "routerkit.py"
    name = "routerkit_setup_bootstrap_cli"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
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


class FakeBootstrapProcess:
    pid = 999999

    def __init__(self, returncode=0, wait_action=None, poll_action=None):
        self.returncode = returncode
        self.wait_action = wait_action
        self.poll_action = poll_action
        self.signals = []
        self.wait_calls = 0
        self.poll_calls = 0

    def send_signal(self, signum):
        self.signals.append(signum)

    def wait(self):
        self.wait_calls += 1
        if self.wait_action is not None:
            return self.wait_action(self)
        return self.returncode

    def poll(self):
        self.poll_calls += 1
        if self.poll_action is not None:
            return self.poll_action(self)
        return self.returncode


class WaitErrorProcess:
    def __init__(self, child, exc_type=OSError, failures=1, on_first_failure=None):
        self._child = child
        self._exc_type = exc_type
        self._failures_remaining = failures
        self._on_first_failure = on_first_failure
        self.wait_failures = 0
        self.alive_at_first_failure = None

    @property
    def pid(self):
        return self._child.pid

    def send_signal(self, signum):
        return self._child.send_signal(signum)

    def poll(self):
        return self._child.poll()

    def wait(self):
        if self._failures_remaining > 0:
            self._failures_remaining -= 1
            self.wait_failures += 1
            alive = self._child.poll() is None
            if self.alive_at_first_failure is None:
                self.alive_at_first_failure = alive
                if self._on_first_failure is not None:
                    self._on_first_failure()
            raise self._exc_type("synthetic wait failure")
        return self._child.wait()


def run_supervisor_with_teardown_signal(signum, *, returncode=0, spawn_error=False):
    supervisor = cli.SetupBootstrapSupervisor()
    process = FakeBootstrapProcess(returncode)
    previous_handlers = {
        item: signal.getsignal(item)
        for item in cli.SetupBootstrapSupervisor.handled_signals()
    }
    real_restore_handlers = supervisor._restore_handlers
    state = {"restore_calls": 0}

    def restore_handlers():
        state["restore_calls"] += 1
        os.kill(os.getpid(), signum)
        return real_restore_handlers()

    supervisor._restore_handlers = restore_handlers
    popen = mock.patch.object(
        cli.subprocess,
        "Popen",
        side_effect=OSError("missing") if spawn_error else None,
        return_value=process,
    )
    with popen:
        result = supervisor.run(cli.build_setup_bootstrap_apply_step(ROOT))
    return supervisor, process, result, previous_handlers, state


class SetupBootstrapCliTests(unittest.TestCase):
    def test_option_requires_apply_before_environment_or_workspace_access(self):
        source_name = "ROUTERKIT_EARLY_REJECT_SOURCE"
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {source_name: "SECRET_MARKER"}):
            with mock.patch.object(cli, "create_setup_workspace", side_effect=AssertionError("no workspace")):
                with mock.patch.object(cli.subprocess, "Popen", side_effect=AssertionError("no child")):
                    with mock.patch.object(cli.subprocess, "run", side_effect=AssertionError("no child")):
                        with contextlib.redirect_stderr(stderr):
                            code = cli.main(
                                ["setup", "--source-env", source_name, "--bootstrap-apply"]
                            )
            self.assertEqual(os.environ[source_name], "SECRET_MARKER")
        self.assertEqual(code, 2)
        self.assertIn("setup --bootstrap-apply requires --apply.", stderr.getvalue())

    def test_yes_with_bootstrap_still_requires_apply(self):
        with self.assertRaisesRegex(
            cli.RouterkitCliError,
            "setup --bootstrap-apply requires --apply",
        ):
            cli.validate_setup_args(
                cli.parse_args(["setup", "--yes", "--bootstrap-apply"])
            )

    def test_builder_delegates_only_reviewed_standalone_apply(self):
        step = cli.build_setup_bootstrap_apply_step(
            ROOT,
            remove_env_names=("ROUTERKIT_PRIVATE_SOURCE",),
        )
        self.assertEqual(step.name, "bootstrap apply")
        self.assertEqual(
            step.command,
            [
                sys.executable,
                str(ROOT / "scripts" / "routerkit-bootstrap.py"),
                "--apply",
                "--yes",
            ],
        )
        self.assertEqual(step.remove_env_names, ("ROUTERKIT_PRIVATE_SOURCE",))
        forbidden = {
            "--manifest",
            "--inventory-file",
            "--target-root",
            "--json",
            "--dry-run",
        }
        self.assertFalse(forbidden.intersection(step.command))
        self.assertNotIn("routerkit.py", Path(step.command[1]).name)

    def test_dry_run_is_abstract_and_orders_bootstrap_before_preflight(self):
        args = cli.parse_args(
            [
                "setup",
                "--source-file",
                "/private/SECRET_SOURCE_PATH",
                "--apply",
                "--bootstrap-apply",
                "--dry-run",
            ]
        )
        output = cli.render_setup_pipeline(args)
        bootstrap = "bootstrap apply (fixed missing packages + pinned Xray transaction)"
        self.assertLess(output.index("confirmation gate"), output.index(bootstrap))
        self.assertLess(output.index(bootstrap), output.index("preflight"))
        self.assertNotIn("SECRET_SOURCE_PATH", output)
        self.assertNotIn("manifests/", output)
        self.assertNotIn("http", output)

    def test_dry_run_without_option_is_unchanged(self):
        output = cli.render_setup_pipeline(
            cli.parse_args(["setup", "--apply", "--dry-run"])
        )
        self.assertNotIn("bootstrap apply", output)

    def test_dry_run_starts_no_input_workspace_or_process(self):
        forms = (
            ["--dry-run", "setup", "--apply", "--bootstrap-apply"],
            ["setup", "--apply", "--bootstrap-apply", "--dry-run"],
        )
        for argv in forms:
            with self.subTest(argv=argv):
                with mock.patch.object(cli, "create_setup_workspace", side_effect=AssertionError("no workspace")):
                    with mock.patch.object(cli.subprocess, "Popen", side_effect=AssertionError("no child")):
                        with mock.patch.object(cli.subprocess, "run", side_effect=AssertionError("no child")):
                            with contextlib.redirect_stdout(io.StringIO()):
                                self.assertEqual(cli.main(argv), 0)

    def test_confirmation_wording_is_explicit_and_old_prompt_is_preserved(self):
        prompts = []
        cli.confirm_setup_apply(lambda prompt: prompts.append(prompt) or "yes")
        cli.confirm_setup_apply(
            lambda prompt: prompts.append(prompt) or "yes",
            bootstrap_apply=True,
        )
        self.assertEqual(
            prompts,
            [
                "Proceed with router apply stages? [y/N]: ",
                "Proceed with bootstrap and router apply stages? [y/N]: ",
            ],
        )


class SetupBootstrapIntegrationTests(unittest.TestCase):
    def _run(self, bootstrap_result=None, *, yes=False, apply_result=0, answer="yes"):
        events = []
        stdout = io.StringIO()
        stderr = io.StringIO()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = workspace_under(Path(temporary.name))
        argv = ["setup", "--primary-index", "1", "--apply", "--bootstrap-apply"]
        if yes:
            argv.append("--yes")
        args = cli.parse_args(argv)

        def run_steps(steps, **_kwargs):
            if steps[0].name == "preflight" and apply_result != 0:
                events.append("preflight")
                return apply_result
            events.extend(step.name for step in steps)
            if steps[0].name == "profile source":
                write_profiles(workspace.profiles_path)
            if steps[0].name == "strict plan":
                self.assertFalse(workspace.directory.exists())
            return 0

        def confirm(prompt):
            events.append("confirmation")
            self.assertEqual(
                prompt,
                "Proceed with bootstrap and router apply stages? [y/N]: ",
            )
            return answer

        def bootstrap(step):
            events.append(step.name)
            return bootstrap_result or cli.SetupBootstrapResult(0)

        with mock.patch.object(cli, "create_setup_workspace", return_value=workspace):
            with mock.patch.object(cli, "run_steps", side_effect=run_steps):
                with mock.patch.object(cli, "run_setup_bootstrap_apply", side_effect=bootstrap) as run_bootstrap:
                    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                        code = cli.run_setup(args, ROOT, input_fn=confirm)
        return code, events, stdout.getvalue(), stderr.getvalue(), run_bootstrap

    def test_final_order_and_success_summary(self):
        code, events, stdout, _stderr, run_bootstrap = self._run()
        self.assertEqual(code, 0)
        self.assertEqual(
            events,
            [
                "profile source",
                "generator",
                "strict plan",
                "confirmation",
                "bootstrap apply",
                "preflight",
                "backup",
                "install",
                "healthcheck",
            ],
        )
        run_bootstrap.assert_called_once()
        self.assertIn("Bootstrap apply requested:", stdout)
        self.assertIn("Bootstrap apply completed before preflight.", stdout)
        self.assertIn("Bootstrap performed no service restart or autostart.", stdout)

    def test_refusal_starts_no_bootstrap_or_router_stage(self):
        code, events, stdout, _stderr, run_bootstrap = self._run(answer="no")
        self.assertEqual(code, 1)
        self.assertEqual(events, ["profile source", "generator", "strict plan", "confirmation"])
        run_bootstrap.assert_not_called()
        self.assertIn("Cancelled before bootstrap and router apply.", stdout)

    def test_yes_skips_only_confirmation(self):
        code, events, _stdout, _stderr, _run_bootstrap = self._run(yes=True)
        self.assertEqual(code, 0)
        self.assertNotIn("confirmation", events)
        self.assertIn("strict plan", events)
        self.assertIn("bootstrap apply", events)

    def test_bootstrap_failures_preserve_code_and_stop_later_stages(self):
        for returncode in (1, 2, 3, 129, 130, 143):
            with self.subTest(returncode=returncode):
                result = cli.SetupBootstrapResult(returncode)
                code, events, stdout, stderr, _run_bootstrap = self._run(
                    bootstrap_result=result
                )
                self.assertEqual(code, returncode)
                self.assertEqual(events[-1], "bootstrap apply")
                self.assertNotIn("preflight", events)
                self.assertNotIn("Setup apply completed.", stdout)
                self.assertIn(f"failed with exit code {returncode}", stderr)
                self.assertIn("No preflight, backup, install, or healthcheck", stderr)
                self.assertIn("package additions may remain", stderr)
                self.assertNotIn("Rollback hint", stderr)

    def test_spawn_failure_is_secret_safe_and_returns_127(self):
        result = cli.SetupBootstrapResult(127, spawn_failed=True)
        code, events, stdout, stderr, _run_bootstrap = self._run(
            bootstrap_result=result
        )
        self.assertEqual(code, 127)
        self.assertEqual(events[-1], "bootstrap apply")
        self.assertNotIn("preflight", events)
        self.assertNotIn("Setup apply completed.", stdout)
        self.assertEqual(stderr, "routerkit: could not run bootstrap apply.\n")

    def test_supervision_flag_blocks_later_stages_even_with_zero_code(self):
        result = cli.SetupBootstrapResult(0, supervision_failed=True)
        code, events, stdout, stderr, _run_bootstrap = self._run(
            bootstrap_result=result
        )
        self.assertEqual(code, 1)
        self.assertEqual(events[-1], "bootstrap apply")
        for stage in ("preflight", "backup", "install", "healthcheck"):
            self.assertNotIn(stage, events)
        self.assertNotIn("Setup apply completed.", stdout)
        self.assertIn("bootstrap supervision did not complete cleanly", stderr)
        self.assertIn("No preflight, backup, install, or healthcheck", stderr)
        self.assertNotIn("synthetic", stderr)

    def test_wait_error_result_blocks_router_stages_and_success_summary(self):
        result = cli.SetupBootstrapResult(1, supervision_failed=True)
        code, events, stdout, stderr, _run_bootstrap = self._run(
            bootstrap_result=result
        )
        self.assertEqual(code, 1)
        self.assertEqual(events[-1], "bootstrap apply")
        self.assertNotIn("preflight", events)
        self.assertNotIn("backup", events)
        self.assertNotIn("install", events)
        self.assertNotIn("healthcheck", events)
        self.assertNotIn("Setup apply completed.", stdout)
        self.assertIn("bootstrap supervision did not complete cleanly", stderr)

    def test_restoration_error_result_preserves_child_code_and_blocks_stages(self):
        result = cli.SetupBootstrapResult(3, supervision_failed=True)
        code, events, stdout, stderr, _run_bootstrap = self._run(
            bootstrap_result=result
        )
        self.assertEqual(code, 3)
        self.assertEqual(events[-1], "bootstrap apply")
        self.assertNotIn("preflight", events)
        self.assertNotIn("Setup apply completed.", stdout)
        self.assertIn("bootstrap supervision did not complete cleanly", stderr)
        self.assertIn("failed with exit code 3", stderr)

    def test_parent_signal_result_blocks_later_stages_even_after_child_zero(self):
        result = cli.SetupBootstrapResult(130, first_signal=signal.SIGINT)
        code, events, stdout, stderr, _run_bootstrap = self._run(
            bootstrap_result=result
        )
        self.assertEqual(code, 130)
        self.assertNotIn("preflight", events)
        self.assertNotIn("Setup apply completed.", stdout)
        self.assertIn("setup received SIGINT", stderr)
        self.assertNotIn("rollback succeeded", stderr.lower())

    def test_late_supervisor_signal_result_blocks_all_later_stages(self):
        supervisor, process, result, previous, state = (
            run_supervisor_with_teardown_signal(signal.SIGINT)
        )
        code, events, stdout, stderr, _run_bootstrap = self._run(
            bootstrap_result=result
        )
        self.assertEqual(supervisor.first_signal, signal.SIGINT)
        self.assertEqual(process.signals, [])
        self.assertEqual(result, cli.SetupBootstrapResult(130, first_signal=signal.SIGINT))
        self.assertEqual(code, 130)
        self.assertEqual(events[-1], "bootstrap apply")
        for stage in ("preflight", "backup", "install", "healthcheck"):
            self.assertNotIn(stage, events)
        self.assertNotIn("Setup apply completed.", stdout)
        self.assertIn("setup received SIGINT", stderr)
        self.assertEqual(state["restore_calls"], 1)
        self.assertIsNone(supervisor.child)
        for item, handler in previous.items():
            self.assertIs(signal.getsignal(item), handler)

    def test_preflight_failure_after_bootstrap_is_preserved(self):
        code, events, stdout, _stderr, _run_bootstrap = self._run(apply_result=47)
        self.assertEqual(code, 47)
        self.assertEqual(events[-1], "preflight")
        self.assertLess(events.index("bootstrap apply"), events.index("preflight"))
        self.assertNotIn("Setup apply completed.", stdout)


class SetupBootstrapSupervisorTests(unittest.TestCase):
    def assert_handlers_restored(self, previous):
        for item, handler in previous.items():
            self.assertIs(signal.getsignal(item), handler)

    def run_real_child_with_wait_error(
        self,
        *,
        returncode=0,
        exc_type=OSError,
        failures=1,
        child_script=None,
        on_first_failure=None,
    ):
        real_popen = cli.subprocess.Popen
        wrapped = {}
        script = child_script
        if script is None:
            script = "import sys, time; time.sleep(0.2); sys.exit({})".format(
                returncode
            )

        def popen(_command, **_kwargs):
            child = real_popen([sys.executable, "-c", script])
            process = WaitErrorProcess(
                child,
                exc_type=exc_type,
                failures=failures,
                on_first_failure=on_first_failure,
            )
            wrapped["process"] = process
            wrapped["child"] = child
            return process

        supervisor = cli.SetupBootstrapSupervisor()
        with mock.patch.object(cli.subprocess, "Popen", side_effect=popen):
            result = supervisor.run(cli.CommandStep("bootstrap apply", ["ignored"]))
        child = wrapped["child"]
        try:
            child.wait(timeout=1)
        except Exception:
            child.kill()
            child.wait(timeout=1)
            raise
        return supervisor, wrapped["process"], child, result

    def test_transient_wait_oserror_keeps_real_child_owned_and_reaped(self):
        supervisor, process, child, result = self.run_real_child_with_wait_error()
        self.assertTrue(process.alive_at_first_failure)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(result.spawn_failed)
        self.assertTrue(result.supervision_failed)
        self.assertIsNone(supervisor.child)
        self.assertIsNotNone(child.returncode)
        with self.assertRaises(ProcessLookupError):
            os.kill(child.pid, 0)

    def test_repeated_wait_error_while_child_remains_live_is_reaped(self):
        supervisor, process, child, result = self.run_real_child_with_wait_error(
            failures=3
        )
        self.assertTrue(process.alive_at_first_failure)
        self.assertEqual(process.wait_failures, 3)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.supervision_failed)
        self.assertFalse(result.spawn_failed)
        self.assertIsNone(supervisor.child)
        with self.assertRaises(ProcessLookupError):
            os.kill(child.pid, 0)

    def test_wait_error_preserves_child_exit_three(self):
        supervisor, _process, child, result = self.run_real_child_with_wait_error(
            returncode=3
        )
        self.assertEqual(result.returncode, 3)
        self.assertTrue(result.supervision_failed)
        self.assertFalse(result.spawn_failed)
        self.assertIsNone(supervisor.child)
        with self.assertRaises(ProcessLookupError):
            os.kill(child.pid, 0)

    def test_wait_runtimeerror_and_valueerror_after_spawn_are_not_spawn_failures(self):
        for exc_type in (RuntimeError, ValueError):
            with self.subTest(exc_type=exc_type.__name__):
                supervisor, _process, child, result = self.run_real_child_with_wait_error(
                    exc_type=exc_type
                )
                self.assertEqual(result.returncode, 1)
                self.assertFalse(result.spawn_failed)
                self.assertTrue(result.supervision_failed)
                self.assertIsNone(supervisor.child)
                with self.assertRaises(ProcessLookupError):
                    os.kill(child.pid, 0)

    def test_signal_during_exceptional_wait_is_forwarded_and_blocks_success(self):
        signum = signal.SIGTERM
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "signal"
            ready = Path(directory) / "ready"
            child_script = "\n".join(
                [
                    "import pathlib, signal, sys, time",
                    "marker = pathlib.Path({!r})".format(str(marker)),
                    "ready = pathlib.Path({!r})".format(str(ready)),
                    "def handle(signum, _frame):",
                    "    marker.write_text(str(signum), encoding='utf-8')",
                    "signal.signal(signal.SIGTERM, handle)",
                    "ready.write_text('ready', encoding='utf-8')",
                    "deadline = time.time() + 2",
                    "while not marker.exists() and time.time() < deadline:",
                    "    time.sleep(0.01)",
                    "time.sleep(0.05)",
                    "sys.exit(0)",
                ]
            )
            def send_after_ready():
                deadline = time.monotonic() + 2
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                os.kill(os.getpid(), signum)

            supervisor, _process, child, result = self.run_real_child_with_wait_error(
                child_script=child_script,
                on_first_failure=send_after_ready,
            )
            self.assertEqual(result.returncode, 128 + signum)
            self.assertEqual(result.first_signal, signum)
            self.assertTrue(result.supervision_failed)
            self.assertFalse(result.spawn_failed)
            self.assertEqual(marker.read_text(encoding="utf-8"), str(int(signum)))
            self.assertIsNone(supervisor.child)
            with self.assertRaises(ProcessLookupError):
                os.kill(child.pid, 0)

    @unittest.skipUnless(hasattr(signal, "pthread_sigmask"), "pthread_sigmask unavailable")
    def run_with_mask_restore_failure(self, *, failures, returncode=0):
        real_sigmask = cli.signal.pthread_sigmask
        saved_mask = real_sigmask(signal.SIG_BLOCK, [])
        supervisor = cli.SetupBootstrapSupervisor()
        state = {"armed": False, "failures": 0}

        def wait_action(_process):
            state["armed"] = True
            supervisor._previous_signal_mask = saved_mask
            return returncode

        def pthread_sigmask(how, mask):
            if (
                state["armed"]
                and how == signal.SIG_SETMASK
                and state["failures"] < failures
            ):
                state["failures"] += 1
                raise OSError("synthetic mask restore failure")
            return real_sigmask(how, mask)

        with mock.patch.object(cli.signal, "pthread_sigmask", side_effect=pthread_sigmask):
            with mock.patch.object(
                cli.subprocess,
                "Popen",
                return_value=FakeBootstrapProcess(returncode, wait_action=wait_action),
            ):
                result = supervisor.run(cli.build_setup_bootstrap_apply_step(ROOT))
        return supervisor, result, state

    @unittest.skipUnless(hasattr(signal, "pthread_sigmask"), "pthread_sigmask unavailable")
    def test_one_shot_signal_mask_restore_failure_is_retried_and_blocks_success(self):
        supervisor, result, state = self.run_with_mask_restore_failure(failures=1)
        self.assertEqual(state["failures"], 1)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.supervision_failed)
        self.assertFalse(result.spawn_failed)
        self.assertIsNone(supervisor._previous_signal_mask)
        self.assertIsNone(supervisor.child)

    @unittest.skipUnless(hasattr(signal, "pthread_sigmask"), "pthread_sigmask unavailable")
    def test_persistent_signal_mask_restore_failure_is_controlled(self):
        supervisor, result, state = self.run_with_mask_restore_failure(failures=2)
        self.assertEqual(state["failures"], 2)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.supervision_failed)
        self.assertFalse(result.spawn_failed)
        self.assertIsNotNone(supervisor._previous_signal_mask)
        self.assertIsNone(supervisor.child)

    def run_with_handler_restore_failure(
        self,
        *,
        failures,
        returncode=0,
        spawn_error=False,
    ):
        handled = cli.SetupBootstrapSupervisor.handled_signals()
        if len(handled) < 2:
            self.skipTest("at least two handled signals required")
        previous = {signum: signal.getsignal(signum) for signum in handled}
        supervisor = cli.SetupBootstrapSupervisor()
        failed_signum = handled[0]
        state = {"failures": 0, "restore_calls": []}
        real_signal = cli.signal.signal

        def signal_call(signum, handler):
            installing = getattr(handler, "__self__", None) is supervisor
            if not installing:
                state["restore_calls"].append(signum)
                if signum == failed_signum and state["failures"] < failures:
                    state["failures"] += 1
                    raise RuntimeError("synthetic handler restore failure")
            return real_signal(signum, handler)

        try:
            with mock.patch.object(cli.signal, "signal", side_effect=signal_call):
                popen = mock.patch.object(
                    cli.subprocess,
                    "Popen",
                    side_effect=OSError("missing") if spawn_error else None,
                    return_value=FakeBootstrapProcess(returncode),
                )
                with popen:
                    result = supervisor.run(cli.build_setup_bootstrap_apply_step(ROOT))
            remaining = dict(supervisor._previous_handlers)
            active_handlers = {
                signum: signal.getsignal(signum)
                for signum in handled
            }
            return supervisor, result, state, previous, remaining, active_handlers
        finally:
            for signum, handler in previous.items():
                real_signal(signum, handler)

    def test_one_handler_restore_failure_retries_after_restoring_others(self):
        supervisor, result, state, previous, remaining, active = (
            self.run_with_handler_restore_failure(failures=1)
        )
        handled = cli.SetupBootstrapSupervisor.handled_signals()
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.supervision_failed)
        self.assertFalse(result.spawn_failed)
        self.assertEqual(state["failures"], 1)
        self.assertEqual(state["restore_calls"][0], handled[0])
        self.assertIn(handled[1], state["restore_calls"][1:])
        self.assertGreaterEqual(state["restore_calls"].count(handled[0]), 2)
        self.assertEqual(remaining, {})
        self.assertEqual(active, previous)
        self.assertIsNone(supervisor.child)

    def test_persistent_handler_restore_failure_restores_later_handlers(self):
        supervisor, result, state, previous, remaining, active = (
            self.run_with_handler_restore_failure(failures=2)
        )
        handled = cli.SetupBootstrapSupervisor.handled_signals()
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.supervision_failed)
        self.assertEqual(state["failures"], 2)
        self.assertEqual(set(remaining), {handled[0]})
        for signum in handled[1:]:
            self.assertIs(active[signum], previous[signum])
        self.assertIsNone(supervisor.child)

    def test_child_exit_three_wins_over_restoration_failure(self):
        supervisor, result, _state, _previous, remaining, _active = (
            self.run_with_handler_restore_failure(failures=1, returncode=3)
        )
        self.assertEqual(result.returncode, 3)
        self.assertTrue(result.supervision_failed)
        self.assertFalse(result.spawn_failed)
        self.assertEqual(remaining, {})
        self.assertIsNone(supervisor.child)

    def test_spawn_failure_with_restoration_failure_preserves_127(self):
        supervisor, result, _state, _previous, _remaining, _active = (
            self.run_with_handler_restore_failure(failures=1, spawn_error=True)
        )
        self.assertEqual(result.returncode, 127)
        self.assertTrue(result.spawn_failed)
        self.assertTrue(result.supervision_failed)
        self.assertIsNone(supervisor.child)

    def test_cleanup_bookkeeping_and_new_supervisor_start_clean(self):
        process = FakeBootstrapProcess()

        def wait_action(_process):
            os.kill(os.getpid(), signal.SIGINT)
            return 0

        process.wait_action = wait_action
        supervisor = cli.SetupBootstrapSupervisor()
        with mock.patch.object(cli.subprocess, "Popen", return_value=process):
            result = supervisor.run(cli.build_setup_bootstrap_apply_step(ROOT))
        self.assertEqual(result.returncode, 130)
        self.assertEqual(process.signals, [signal.SIGINT])
        self.assertEqual(supervisor._pending_signals, [])
        self.assertIsNone(supervisor.child)

        second = cli.SetupBootstrapSupervisor()
        with mock.patch.object(cli.subprocess, "Popen", return_value=FakeBootstrapProcess()):
            second_result = second.run(cli.build_setup_bootstrap_apply_step(ROOT))
        self.assertEqual(second_result, cli.SetupBootstrapResult(0))
        self.assertEqual(second._pending_signals, [])
        self.assertIsNone(second.child)

    def test_late_sigint_during_handler_teardown_is_in_result(self):
        supervisor, process, result, previous, state = (
            run_supervisor_with_teardown_signal(signal.SIGINT)
        )
        self.assertEqual(supervisor.first_signal, signal.SIGINT)
        self.assertEqual(process.signals, [])
        self.assertEqual(result.first_signal, signal.SIGINT)
        self.assertEqual(result.returncode, 130)
        self.assertFalse(result.spawn_failed)
        self.assertEqual(state["restore_calls"], 1)
        self.assertIsNone(supervisor.child)
        self.assert_handlers_restored(previous)

    @unittest.skipUnless(hasattr(signal, "SIGTERM"), "SIGTERM unavailable")
    def test_late_sigterm_during_handler_teardown_is_in_result(self):
        supervisor, process, result, previous, state = (
            run_supervisor_with_teardown_signal(signal.SIGTERM)
        )
        self.assertEqual(supervisor.first_signal, signal.SIGTERM)
        self.assertEqual(process.signals, [])
        self.assertEqual(result.first_signal, signal.SIGTERM)
        self.assertEqual(result.returncode, 143)
        self.assertFalse(result.spawn_failed)
        self.assertEqual(state["restore_calls"], 1)
        self.assertIsNone(supervisor.child)
        self.assert_handlers_restored(previous)

    @unittest.skipUnless(hasattr(signal, "SIGTERM"), "SIGTERM unavailable")
    def test_late_signal_preserves_meaningful_child_failure(self):
        supervisor, process, result, previous, state = (
            run_supervisor_with_teardown_signal(signal.SIGTERM, returncode=3)
        )
        self.assertEqual(supervisor.first_signal, signal.SIGTERM)
        self.assertEqual(process.signals, [])
        self.assertEqual(result.first_signal, signal.SIGTERM)
        self.assertEqual(result.returncode, 3)
        self.assertFalse(result.spawn_failed)
        self.assertEqual(state["restore_calls"], 1)
        self.assertIsNone(supervisor.child)
        self.assert_handlers_restored(previous)

    def test_spawn_failure_preserves_127_and_late_signal(self):
        supervisor, process, result, previous, state = (
            run_supervisor_with_teardown_signal(signal.SIGINT, spawn_error=True)
        )
        self.assertEqual(supervisor.first_signal, signal.SIGINT)
        self.assertEqual(process.signals, [])
        self.assertEqual(result.first_signal, signal.SIGINT)
        self.assertEqual(result.returncode, 127)
        self.assertTrue(result.spawn_failed)
        self.assertEqual(state["restore_calls"], 1)
        self.assertIsNone(supervisor.child)
        self.assert_handlers_restored(previous)

    def test_new_supervisor_has_no_stale_late_signal_state(self):
        first, _process, first_result, previous, _state = (
            run_supervisor_with_teardown_signal(signal.SIGINT)
        )
        second = cli.SetupBootstrapSupervisor()
        with mock.patch.object(
            cli.subprocess,
            "Popen",
            return_value=FakeBootstrapProcess(0),
        ):
            second_result = second.run(cli.build_setup_bootstrap_apply_step(ROOT))
        self.assertEqual(first.first_signal, signal.SIGINT)
        self.assertEqual(first_result.returncode, 130)
        self.assertIsNone(second.first_signal)
        self.assertEqual(second_result, cli.SetupBootstrapResult(0))
        self.assertIsNone(second.child)
        self.assert_handlers_restored(previous)

    def test_environment_is_sanitized_but_path_and_unrelated_values_remain(self):
        captured = {}

        def popen(command, **kwargs):
            captured["command"] = command
            captured.update(kwargs)
            return FakeBootstrapProcess()

        step = cli.build_setup_bootstrap_apply_step(
            ROOT,
            remove_env_names=("ROUTERKIT_PRIVATE_SOURCE",),
        )
        with mock.patch.dict(
            os.environ,
            {
                "ROUTERKIT_PRIVATE_SOURCE": "SECRET_MARKER",
                "ROUTERKIT_UNRELATED": "kept",
                "PATH": "/synthetic/path",
            },
        ):
            with mock.patch.object(cli.subprocess, "Popen", side_effect=popen):
                result = cli.run_setup_bootstrap_apply(step)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("ROUTERKIT_PRIVATE_SOURCE", captured["env"])
        self.assertEqual(captured["env"]["ROUTERKIT_UNRELATED"], "kept")
        self.assertEqual(captured["env"]["PATH"], "/synthetic/path")
        self.assertNotIn("SECRET_MARKER", " ".join(captured["command"]))
        self.assertEqual(captured["start_new_session"], os.name == "posix")

    def test_oserror_before_spawn_returns_127(self):
        step = cli.build_setup_bootstrap_apply_step(ROOT)
        with mock.patch.object(cli.subprocess, "Popen", side_effect=OSError("missing")):
            result = cli.run_setup_bootstrap_apply(step)
        self.assertEqual(result, cli.SetupBootstrapResult(127, spawn_failed=True))

    def test_non_posix_fallback_uses_direct_child_without_new_session(self):
        captured = {}

        def popen(_command, **kwargs):
            captured.update(kwargs)
            return FakeBootstrapProcess()

        step = cli.build_setup_bootstrap_apply_step(ROOT)
        with mock.patch.object(cli.os, "name", "nt"):
            with mock.patch.object(cli.subprocess, "Popen", side_effect=popen):
                result = cli.run_setup_bootstrap_apply(step)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(captured["start_new_session"])

    def test_repeated_mixed_signals_are_forwarded_and_first_is_authoritative(self):
        first = signal.SIGTERM
        second = signal.SIGINT

        def wait_action(process):
            os.kill(os.getpid(), first)
            os.kill(os.getpid(), second)
            return 0

        process = FakeBootstrapProcess(wait_action=wait_action)
        with mock.patch.object(cli.subprocess, "Popen", return_value=process):
            result = cli.run_setup_bootstrap_apply(
                cli.build_setup_bootstrap_apply_step(ROOT)
            )
        self.assertEqual(process.signals, [first, second])
        self.assertEqual(result.returncode, 128 + first)
        self.assertEqual(result.first_signal, first)

    def test_child_exit_three_after_signal_takes_precedence(self):
        def wait_action(_process):
            os.kill(os.getpid(), signal.SIGTERM)
            return 3

        process = FakeBootstrapProcess(wait_action=wait_action)
        with mock.patch.object(cli.subprocess, "Popen", return_value=process):
            result = cli.run_setup_bootstrap_apply(
                cli.build_setup_bootstrap_apply_step(ROOT)
            )
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.first_signal, signal.SIGTERM)

    @unittest.skipUnless(os.name == "posix", "POSIX signal supervision test")
    def test_signal_during_spawn_is_forwarded_after_child_registration(self):
        process = FakeBootstrapProcess(returncode=143)

        def popen(*_args, **_kwargs):
            os.kill(os.getpid(), signal.SIGTERM)
            return process

        with mock.patch.object(cli.subprocess, "Popen", side_effect=popen):
            result = cli.run_setup_bootstrap_apply(
                cli.build_setup_bootstrap_apply_step(ROOT)
            )
        self.assertEqual(process.signals, [signal.SIGTERM])
        self.assertEqual(result.returncode, 143)

    @unittest.skipUnless(os.name == "posix", "POSIX signal supervision test")
    def test_real_child_receives_signals_exits_with_expected_code_and_is_reaped(self):
        signals = [signal.SIGINT, signal.SIGTERM]
        if hasattr(signal, "SIGHUP"):
            signals.append(signal.SIGHUP)
        for signum in signals:
            with self.subTest(signum=signum), tempfile.TemporaryDirectory() as directory:
                ready = Path(directory) / "ready"
                previous = {
                    item: signal.getsignal(item)
                    for item in cli.SetupBootstrapSupervisor.handled_signals()
                }

                def send_when_ready():
                    deadline = time.monotonic() + 5
                    while not ready.exists():
                        if time.monotonic() > deadline:
                            return
                        time.sleep(0.01)
                    os.kill(os.getpid(), signum)

                sender = threading.Thread(target=send_when_ready)
                sender.start()
                result = cli.run_setup_bootstrap_apply(
                    cli.CommandStep(
                        "bootstrap apply",
                        [sys.executable, __file__, "--helper", str(ready)],
                    )
                )
                sender.join(timeout=2)
                self.assertFalse(sender.is_alive())
                self.assertEqual(result.returncode, 128 + signum)
                self.assertEqual(result.first_signal, signum)
                child_pid = int(ready.read_text(encoding="utf-8"))
                with self.assertRaises(ProcessLookupError):
                    os.kill(child_pid, 0)
                for item, handler in previous.items():
                    self.assertIs(signal.getsignal(item), handler)


def helper_main(ready_path):
    def finish(signum, _frame):
        raise SystemExit(128 + signum)

    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        signum = getattr(signal, name, None)
        if signum is not None:
            signal.signal(signum, finish)
    Path(ready_path).write_text(str(os.getpid()), encoding="utf-8")
    while True:
        signal.pause()


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--helper":
    helper_main(sys.argv[2])
elif __name__ == "__main__":
    unittest.main()
