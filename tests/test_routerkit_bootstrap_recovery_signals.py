import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import routerkit_artifact_network as artifact_network
import routerkit_bootstrap_apply as apply


SECRET_MARKER = "SYNTHETIC_SECRET_MARKER"


def _write_executable(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _write_opkg(path):
    _write_executable(
        path,
        "#!/bin/sh\n"
        "if [ \"$1\" = status ]; then\n"
        "  printf '%s\\n' 'Status: install ok installed'\n"
        "  exit 0\n"
        "fi\n"
        "exit 91\n",
    )


def _candidate_body(target, ready, release, child_pid):
    return (
        "#!/bin/sh\n"
        "# {} must never reach process output.\n"
        "if [ \"$0\" = '{}' ]; then\n"
        "  printf '%s\\n' \"$$\" > '{}'\n"
        "  : > '{}'\n"
        "  while [ ! -f '{}' ]; do /bin/sleep 0.05; done\n"
        "fi\n"
        "printf '%s\\n' 'Xray 26.3.27'\n"
    ).format(SECRET_MARKER, target, child_pid, ready, release)


def _old_body(counter, recovery_ready, recovery_release, recovery_pid, mode):
    recovery = ""
    if mode in ("repeat", "timeout"):
        recovery = (
            "count=0\n"
            "if [ -f '{counter}' ]; then IFS= read -r count < '{counter}'; fi\n"
            "count=$((count + 1))\n"
            "printf '%s\\n' \"$count\" > '{counter}'\n"
            "if [ \"$count\" -ge 3 ]; then\n"
            "  printf '%s\\n' \"$$\" > '{pid}'\n"
            "  : > '{ready}'\n"
            "  while [ ! -f '{release}' ]; do /bin/sleep 0.05; done\n"
            "fi\n"
        ).format(
            counter=counter,
            pid=recovery_pid,
            ready=recovery_ready,
            release=recovery_release,
        )
    return "#!/bin/sh\n{}printf '%s\\n' 'Xray 25.1.30'\n".format(recovery)


def _worker(config_path):
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    root = Path(config["root"])
    archive = Path(config["archive"])
    manifest = json.loads((ROOT / "manifests/xray-artifacts.json").read_text(encoding="utf-8"))
    manifest["artifacts"]["linux-arm64"]["sha256"] = hashlib.sha256(
        archive.read_bytes()
    ).hexdigest()

    def downloader(source_url, destination, *, expected_url):
        if source_url != expected_url:
            raise AssertionError("manifest URL mismatch")
        data = archive.read_bytes()
        Path(destination).write_bytes(data)
        return artifact_network.ArtifactDownload(
            byte_count=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            redirect_count=0,
        )

    if config.get("rollback_fail"):
        def fail_rollback(*args, **kwargs):
            raise apply.BootstrapRollbackError("synthetic rollback failure")

        apply._rollback = fail_rollback
    def runner(command, **kwargs):
        if config.get("version_timeout") and kwargs["lifecycle"]._recovery_active:
            kwargs["timeout"] = float(config["version_timeout"])
        return apply.run_bounded_process(command, **kwargs)

    try:
        apply.apply_bootstrap_transaction(
            manifest, target_root=root, downloader=downloader, runner=runner
        )
    except apply.BootstrapTermination as exc:
        if exc.recovery_verified:
            print(
                "bootstrap: terminated after active-child shutdown, verified binary recovery, and staging cleanup.",
                file=sys.stderr,
            )
        else:
            print(
                "bootstrap: terminated after active-child shutdown and staging cleanup; no signal-time binary recovery was required.",
                file=sys.stderr,
            )
        return apply.termination_exit_code(exc)
    except apply.BootstrapApplyError as exc:
        print("bootstrap: {}".format(exc), file=sys.stderr)
        return exc.exit_code
    return 0


class BootstrapRecoveryProcessTests(unittest.TestCase):
    def _wait_for(self, path, process, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists():
                return
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                self.fail(
                    "worker exited before readiness: {} {} {}".format(
                        process.returncode, stdout, stderr
                    )
                )
            time.sleep(0.02)
        process.kill()
        process.wait()
        self.fail("worker readiness timed out: {}".format(path))

    def _assert_pid_gone(self, path):
        if not path.exists():
            return
        pid = int(path.read_text(encoding="utf-8").strip())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def _run_signal_case(
        self,
        signum,
        *,
        existing=True,
        recovery_mode="normal",
        rollback_fail=False,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / apply.TARGET_RELATIVE_PATH
            ready = root / "candidate-ready"
            release = root / "candidate-release"
            child_pid = root / "candidate-child-pid"
            recovery_ready = root / "recovery-ready"
            recovery_release = root / "recovery-release"
            recovery_pid = root / "recovery-child-pid"
            counter = root / "old-version-count"
            _write_opkg(root / "bin/opkg")

            old_bytes = None
            if existing:
                old_body = _old_body(
                    counter,
                    recovery_ready,
                    recovery_release,
                    recovery_pid,
                    recovery_mode,
                )
                _write_executable(target, old_body)
                old_bytes = target.read_bytes()

            candidate = _candidate_body(target, ready, release, child_pid)
            archive = root / "synthetic-source.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                bundle.writestr("xray", candidate.encode("utf-8"))

            config = {
                "root": str(root),
                "archive": str(archive),
                "rollback_fail": rollback_fail,
                "version_timeout": 0.25 if recovery_mode == "timeout" else None,
            }
            config_path = root / "worker.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--worker", str(config_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=dict(os.environ, PYTHONPYCACHEPREFIX="/tmp/routerkit-pycache"),
            )
            self._wait_for(ready, process)
            self.assertIn(b"Xray 26.3.27", target.read_bytes())
            os.kill(process.pid, signum)

            if recovery_mode == "repeat":
                self._wait_for(recovery_ready, process)
                repeated = getattr(signal, "SIGHUP", signum)
                os.kill(process.pid, repeated)
                recovery_release.touch()

            stdout, stderr = process.communicate(timeout=15.0)
            self._assert_pid_gone(child_pid)
            self._assert_pid_gone(recovery_pid)
            self.assertNotIn(SECRET_MARKER, stdout + stderr)
            self.assertFalse((root / apply.STATE_RELATIVE_PATH).exists())
            staging_parent = root / apply.STAGING_RELATIVE_DIR
            self.assertTrue(staging_parent.exists())
            self.assertEqual(list(staging_parent.iterdir()), [])

            backup_paths = list((root / apply.BACKUP_RELATIVE_DIR).glob("xray-*")) if existing else []
            if rollback_fail:
                self.assertEqual(process.returncode, 3)
                self.assertIn("Signal-time replacement recovery could not be proven", stderr)
                self.assertNotIn("verified binary recovery", stderr)
                self.assertEqual(len(backup_paths), 1)
                self.assertIn(str(backup_paths[0]), stderr)
            elif recovery_mode == "timeout":
                self.assertEqual(process.returncode, 3)
                self.assertIn("Signal-time replacement recovery could not be proven", stderr)
                self.assertNotIn("verified binary recovery", stderr)
            else:
                self.assertEqual(process.returncode, 128 + signum)
                self.assertIn("verified binary recovery", stderr)
                if existing:
                    self.assertEqual(target.read_bytes(), old_bytes)
                    self.assertTrue(os.access(str(target), os.X_OK))
                else:
                    self.assertFalse(target.exists())

            return process.returncode, stdout, stderr

    @unittest.skipUnless(hasattr(signal, "SIGTERM"), "SIGTERM unavailable")
    def test_existing_install_sigterm_after_replacement(self):
        self._run_signal_case(signal.SIGTERM)

    @unittest.skipUnless(hasattr(signal, "SIGHUP"), "SIGHUP unavailable")
    def test_existing_install_sighup_after_replacement(self):
        self._run_signal_case(signal.SIGHUP)

    @unittest.skipUnless(hasattr(signal, "SIGTERM"), "SIGTERM unavailable")
    def test_clean_install_signal_after_replacement_removes_candidate(self):
        self._run_signal_case(signal.SIGTERM, existing=False)

    @unittest.skipUnless(hasattr(signal, "SIGTERM"), "SIGTERM unavailable")
    def test_signal_time_rollback_failure_is_visible(self):
        self._run_signal_case(signal.SIGTERM, rollback_fail=True)

    @unittest.skipUnless(hasattr(signal, "SIGTERM"), "SIGTERM unavailable")
    def test_repeated_signal_is_deferred_during_recovery(self):
        self._run_signal_case(signal.SIGTERM, recovery_mode="repeat")

    @unittest.skipUnless(hasattr(signal, "SIGTERM"), "SIGTERM unavailable")
    def test_rollback_validation_child_timeout_is_failure_and_is_reaped(self):
        self._run_signal_case(signal.SIGTERM, recovery_mode="timeout")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        raise SystemExit(_worker(Path(sys.argv[2])))
    unittest.main()
