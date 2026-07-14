import contextlib
import importlib.util
import io
import json
import os
import stat
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


def load_module():
    path = SCRIPTS / "routerkit_autostart.py"
    spec = importlib.util.spec_from_file_location("routerkit_autostart_test_module", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


autostart = load_module()


def _tcp_row(port, inode, address="0100007F"):
    return (
        "  0: {address}:{port:04X} 00000000:0000 0A "
        "00000000:00000000 00:00000000 00000000 0 0 {inode} 1 00000000"
    ).format(address=address, port=port, inode=inode)


def _stat_row(pid, start_time="123456"):
    fields_after_comm = [
        "S", "1", "1", "1", "0", "-1", "4194560", "0", "0", "0",
        "0", "0", "0", "0", "0", "20", "0", "1", "0", start_time,
        "4096", "1",
    ]
    return "{} (xray) {}\n".format(pid, " ".join(fields_after_comm))


def build_synthetic_runtime(root, *, exposed_port=None, wrong_owner_port=None):
    root = Path(root)
    opt = root / "opt"
    proc = root / "proc"
    init_dir = opt / "etc" / "init.d"
    conf_dir = opt / "etc" / "xray" / "configs"
    run_dir = opt / "var" / "run"
    xray = opt / "sbin" / "xray"
    pid = 4321

    init_dir.mkdir(parents=True)
    conf_dir.mkdir(parents=True)
    run_dir.mkdir(parents=True)
    xray.parent.mkdir(parents=True)
    xray.write_text("#!/bin/sh\n", encoding="utf-8")
    xray.chmod(0o755)
    (init_dir / "S23xray-direct").write_bytes((ROOT / "templates" / "S23xray-direct").read_bytes())
    (init_dir / "S23xray-direct").chmod(0o644)
    (init_dir / "S24xray").write_text("#!/bin/sh\n", encoding="utf-8")
    (init_dir / "S24xray").chmod(0o755)
    (run_dir / "xray-direct.pid").write_text(str(pid) + "\n", encoding="ascii")

    pid_dir = proc / str(pid)
    fd_dir = pid_dir / "fd"
    net_dir = proc / "net"
    fd_dir.mkdir(parents=True)
    net_dir.mkdir(parents=True)
    (pid_dir / "exe").symlink_to(xray)
    (pid_dir / "stat").write_text(_stat_row(pid), encoding="ascii")
    (pid_dir / "cmdline").write_bytes(
        b"\0".join(
            [
                str(xray).encode("utf-8"),
                b"run",
                b"-confdir",
                str(conf_dir).encode("utf-8"),
            ]
        )
        + b"\0"
    )
    rows = []
    for index, port in enumerate(autostart.EXPECTED_PORTS, start=1):
        inode = str(1000 + index)
        owner_inode = inode
        if wrong_owner_port == port:
            owner_inode = "999999"
        (fd_dir / str(index)).symlink_to("socket:[{}]".format(owner_inode))
        rows.append(_tcp_row(port, inode))
    if exposed_port is not None:
        rows.append(_tcp_row(exposed_port, 7777, address="00000000"))
    (net_dir / "tcp").write_text(
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
        + "\n".join(rows)
        + "\n",
        encoding="ascii",
    )
    (net_dir / "tcp6").write_text(
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n",
        encoding="ascii",
    )
    return opt, proc


class AutostartCliValidationTests(unittest.TestCase):
    def run_main(self, *argv, answer="no"):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = autostart.main(list(argv), input_fn=lambda _prompt: answer)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_enable_requires_apply_or_dry_run(self):
        code, _stdout, stderr = self.run_main("--enable")
        self.assertEqual(code, 2)
        self.assertIn("require either --apply or --dry-run", stderr)

    def test_apply_requires_enable_or_disable(self):
        code, _stdout, stderr = self.run_main("--apply")
        self.assertEqual(code, 2)
        self.assertIn("--apply requires --enable or --disable", stderr)

    def test_yes_requires_apply(self):
        code, _stdout, stderr = self.run_main("--enable", "--dry-run", "--yes")
        self.assertEqual(code, 2)
        self.assertIn("--yes requires --apply", stderr)

    def test_refusal_has_no_side_effects(self):
        with mock.patch.object(autostart, "enable_autostart", side_effect=AssertionError("must not apply")):
            code, stdout, _stderr = self.run_main("--enable", "--apply", answer="no")
        self.assertEqual(code, 1)
        self.assertIn("Cancelled", stdout)

    def test_public_cli_rejects_proc_root(self):
        with tempfile.TemporaryDirectory() as directory:
            opt, proc = build_synthetic_runtime(Path(directory))
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as caught:
                    autostart.main(["--target-root", str(opt), "--proc-root", str(proc), "--json"])
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("unrecognized arguments: --proc-root", stderr.getvalue())

    def test_public_cli_has_no_proc_root_attribute_or_env_override(self):
        with mock.patch.dict(os.environ, {"ROUTERKIT_PROC_ROOT": "/tmp/fake-proc"}):
            args = autostart.parse_args(["--json"])
        self.assertFalse(hasattr(args, "proc_root"))

    def test_json_status_is_secret_safe_without_pid_or_cmdline(self):
        with tempfile.TemporaryDirectory() as directory:
            opt, proc = build_synthetic_runtime(Path(directory))
            status = autostart.inspect_status(opt, proc_root=proc)
        payload = json.dumps(status.to_json(), sort_keys=True)
        parsed = json.loads(payload)
        self.assertNotIn("cmdline", payload)
        self.assertNotIn("SECRET", payload)
        self.assertNotIn('"pid"', payload)
        self.assertTrue(parsed["runtime_verification"]["ok"])
        self.assertTrue(parsed["runtime_verification"]["process_identity_verified"])


class AutostartRuntimeTests(unittest.TestCase):
    def test_verify_accepts_expected_loopback_listeners_owned_by_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            opt, proc = build_synthetic_runtime(Path(directory))
            status = autostart.inspect_status(opt, proc_root=proc)
        self.assertTrue(status.runtime.ok)
        self.assertFalse(status.verify_ok)
        self.assertEqual(set(status.runtime.listeners), set(autostart.EXPECTED_PORTS))

    def test_verify_rejects_wildcard_expected_port(self):
        with tempfile.TemporaryDirectory() as directory:
            opt, proc = build_synthetic_runtime(Path(directory), exposed_port=1082)
            runtime = autostart.verify_runtime(autostart.AutostartPaths(opt), proc_root=proc)
        self.assertFalse(runtime.ok)
        self.assertTrue(any("exposed" in message for message in runtime.messages))

    def test_verify_rejects_other_process_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            opt, proc = build_synthetic_runtime(Path(directory), wrong_owner_port=1083)
            runtime = autostart.verify_runtime(autostart.AutostartPaths(opt), proc_root=proc)
        self.assertFalse(runtime.ok)
        self.assertTrue(any("another process" in message for message in runtime.messages))

    def test_successful_enable_disables_s24_then_enables_s23_after_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            opt, proc = build_synthetic_runtime(Path(directory))
            paths = autostart.AutostartPaths(opt)
            stat_path = proc / "4321" / "stat"

            def restart_changes_epoch(*_args, **_kwargs):
                stat_path.write_text(_stat_row(4321, start_time="123999"), encoding="ascii")

            with mock.patch.object(autostart, "DEFAULT_TARGET_ROOT", str(opt)):
                with mock.patch.object(autostart.os, "uname", return_value=SimpleNamespace(sysname="Linux")):
                    with mock.patch.object(autostart, "_run_init", side_effect=restart_changes_epoch) as run_init:
                        result = autostart.enable_autostart(paths, proc_root=proc)
            self.assertEqual(result.action, "enable")
            self.assertTrue(result.changed_s23_mode)
            self.assertTrue(result.disabled_s24)
            self.assertTrue(result.runtime_verified)
            self.assertTrue(result.restart_performed)
            self.assertTrue(result.restart_verified)
            run_init.assert_called_once()
            self.assertEqual(run_init.call_args.args[:2], (paths, "restart"))
            self.assertTrue(paths.s23.stat().st_mode & stat.S_IXUSR)
            self.assertFalse(paths.s24.stat().st_mode & stat.S_IXUSR)

    def test_verified_noop_does_not_claim_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            opt, proc = build_synthetic_runtime(Path(directory))
            paths = autostart.AutostartPaths(opt)
            paths.s23.chmod(0o755)
            paths.s24.chmod(0o644)
            with mock.patch.object(autostart, "DEFAULT_TARGET_ROOT", str(opt)):
                with mock.patch.object(autostart.os, "uname", return_value=SimpleNamespace(sysname="Linux")):
                    result = autostart.enable_autostart(paths, proc_root=proc)
        self.assertTrue(result.noop)
        self.assertTrue(result.runtime_verified)
        self.assertFalse(result.restart_performed)
        self.assertFalse(result.restart_verified)

    def test_same_epoch_after_requested_restart_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            opt, proc = build_synthetic_runtime(Path(directory))
            paths = autostart.AutostartPaths(opt)
            with mock.patch.object(autostart, "DEFAULT_TARGET_ROOT", str(opt)):
                with mock.patch.object(autostart.os, "uname", return_value=SimpleNamespace(sysname="Linux")):
                    with mock.patch.object(autostart, "_run_init"):
                        with self.assertRaises(autostart.AutostartError) as caught:
                            autostart.enable_autostart(paths, proc_root=proc)
        self.assertIn("new Xray process epoch", str(caught.exception))

    def test_pid_epoch_change_during_verification_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            opt, proc = build_synthetic_runtime(Path(directory))
            paths = autostart.AutostartPaths(opt)
            original = autostart._read_start_time
            calls = {"count": 0}

            def changing_start(root, pid):
                calls["count"] += 1
                if calls["count"] == 1:
                    return "10"
                return "11"

            with mock.patch.object(autostart, "_read_start_time", side_effect=changing_start):
                runtime = autostart.verify_runtime(paths, proc_root=proc)
        self.assertFalse(runtime.ok)
        self.assertTrue(any("changed during verification" in item for item in runtime.messages))
        self.assertIs(original, autostart._read_start_time)

    def test_disable_does_not_stop_runtime_or_delete_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            opt, _proc = build_synthetic_runtime(Path(directory))
            paths = autostart.AutostartPaths(opt)
            paths.s23.chmod(0o755)
            with mock.patch.object(autostart, "DEFAULT_TARGET_ROOT", str(opt)):
                with mock.patch.object(autostart.subprocess, "run", side_effect=AssertionError("must not stop")):
                    result = autostart.disable_autostart(paths)
            self.assertTrue(result.changed_s23_mode)
            self.assertTrue(paths.pid_file.exists())
            self.assertFalse(paths.s23.stat().st_mode & stat.S_IXUSR)


if __name__ == "__main__":
    unittest.main()
