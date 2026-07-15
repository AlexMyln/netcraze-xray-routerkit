import importlib.util
import os
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
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_module():
    path = SCRIPTS / "routerkit_autostart.py"
    spec = importlib.util.spec_from_file_location("routerkit_autostart_signal_tests", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


autostart = load_module()


class FakeChild:
    def __init__(self, returncode=0):
        self.pid = 4242
        self.returncode = returncode
        self.wait_calls = 0
        self.signals = []

    def poll(self):
        return None if self.wait_calls == 0 else self.returncode

    def wait(self):
        self.wait_calls += 1
        return self.returncode

    def communicate(self):
        self.wait_calls += 1
        return "", ""

    def send_signal(self, signum):
        self.signals.append(signum)

    def terminate(self):
        self.signals.append(signal.SIGTERM)


class PollHostileChild(FakeChild):
    def poll(self):
        raise AssertionError("signal handler must not poll")


class AutostartInitChildTests(unittest.TestCase):
    def test_run_init_owns_child_session(self):
        paths = autostart.AutostartPaths(Path("/opt"))
        child = FakeChild(0)
        with mock.patch.object(autostart.subprocess, "Popen", return_value=child) as popen:
            autostart._run_init(paths, "restart")

        kwargs = popen.call_args.kwargs
        self.assertEqual(popen.call_args.args[0], ["sh", "/opt/etc/init.d/S23xray-direct", "restart"])
        self.assertEqual(kwargs["start_new_session"], autostart.os.name == "posix")
        self.assertIsNone(kwargs["stdout"])
        self.assertIsNone(kwargs["stderr"])
        self.assertEqual(child.wait_calls, 1)

    def test_json_mode_captures_child_output(self):
        paths = autostart.AutostartPaths(Path("/opt"))
        child = FakeChild(0)
        with mock.patch.object(autostart.subprocess, "Popen", return_value=child) as popen:
            autostart._run_init(paths, "restart", emit_output=False)

        kwargs = popen.call_args.kwargs
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], subprocess.PIPE)
        self.assertEqual(child.wait_calls, 1)

    def test_transaction_signal_forwards_to_owned_child(self):
        child = FakeChild(0)
        with autostart.TransactionSignals() as signals:
            signals.child = child
            signals._handle(signal.SIGTERM, None)

        self.assertEqual(signals.first_signal, signal.SIGTERM)
        self.assertTrue(child.signals or autostart.os.name == "posix")

    def test_signal_handler_records_and_forwards_without_polling(self):
        child = PollHostileChild(0)
        with autostart.TransactionSignals() as signals:
            signals.child = child
            signals._handle(signal.SIGINT, None)
            signals._handle(signal.SIGHUP, None)

        self.assertEqual(signals.first_signal, signal.SIGINT)
        self.assertEqual(signals.subsequent_signals, [signal.SIGHUP])

    @unittest.skipUnless(os.name == "posix", "process-group supervision is POSIX-specific")
    def test_pending_signal_after_spawn_is_reaped_before_raising(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "opt"
            init_dir = root / "etc" / "init.d"
            init_dir.mkdir(parents=True)
            script = init_dir / "S23xray-direct"
            script.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
            script.chmod(0o755)
            paths = autostart.AutostartPaths(root)
            signals = autostart.TransactionSignals()
            signals.first_signal = signal.SIGINT
            spawned = {}
            original_popen = autostart.subprocess.Popen

            def capture_popen(*args, **kwargs):
                child = original_popen(*args, **kwargs)
                spawned["child"] = child
                return child

            with mock.patch.object(autostart.subprocess, "Popen", side_effect=capture_popen):
                with self.assertRaises(autostart.AutostartError) as caught:
                    autostart._run_init(paths, "restart", signals=signals)

            child = spawned["child"]
            self.assertEqual(caught.exception.exit_code, 130)
            self.assertIsNone(signals.child)
            self.assertIsNotNone(child.returncode)
            self.assertIsNotNone(child.poll())

    def test_recovery_critical_does_not_abort_child_for_original_signal(self):
        paths = autostart.AutostartPaths(Path("/opt"))
        child = FakeChild(0)
        signals = autostart.TransactionSignals()
        signals.first_signal = signal.SIGTERM
        with signals.recovery_critical():
            with mock.patch.object(autostart.subprocess, "Popen", return_value=child):
                autostart._run_init(paths, "start", signals=signals)

        self.assertEqual(child.wait_calls, 1)
        self.assertEqual(child.signals, [])
        self.assertEqual(signals.first_signal, signal.SIGTERM)

    @unittest.skipUnless(os.name == "posix", "process-group supervision is POSIX-specific")
    def test_second_sigterm_during_recovery_child_is_deferred(self):
        result = self._run_recovery_signal_probe(signal.SIGTERM)

        self.assertIsNone(result["exception"])
        self.assertEqual(result["child_returncode"], 0)
        self.assertEqual(result["subsequent_signals"], [signal.SIGTERM])
        self.assertIsNone(result["signals"].child)
        self.assertEqual(result["signals"].signal_exit_code(), 130)

    @unittest.skipUnless(os.name == "posix", "process-group supervision is POSIX-specific")
    def test_second_sigint_during_recovery_child_is_deferred(self):
        result = self._run_recovery_signal_probe(signal.SIGINT, first_signal=signal.SIGTERM)

        self.assertIsNone(result["exception"])
        self.assertEqual(result["child_returncode"], 0)
        self.assertEqual(result["subsequent_signals"], [signal.SIGINT])
        self.assertEqual(result["signals"].signal_exit_code(), 143)

    @unittest.skipUnless(os.name == "posix", "process-group supervision is POSIX-specific")
    def test_mixed_recovery_signals_are_recorded_without_replay(self):
        result = self._run_recovery_signal_probe(signal.SIGTERM, extra_signal=signal.SIGHUP)

        self.assertIsNone(result["exception"])
        self.assertEqual(result["child_returncode"], 0)
        self.assertEqual(result["subsequent_signals"], [signal.SIGTERM, signal.SIGHUP])
        self.assertEqual(result["signals"].signal_exit_code(), 130)

    @unittest.skipUnless(os.name == "posix", "process-group supervision is POSIX-specific")
    def test_nested_recovery_context_keeps_signals_deferred(self):
        result = self._run_recovery_signal_probe(signal.SIGTERM, nested=True)

        self.assertIsNone(result["exception"])
        self.assertEqual(result["child_returncode"], 0)
        self.assertEqual(result["subsequent_signals"], [signal.SIGTERM])
        self.assertEqual(result["signals"]._recovery_depth, 0)

    def _run_recovery_signal_probe(
        self,
        second_signal,
        *,
        first_signal=signal.SIGINT,
        extra_signal=None,
        nested=False,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "opt"
            init_dir = root / "etc" / "init.d"
            init_dir.mkdir(parents=True)
            ready = Path(directory) / "ready"
            script = init_dir / "S23xray-direct"
            script.write_text(
                "#!/bin/sh\n"
                "echo ready > \"{}\"\n"
                "sleep 0.3\n".format(ready),
                encoding="utf-8",
            )
            script.chmod(0o755)
            paths = autostart.AutostartPaths(root)
            signals = autostart.TransactionSignals()
            signals.first_signal = first_signal
            child_box = {}
            original_popen = autostart.subprocess.Popen

            def capture_popen(*args, **kwargs):
                child = original_popen(*args, **kwargs)
                child_box["child"] = child
                return child

            def send_later():
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if child_box.get("child") is not None and ready.exists():
                        signals._handle(second_signal, None)
                        if extra_signal is not None:
                            signals._handle(extra_signal, None)
                        return
                    time.sleep(0.01)

            sender = threading.Thread(target=send_later)
            caught = None
            with mock.patch.object(autostart.subprocess, "Popen", side_effect=capture_popen):
                sender.start()
                try:
                    if nested:
                        with signals.recovery_critical():
                            with signals.recovery_critical():
                                autostart._run_init(paths, "start", signals=signals)
                    else:
                        with signals.recovery_critical():
                            autostart._run_init(paths, "start", signals=signals)
                except Exception as exc:
                    caught = exc
                finally:
                    sender.join(timeout=2)
                    child = child_box.get("child")
                    if child is not None and child.poll() is None:
                        os.killpg(child.pid, signal.SIGTERM)
                        child.wait(timeout=5)

            child = child_box["child"]
            return {
                "exception": caught,
                "child_returncode": child.returncode,
                "subsequent_signals": list(signals.subsequent_signals),
                "signals": signals,
            }

    def test_json_mode_bounds_child_output_and_reaps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "opt"
            init_dir = root / "etc" / "init.d"
            init_dir.mkdir(parents=True)
            script = init_dir / "S23xray-direct"
            script.write_text(
                "#!/bin/sh\n"
                "i=0\n"
                "while [ \"$i\" -lt 80 ]; do\n"
                "  printf '%01024d\\n' 0\n"
                "  printf '%01024d\\n' 0 >&2\n"
                "  i=$((i + 1))\n"
                "done\n",
                encoding="utf-8",
            )
            script.chmod(0o755)
            paths = autostart.AutostartPaths(root)
            spawned = {}
            original_popen = autostart.subprocess.Popen

            def capture_popen(*args, **kwargs):
                child = original_popen(*args, **kwargs)
                spawned["child"] = child
                return child

            with mock.patch.object(autostart.subprocess, "Popen", side_effect=capture_popen):
                with self.assertRaises(autostart.AutostartError) as caught:
                    autostart._run_init(paths, "restart", emit_output=False)

            self.assertIn("output exceeded", str(caught.exception))
            self.assertIsNotNone(spawned["child"].poll())


if __name__ == "__main__":
    unittest.main()
