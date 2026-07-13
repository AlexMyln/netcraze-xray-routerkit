import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import routerkit_bootstrap_apply as apply


class BootstrapSignalTests(unittest.TestCase):
    def test_handlers_are_scoped_restored_and_repeated_signal_is_deferred(self):
        lifecycle = apply.BootstrapSignalLifecycle()
        previous = {value: signal.getsignal(value) for value in lifecycle.handled_signals()}
        lifecycle.install()
        try:
            first = lifecycle.handled_signals()[0]
            with self.assertRaises(apply.BootstrapTermination):
                lifecycle._handle_signal(first, None)
            lifecycle.begin_cleanup()
            for value in lifecycle.handled_signals():
                lifecycle._handle_signal(value, None)
            self.assertEqual(lifecycle.requested_signum, first)
        finally:
            lifecycle.restore()
        current = {value: signal.getsignal(value) for value in previous}
        self.assertEqual(current, previous)

    def test_sigint_remains_normal_interactive_cancellation(self):
        self.assertNotIn(signal.SIGINT, apply.BootstrapSignalLifecycle.handled_signals())

    def test_recovery_defers_recorded_and_repeated_signals_until_recovery_ends(self):
        lifecycle = apply.BootstrapSignalLifecycle()
        first = getattr(signal, "SIGTERM", 15)
        repeated = getattr(signal, "SIGHUP", first)
        lifecycle.requested_signum = first
        lifecycle.begin_recovery()

        lifecycle.raise_if_requested()
        lifecycle._handle_signal(repeated, None)
        lifecycle.raise_if_requested(prior_exit_code=9)

        self.assertEqual(lifecycle.requested_signum, first)
        self.assertTrue(lifecycle._recovery_active)
        lifecycle.end_recovery()
        self.assertFalse(lifecycle._recovery_active)
        with self.assertRaises(apply.BootstrapTermination) as raised:
            lifecycle.raise_if_requested()
        self.assertEqual(raised.exception.signum, first)

    def test_recovery_child_runs_despite_pending_signal_and_internal_errors_remain(self):
        lifecycle = apply.BootstrapSignalLifecycle()
        lifecycle.requested_signum = getattr(signal, "SIGTERM", 15)
        lifecycle.begin_recovery()
        with tempfile.TemporaryDirectory() as tmp:
            completed = apply.run_bounded_process(
                ["/bin/sh", "-c", "exit 7"],
                timeout=2.0,
                cwd=Path(tmp),
                env={"PATH": "/bin:/usr/bin"},
                lifecycle=lifecycle,
            )
            self.assertEqual(completed.returncode, 7)
            with self.assertRaises(apply.BootstrapApplyError):
                apply.run_bounded_process(
                    ["/bin/sh", "-c", "sleep 5"],
                    timeout=0.05,
                    cwd=Path(tmp),
                    env={"PATH": "/bin:/usr/bin"},
                    lifecycle=lifecycle,
                )
        self.assertIsNone(lifecycle.active_child)
        lifecycle.end_recovery()

    def test_unresponsive_child_is_terminated_then_forced_and_reaped(self):
        lifecycle = apply.BootstrapSignalLifecycle()
        child = mock.Mock()
        child.poll.return_value = 0
        child.wait.side_effect = [subprocess.TimeoutExpired(["synthetic"], 2), 0]
        lifecycle.active_child = child
        calls = []
        with mock.patch.object(
            apply, "_signal_child", side_effect=lambda owned, force: calls.append(force)
        ):
            lifecycle.shutdown_active_child()
        self.assertEqual(calls, [False, True])
        self.assertIsNone(lifecycle.active_child)

    def test_termination_exit_code_is_conventional_unless_failure_precedes_it(self):
        signum = getattr(signal, "SIGTERM", 15)
        self.assertEqual(
            apply.termination_exit_code(apply.BootstrapTermination(signum)),
            128 + signum,
        )
        self.assertEqual(
            apply.termination_exit_code(apply.BootstrapTermination(signum, 7)), 7
        )


if __name__ == "__main__":
    unittest.main()
