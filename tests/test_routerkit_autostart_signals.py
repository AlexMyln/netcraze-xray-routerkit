import importlib.util
import signal
import subprocess
import sys
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


if __name__ == "__main__":
    unittest.main()
