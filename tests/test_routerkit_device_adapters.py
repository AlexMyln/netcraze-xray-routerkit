import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import routerkit_devices as devices


class CommandRunnerTests(unittest.TestCase):
    def test_runner_uses_exact_argv_allowlist_and_clean_environment(self):
        argv = (sys.executable, "-c", "import os; print(os.environ.get('SECRET_MARKER', 'clean'))")
        runner = devices.BoundedCommandRunner([argv])

        result = runner.run(argv, timeout_seconds=5.0, env={})

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), b"clean")

        with self.assertRaises(devices.CommandExecutionError) as caught:
            runner.run((sys.executable, "-c", "print('not allowed')"), timeout_seconds=5.0)
        self.assertEqual(caught.exception.state, devices.STATE_UNSUPPORTED)

    def test_runner_maps_timeout_and_oversized_output(self):
        timeout_argv = (sys.executable, "-c", "import time; time.sleep(2)")
        runner = devices.BoundedCommandRunner([timeout_argv])

        with self.assertRaises(devices.CommandExecutionError) as caught:
            runner.run(timeout_argv, timeout_seconds=0.01)
        self.assertEqual(caught.exception.state, devices.STATE_TIMEOUT)

        output_argv = (sys.executable, "-c", "print('x' * 200)")
        runner = devices.BoundedCommandRunner([output_argv])
        with self.assertRaises(devices.CommandExecutionError) as caught:
            runner.run(output_argv, timeout_seconds=5.0, maximum_output_bytes=10)
        self.assertEqual(caught.exception.state, devices.STATE_OUTPUT_TOO_LARGE)

    def test_runner_enforces_stdout_stderr_and_combined_output_limits(self):
        exact_argv = (sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 10)")
        runner = devices.BoundedCommandRunner([exact_argv])
        result = runner.run(exact_argv, timeout_seconds=5.0, maximum_output_bytes=10)
        self.assertEqual(result.stdout, b"x" * 10)

        stdout_argv = (sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 11)")
        stderr_argv = (sys.executable, "-c", "import sys; sys.stderr.buffer.write(b'e' * 11)")
        both_argv = (
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'x' * 6); sys.stdout.flush(); sys.stderr.buffer.write(b'e' * 6); sys.stderr.flush()",
        )
        runner = devices.BoundedCommandRunner([stdout_argv, stderr_argv, both_argv])
        for argv in (stdout_argv, stderr_argv, both_argv):
            with self.subTest(argv=argv), self.assertRaises(devices.CommandExecutionError) as caught:
                runner.run(argv, timeout_seconds=5.0, maximum_output_bytes=10)
            self.assertEqual(caught.exception.state, devices.STATE_OUTPUT_TOO_LARGE)

    def test_runner_kills_term_ignoring_child_on_timeout(self):
        argv = (
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
        )
        runner = devices.BoundedCommandRunner([argv])
        start = time.monotonic()

        with self.assertRaises(devices.CommandExecutionError) as caught:
            runner.run(argv, timeout_seconds=0.05)

        self.assertEqual(caught.exception.state, devices.STATE_TIMEOUT)
        self.assertLess(time.monotonic() - start, 2.0)

    @unittest.skipUnless(os.name == "posix", "process groups require POSIX")
    def test_runner_kills_descendants_on_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / "child.pid"
            argv = (
                sys.executable,
                "-c",
                (
                    "import subprocess,sys,time;"
                    "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']);"
                    "open(%r,'w').write(str(p.pid));"
                    "time.sleep(30)"
                )
                % str(pidfile),
            )
            runner = devices.BoundedCommandRunner([argv])

            with self.assertRaises(devices.CommandExecutionError) as caught:
                runner.run(argv, timeout_seconds=0.2)

            self.assertEqual(caught.exception.state, devices.STATE_TIMEOUT)
            descendant_pid = int(pidfile.read_text(encoding="utf-8"))
            for _ in range(20):
                try:
                    os.kill(descendant_pid, 0)
                except OSError:
                    break
                time.sleep(0.05)
            with self.assertRaises(OSError):
                os.kill(descendant_pid, 0)

    def test_runner_maps_permission_denied_return_code(self):
        argv = (sys.executable, "-c", "raise SystemExit(126)")
        runner = devices.BoundedCommandRunner([argv])

        with self.assertRaises(devices.CommandExecutionError) as caught:
            runner.run(argv, timeout_seconds=5.0)
        self.assertEqual(caught.exception.state, devices.STATE_PERMISSION_DENIED)

    def test_runner_maps_missing_executable(self):
        argv = ("/definitely/missing/routerkit-device-adapter",)
        runner = devices.BoundedCommandRunner([argv])

        with self.assertRaises(devices.CommandExecutionError) as caught:
            runner.run(argv, timeout_seconds=5.0)
        self.assertEqual(caught.exception.state, devices.STATE_UNSUPPORTED)


class InventoryFileTests(unittest.TestCase):
    def write_private(self, directory, name, data):
        path = Path(directory) / name
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8")
        if os.name == "posix":
            path.chmod(0o600)
        return path

    def test_private_inventory_rejects_invalid_encoding_and_oversize(self):
        with tempfile.TemporaryDirectory() as directory:
            bad_encoding = self.write_private(directory, "bad.json", b"\xff")
            with self.assertRaises(devices.DeviceDiscoveryError) as caught:
                devices.load_result_from_inventory_file(bad_encoding)
            self.assertEqual(caught.exception.state, devices.STATE_MALFORMED_OUTPUT)

            oversized = self.write_private(directory, "large.json", "{}")
            with mock.patch.object(devices, "MAX_INVENTORY_BYTES", 1):
                with self.assertRaises(devices.DeviceDiscoveryError) as caught:
                    devices.load_result_from_inventory_file(oversized)
            self.assertEqual(caught.exception.state, devices.STATE_OUTPUT_TOO_LARGE)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_private_inventory_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.write_private(directory, "target.json", "{}")
            link = Path(directory) / "link.json"
            link.symlink_to(target)

            with self.assertRaises(devices.PrivateFileError):
                devices.read_private_inventory_file(link)

    @unittest.skipUnless(hasattr(os, "link"), "hard links are unavailable")
    def test_private_inventory_rejects_hardlink(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.write_private(directory, "source.json", "{}")
            hardlink = Path(directory) / "hardlink.json"
            os.link(source, hardlink)

            with self.assertRaises(devices.PrivateFileError):
                devices.read_private_inventory_file(source)

    def test_contract_pending_adapter_never_collects(self):
        adapter = devices.ContractPendingAdapter()
        result = adapter.result()

        self.assertEqual(result.adapter_state, devices.STATE_CONTRACT_UNVERIFIED)
        with self.assertRaises(devices.DeviceDiscoveryError) as caught:
            adapter.collect()
        self.assertEqual(caught.exception.state, devices.STATE_CONTRACT_UNVERIFIED)


if __name__ == "__main__":
    unittest.main()
