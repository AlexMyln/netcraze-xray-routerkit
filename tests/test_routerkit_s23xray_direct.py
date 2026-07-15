import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "S23xray-direct"


class S23XrayDirectTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = TEMPLATE.read_text(encoding="utf-8")

    def test_shell_syntax(self):
        completed = subprocess.run(["sh", "-n", str(TEMPLATE)], check=False)
        self.assertEqual(completed.returncode, 0)

    def test_pid_matching_fails_closed_on_proc_identity(self):
        self.assertIn('[ -d "/proc/$pid" ] || return 1', self.text)
        self.assertIn('[ -r "/proc/$pid/stat" ] || return 1', self.text)
        self.assertIn('[ -L "/proc/$pid/exe" ] || return 1', self.text)
        self.assertIn('[ -r "/proc/$pid/cmdline" ] || return 1', self.text)
        self.assertNotIn('kill -0 "$pid"', self.text.split("pid_matches_xray()")[1].split("}")[0])

    def test_signals_revalidate_epoch_before_term_and_kill(self):
        self.assertIn('pid_epoch_matches_xray "$pid" "$start" && kill "$pid"', self.text)
        self.assertIn('pid_epoch_matches_xray "$pid" "$start" && kill -9 "$pid"', self.text)
        self.assertIn('ERROR: xray-direct process did not stop.', self.text)

    def test_lock_owner_lives_inside_private_lock_directory(self):
        self.assertIn('mkdir "$LOCKDIR"', self.text)
        self.assertIn('LOCKDIR/$OWNERFILE', self.text)
        self.assertIn("trap 'exit_trap' EXIT", self.text)
        self.assertIn("trap 'signal_exit 130' INT", self.text)
        self.assertNotIn("rm -rf", self.text)

    def test_pid_publication_uses_lock_directory_temp_and_cleans_child(self):
        self.assertIn('tmp="$LOCKDIR/xray-direct.pid.$$"', self.text)
        self.assertIn('publish_pid "$child"', self.text)
        self.assertIn('terminate_direct_child "$child" "$child_start"', self.text)
        self.assertNotIn('$PIDFILE.$$', self.text)

    def test_direct_child_termination_is_bounded_and_epoch_checked(self):
        self.assertIn("ACTIVE_CHILD_PID", self.text)
        self.assertIn("ACTIVE_CHILD_START", self.text)
        self.assertIn('direct_child_epoch_alive "$child" "$start" && kill "$child"', self.text)
        self.assertIn('direct_child_epoch_alive "$child" "$start" && kill -9 "$child"', self.text)
        self.assertIn('[ "$i" -lt 10 ]', self.text)
        self.assertIn('[ "$i" -lt 5 ]', self.text)

    def test_signal_traps_clean_active_child_before_lock_release(self):
        self.assertIn("signal_exit() {", self.text)
        self.assertIn("if ! cleanup_active_child; then", self.text)
        self.assertIn("exit 3", self.text)
        self.assertIn("remove_active_pidfile", self.text)

    def test_signal_traps_preserve_successful_signal_codes(self):
        cases = (("INT", 130), ("TERM", 143), ("HUP", 129))
        for signame, expected in cases:
            with self.subTest(signame=signame):
                completed = self.run_shell_harness(
                    self.stub_functions(cleanup="return 0", release="return 0")
                    + self.function_block("manual_recovery_guidance")
                    + self.function_block("lock_recovery_guidance")
                    + self.function_block("signal_exit")
                    + self.function_block("exit_trap")
                    + "\ntrap 'exit_trap' EXIT\n"
                    + "trap 'signal_exit 130' INT\n"
                    + "trap 'signal_exit 143' TERM\n"
                    + "trap 'signal_exit 129' HUP\n"
                    + f"kill -{signame} $$\n"
                )
                self.assertEqual(completed.returncode, expected)

    def test_signal_cleanup_failure_beats_signal_code(self):
        completed = self.run_shell_harness(
            self.stub_functions(cleanup="return 1", release="return 0")
            + self.function_block("manual_recovery_guidance")
            + self.function_block("lock_recovery_guidance")
            + self.function_block("signal_exit")
            + self.function_block("exit_trap")
            + "\ntrap 'exit_trap' EXIT\n"
            + "trap 'signal_exit 143' TERM\n"
            + "kill -TERM $$\n"
        )
        self.assertEqual(completed.returncode, 3)
        self.assertIn("cleanup could not be proven", completed.stderr)
        self.assertNotIn("stopped", completed.stdout + completed.stderr)

    def test_signal_lock_release_failure_beats_signal_code(self):
        completed = self.run_shell_harness(
            self.stub_functions(cleanup="return 0", release="return 1")
            + self.function_block("manual_recovery_guidance")
            + self.function_block("lock_recovery_guidance")
            + self.function_block("signal_exit")
            + self.function_block("exit_trap")
            + "\ntrap 'exit_trap' EXIT\n"
            + "trap 'signal_exit 130' INT\n"
            + "kill -INT $$\n"
        )
        self.assertEqual(completed.returncode, 3)
        self.assertIn("lock release could not be proven", completed.stderr)

    def test_cleanup_failure_keeps_active_child_metadata(self):
        completed = self.run_shell_harness(
            'ACTIVE_CHILD_PID="123"\n'
            'ACTIVE_CHILD_START="456"\n'
            'terminate_exact_epoch_child() { return 1; }\n'
            'remove_active_pidfile() { echo unexpected-remove; return 0; }\n'
            + self.function_block("cleanup_active_child")
            + "\ncleanup_active_child\n"
            + 'rc="$?"\n'
            + 'printf "%s %s %s\\n" "$rc" "$ACTIVE_CHILD_PID" "$ACTIVE_CHILD_START"\n'
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), "1 123 456")
        self.assertNotIn("unexpected-remove", completed.stdout)

    def test_signal_trap_does_not_run_exit_cleanup_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            counter = Path(directory) / "count"
            completed = self.run_shell_harness(
                self.stub_functions(
                    cleanup=f'count="$(cat "{counter}" 2>/dev/null || printf 0)"; count=$((count + 1)); printf "%s\\n" "$count" > "{counter}"; return 0',
                    release="return 0",
                )
                + self.function_block("manual_recovery_guidance")
                + self.function_block("lock_recovery_guidance")
                + self.function_block("signal_exit")
                + self.function_block("exit_trap")
                + "\ntrap 'exit_trap' EXIT\n"
                + "trap 'signal_exit 143' TERM\n"
                + "kill -TERM $$\n"
            )
            self.assertEqual(completed.returncode, 143)
            self.assertEqual(counter.read_text(encoding="ascii").strip(), "1")

    def test_exit_cleanup_failure_does_not_overwrite_normal_failure(self):
        completed = self.run_shell_harness(
            self.stub_functions(cleanup="return 1", release="return 1")
            + self.function_block("manual_recovery_guidance")
            + self.function_block("lock_recovery_guidance")
            + self.function_block("exit_trap")
            + "\ntrap 'exit_trap' EXIT\n"
            + "exit 7\n"
        )
        self.assertEqual(completed.returncode, 7)

    def test_exit_cleanup_failure_converts_success_to_unproven(self):
        completed = self.run_shell_harness(
            self.stub_functions(cleanup="return 1", release="return 0")
            + self.function_block("manual_recovery_guidance")
            + self.function_block("lock_recovery_guidance")
            + self.function_block("exit_trap")
            + "\ntrap 'exit_trap' EXIT\n"
            + "exit 0\n"
        )
        self.assertEqual(completed.returncode, 3)

    def test_release_lock_fails_when_owned_lock_cannot_be_proven(self):
        completed = self.run_shell_harness(
            'LOCK_OWNED=1\n'
            'LOCKDIR="/tmp/routerkit-missing-lock-$$"\n'
            'OWNERFILE="owner"\n'
            'proc_start_time() { return 1; }\n'
            'read_lock_owner() { return 1; }\n'
            + self.function_block("release_lock")
            + "\nmkdir \"$LOCKDIR\"\n"
            + "release_lock\n"
        )
        self.assertEqual(completed.returncode, 1)

    def function_block(self, name):
        marker = f"{name}() {{"
        start = self.text.index(marker)
        depth = 0
        lines = []
        for line in self.text[start:].splitlines():
            lines.append(line)
            stripped = line.strip()
            if stripped.endswith("{"):
                depth += 1
            if stripped == "}":
                depth -= 1
                if depth == 0:
                    break
        return "\n".join(lines) + "\n"

    def stub_functions(self, *, cleanup, release):
        return textwrap.dedent(
            f"""
            SIGNAL_CLEANUP_DONE=0
            cleanup_active_child() {{
                {cleanup}
            }}
            release_lock() {{
                {release}
            }}
            """
        )

    def run_shell_harness(self, body):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "harness.sh"
            script.write_text("#!/bin/sh\n" + body, encoding="utf-8")
            script.chmod(0o700)
            return subprocess.run(
                ["sh", str(script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=10,
            )


if __name__ == "__main__":
    unittest.main()
