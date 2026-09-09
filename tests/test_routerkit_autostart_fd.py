import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import routerkit_autostart as autostart
import routerkit_autostart_fd as fdscan


class FakeScanner:
    def __init__(self, entries):
        self.entries = list(entries)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def __iter__(self):
        return iter(self.entries)


class AutostartFdScannerTests(unittest.TestCase):
    def test_accepts_xray_like_process_with_more_than_729_descriptors(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            fd_dir = proc / "4321" / "fd"
            fd_dir.mkdir(parents=True)
            for index in range(800):
                (fd_dir / str(index)).symlink_to("/dev/null")
            expected = {"9001", "9002", "9003"}
            for offset, inode in enumerate(sorted(expected), start=800):
                (fd_dir / str(offset)).symlink_to("socket:[{}]".format(inode))

            observed = fdscan.socket_inodes_for_pid(proc, 4321)

        self.assertEqual(observed, expected)

    def test_scan_remains_fail_closed_above_explicit_bound(self):
        entries = [SimpleNamespace(path="/proc/fake/fd/{}".format(index)) for index in range(fdscan.MAX_PID_FD_ENTRIES + 1)]
        with mock.patch.object(fdscan.os, "scandir", return_value=FakeScanner(entries)):
            with mock.patch.object(fdscan.os, "readlink", return_value="/dev/null") as readlink:
                observed = fdscan.socket_inodes_for_pid(Path("/proc"), 4321)

        self.assertIsNone(observed)
        self.assertEqual(readlink.call_count, fdscan.MAX_PID_FD_ENTRIES)

    def test_public_autostart_entrypoint_injects_bounded_scanner(self):
        path = SCRIPTS / "routerkit-autostart.py"
        spec = importlib.util.spec_from_file_location("routerkit_autostart_public_entrypoint_test", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        self.assertIs(autostart._socket_inodes_for_pid, fdscan.socket_inodes_for_pid)
        self.assertIs(module.main, autostart.main)


if __name__ == "__main__":
    unittest.main()
