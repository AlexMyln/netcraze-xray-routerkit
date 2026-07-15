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

    def test_exit_cleanup_failure_overwrites_normal_failure_with_unproven_code(self):
        completed = self.run_shell_harness(
            self.stub_functions(cleanup="return 1", release="return 1")
            + self.function_block("manual_recovery_guidance")
            + self.function_block("lock_recovery_guidance")
            + self.function_block("exit_trap")
            + "\ntrap 'exit_trap' EXIT\n"
            + "exit 7\n"
        )
        self.assertEqual(completed.returncode, 3)

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
            + self.lock_function_blocks()
            + "\nmkdir \"$LOCKDIR\"\n"
            + "release_lock\n"
        )
        self.assertEqual(completed.returncode, 1)

    def test_release_lock_succeeds_when_owned_lock_is_genuinely_absent(self):
        completed = self.run_release_lock_case("")
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["release_rc"], "0")
        self.assertEqual(result["LOCK_OWNED"], "0")
        self.assertEqual(result["is_link"], "no")
        self.assertEqual(result["exists"], "no")

    def test_release_lock_succeeds_for_correctly_owned_real_directory(self):
        completed = self.run_release_lock_case(
            'mkdir "$LOCKDIR"\n'
            'printf "%s 12345\\n" "$$" > "$LOCKDIR/$OWNERFILE"\n'
        )
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["release_rc"], "0")
        self.assertEqual(result["LOCK_OWNED"], "0")
        self.assertEqual(result["exists"], "no")

    def test_release_lock_fails_closed_for_dangling_symlink(self):
        completed = self.run_release_lock_case('ln -s /definitely/missing "$LOCKDIR"\n')
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["release_rc"], "1")
        self.assertEqual(result["LOCK_OWNED"], "1")
        self.assertEqual(result["is_link"], "yes")

    def test_release_lock_fails_closed_for_symlink_to_directory(self):
        completed = self.run_release_lock_case(
            'mkdir "$base/target"\n'
            'ln -s "$base/target" "$LOCKDIR"\n'
        )
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["release_rc"], "1")
        self.assertEqual(result["LOCK_OWNED"], "1")
        self.assertEqual(result["is_link"], "yes")
        self.assertEqual(result["target_dir"], "yes")

    def test_release_lock_fails_closed_for_symlink_to_regular_file(self):
        completed = self.run_release_lock_case(
            'printf "target\\n" > "$base/target"\n'
            'ln -s "$base/target" "$LOCKDIR"\n'
        )
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["release_rc"], "1")
        self.assertEqual(result["LOCK_OWNED"], "1")
        self.assertEqual(result["is_link"], "yes")
        self.assertEqual(result["target_file"], "yes")

    def test_release_lock_fails_closed_for_regular_file(self):
        completed = self.run_release_lock_case('printf "not-dir\\n" > "$LOCKDIR"\n')
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["release_rc"], "1")
        self.assertEqual(result["LOCK_OWNED"], "1")
        self.assertEqual(result["is_file"], "yes")

    def test_release_lock_fails_closed_for_fifo(self):
        completed = self.run_release_lock_case('mkfifo "$LOCKDIR"\n')
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["release_rc"], "1")
        self.assertEqual(result["LOCK_OWNED"], "1")
        self.assertEqual(result["is_fifo"], "yes")

    def test_release_lock_fails_closed_for_malformed_owner_file(self):
        completed = self.run_release_lock_case(
            'mkdir "$LOCKDIR"\n'
            'printf "bad-owner\\n" > "$LOCKDIR/$OWNERFILE"\n'
        )
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["release_rc"], "1")
        self.assertEqual(result["LOCK_OWNED"], "1")
        self.assertEqual(result["owner_file"], "yes")

    def test_release_lock_fails_closed_for_missing_owner_file(self):
        completed = self.run_release_lock_case('mkdir "$LOCKDIR"\n')
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["release_rc"], "1")
        self.assertEqual(result["LOCK_OWNED"], "1")
        self.assertEqual(result["is_dir"], "yes")

    def test_release_lock_fails_closed_for_owner_file_symlink(self):
        completed = self.run_release_lock_case(
            'mkdir "$LOCKDIR"\n'
            'printf "%s 12345\\n" "$$" > "$base/owner-target"\n'
            'ln -s "$base/owner-target" "$LOCKDIR/$OWNERFILE"\n'
        )
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["release_rc"], "1")
        self.assertEqual(result["LOCK_OWNED"], "1")
        self.assertEqual(result["owner_link"], "yes")

    def test_release_lock_fails_closed_for_other_pid_owner(self):
        completed = self.run_release_lock_case(
            'mkdir "$LOCKDIR"\n'
            'printf "99999 12345\\n" > "$LOCKDIR/$OWNERFILE"\n'
        )
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["release_rc"], "1")
        self.assertEqual(result["LOCK_OWNED"], "1")
        self.assertEqual(result["owner_file"], "yes")

    def test_release_lock_fails_closed_for_same_pid_different_start(self):
        completed = self.run_release_lock_case(
            'mkdir "$LOCKDIR"\n'
            'printf "%s 99999\\n" "$$" > "$LOCKDIR/$OWNERFILE"\n'
        )
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["release_rc"], "1")
        self.assertEqual(result["LOCK_OWNED"], "1")
        self.assertEqual(result["owner_file"], "yes")

    def test_release_lock_fails_if_lock_path_becomes_dangling_symlink_during_cleanup(self):
        completed = self.run_release_lock_case(
            'mkdir "$LOCKDIR"\n'
            'printf "%s 12345\\n" "$$" > "$LOCKDIR/$OWNERFILE"\n',
            extra_functions=textwrap.dedent(
                """
                lock_owner_identity() {
                    count_file="$base/owner-identity-count"
                    count="$(cat "$count_file" 2>/dev/null || printf 0)"
                    count=$((count + 1))
                    printf '%s\\n' "$count" > "$count_file"
                    if [ "$count" -eq 2 ]; then
                        rm -f "$LOCKDIR/$OWNERFILE"
                        rmdir "$LOCKDIR"
                        ln -s /definitely/missing "$LOCKDIR"
                        return 1
                    fi
                    lock_owner_file_is_safe || return 1
                    set -- $(ls -i "$LOCKDIR/$OWNERFILE" 2>/dev/null)
                    case "$1" in
                        ""|*[!0-9]*) return 1 ;;
                    esac
                    printf '%s\\n' "$1"
                }
                """
            ),
        )
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["release_rc"], "1")
        self.assertEqual(result["LOCK_OWNED"], "1")
        self.assertEqual(result["is_link"], "yes")

    def test_release_lock_fails_if_lock_path_becomes_another_directory_during_cleanup(self):
        completed = self.run_release_lock_case(
            'mkdir "$LOCKDIR"\n'
            'printf "%s 12345\\n" "$$" > "$LOCKDIR/$OWNERFILE"\n',
            extra_functions=textwrap.dedent(
                """
                lock_owner_identity() {
                    count_file="$base/owner-identity-count"
                    count="$(cat "$count_file" 2>/dev/null || printf 0)"
                    count=$((count + 1))
                    printf '%s\\n' "$count" > "$count_file"
                    if [ "$count" -eq 2 ]; then
                        rm -f "$LOCKDIR/$OWNERFILE"
                        rmdir "$LOCKDIR"
                        mkdir "$LOCKDIR"
                        return 1
                    fi
                    lock_owner_file_is_safe || return 1
                    set -- $(ls -i "$LOCKDIR/$OWNERFILE" 2>/dev/null)
                    case "$1" in
                        ""|*[!0-9]*) return 1 ;;
                    esac
                    printf '%s\\n' "$1"
                }
                """
            ),
        )
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["release_rc"], "1")
        self.assertEqual(result["LOCK_OWNED"], "1")
        self.assertEqual(result["is_link"], "no")
        self.assertEqual(result["is_dir"], "yes")
        self.assertEqual(result["owner_file"], "no")

    def test_signal_trap_returns_unproven_cleanup_for_dangling_lock_symlink(self):
        completed = self.run_shell_harness(
            self.lock_function_blocks()
            + self.function_block("manual_recovery_guidance")
            + self.function_block("lock_recovery_guidance")
            + self.function_block("signal_exit")
            + self.function_block("exit_trap")
            + textwrap.dedent(
                """
                cleanup_active_child() { return 0; }
                proc_start_time() { printf '%s\\n' 12345; }
                SIGNAL_CLEANUP_DONE=0
                OWNERFILE="owner"
                LOCK_OWNED=1
                base="${TMPDIR:-/tmp}/routerkit-signal-lock-$$"
                LOCKDIR="$base/lock"
                mkdir -p "$LOCKDIR"
                printf "%s 12345\\n" "$$" > "$LOCKDIR/$OWNERFILE"
                rm -f "$LOCKDIR/$OWNERFILE"
                rmdir "$LOCKDIR"
                ln -s /definitely/missing "$LOCKDIR"
                trap 'exit_trap' EXIT
                trap 'signal_exit 143' TERM
                kill -TERM $$
                """
            )
        )
        self.assertEqual(completed.returncode, 3)
        self.assertIn("lock release could not be proven", completed.stderr)

    def test_exit_cleanup_returns_unproven_for_dangling_lock_symlink_after_success(self):
        completed = self.run_shell_harness(
            self.lock_function_blocks()
            + self.function_block("lock_recovery_guidance")
            + self.function_block("exit_trap")
            + textwrap.dedent(
                """
                cleanup_active_child() { return 0; }
                proc_start_time() { printf '%s\\n' 12345; }
                SIGNAL_CLEANUP_DONE=0
                OWNERFILE="owner"
                LOCK_OWNED=1
                base="${TMPDIR:-/tmp}/routerkit-exit-lock-$$"
                LOCKDIR="$base/lock"
                mkdir -p "$LOCKDIR"
                printf "%s 12345\\n" "$$" > "$LOCKDIR/$OWNERFILE"
                rm -f "$LOCKDIR/$OWNERFILE"
                rmdir "$LOCKDIR"
                ln -s /definitely/missing "$LOCKDIR"
                trap 'exit_trap' EXIT
                exit 0
                """
            )
        )
        self.assertEqual(completed.returncode, 3)
        self.assertIn("lock release could not be proven", completed.stderr)

    def test_exit_cleanup_keeps_existing_unproven_code_three(self):
        completed = self.run_shell_harness(
            self.stub_functions(cleanup="return 0", release="return 1")
            + self.function_block("lock_recovery_guidance")
            + self.function_block("exit_trap")
            + "\ntrap 'exit_trap' EXIT\n"
            + "exit 3\n"
        )
        self.assertEqual(completed.returncode, 3)

    def test_signal_cleanup_and_lock_release_failures_return_unproven(self):
        completed = self.run_shell_harness(
            self.stub_functions(cleanup="return 1", release="return 1")
            + self.function_block("manual_recovery_guidance")
            + self.function_block("lock_recovery_guidance")
            + self.function_block("signal_exit")
            + self.function_block("exit_trap")
            + "\ntrap 'exit_trap' EXIT\n"
            + "trap 'signal_exit 129' HUP\n"
            + "kill -HUP $$\n"
        )
        self.assertEqual(completed.returncode, 3)

    def test_start_stop_restart_status_still_use_existing_release_contract(self):
        for name in ("start", "stop", "restart"):
            with self.subTest(name=name):
                block = self.function_block(name)
                self.assertIn("lock || return 1", block)
                self.assertIn("if ! release_lock; then", block)
                self.assertIn("rc=3", block)
        self.assertIn("status) status ;;", self.text)

    def test_stale_owner_branch_has_no_path_cleanup_or_retry(self):
        lock_block = self.function_block("lock")
        start = lock_block.index('if [ "$owner_state" -eq 1 ]; then')
        end = lock_block.index('echo "ERROR: xray-direct lock ownership is unclear')
        stale_branch = lock_block[start:end]
        self.assertIn("stale_lock_owner_is_confirmed", stale_branch)
        self.assertIn("lock owner appears stale", stale_branch)
        self.assertNotIn('rm -f "$LOCKDIR/$OWNERFILE"', stale_branch)
        self.assertNotIn('rmdir "$LOCKDIR"', stale_branch)
        self.assertNotIn("continue", stale_branch)

    def test_lock_acquires_when_absent(self):
        completed = self.run_lock_case("")
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["lock_rc"], "0")
        self.assertEqual(result["LOCK_OWNED"], "1")
        self.assertEqual(result["is_dir"], "yes")
        self.assertEqual(result["owner_file"], "yes")
        self.assertEqual(result["rm_calls"], "0")
        self.assertEqual(result["rmdir_calls"], "0")

    def test_lock_live_owner_waits_boundedly_and_preserves_lock(self):
        completed = self.run_lock_case(
            'mkdir "$LOCKDIR"\n'
            'printf "111 555\\n" > "$LOCKDIR/$OWNERFILE"\n',
            extra_functions='kill() { [ "$1" = "-0" ] && [ "$2" = "111" ] && return 0; command kill "$@"; }\n',
        )
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["lock_rc"], "1")
        self.assertEqual(result["LOCK_OWNED"], "0")
        self.assertEqual(result["owner_line"], "111 555")
        self.assertEqual(result["mkdir_lock_calls"], "20")
        self.assertEqual(result["sleep_calls"], "19")
        self.assertEqual(result["rm_calls"], "0")
        self.assertEqual(result["rmdir_calls"], "0")
        self.assertIn("lock is busy", completed.stderr)

    def test_lock_dead_owner_fails_closed_and_preserves_metadata(self):
        completed = self.run_lock_case(
            'mkdir "$LOCKDIR"\n'
            'printf "999999 12345\\n" > "$LOCKDIR/$OWNERFILE"\n'
        )
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["lock_rc"], "1")
        self.assertEqual(result["LOCK_OWNED"], "0")
        self.assertEqual(result["is_dir"], "yes")
        self.assertEqual(result["owner_file"], "yes")
        self.assertEqual(result["owner_line"], "999999 12345")
        self.assertEqual(result["mkdir_lock_calls"], "1")
        self.assertEqual(result["rm_calls"], "0")
        self.assertEqual(result["rmdir_calls"], "0")
        self.assertIn("lock owner appears stale", completed.stderr)

    def test_lock_owner_replacement_after_first_proof_is_preserved(self):
        completed = self.run_lock_case(
            'mkdir "$LOCKDIR"\n'
            'printf "999999 12345\\n" > "$LOCKDIR/$OWNERFILE"\n',
            extra_functions=self.owner_alive_replacement_hook(
                'printf "424242 22222\\n" > "$LOCKDIR/$OWNERFILE"\n'
            ),
        )
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["lock_rc"], "1")
        self.assertEqual(result["owner_line"], "424242 22222")
        self.assertEqual(result["rm_calls"], "0")
        self.assertEqual(result["rmdir_calls"], "0")
        self.assertIn("lock owner appears stale", completed.stderr)

    def test_lock_directory_replacement_after_first_proof_is_preserved(self):
        completed = self.run_lock_case(
            'mkdir "$LOCKDIR"\n'
            'printf "999999 12345\\n" > "$LOCKDIR/$OWNERFILE"\n',
            extra_functions=self.owner_alive_replacement_hook(
                'mv "$LOCKDIR" "$base/original-lock"\n'
                'command mkdir "$LOCKDIR"\n'
                'printf "424242 22222\\n" > "$LOCKDIR/$OWNERFILE"\n'
            ),
        )
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["lock_rc"], "1")
        self.assertEqual(result["owner_line"], "424242 22222")
        self.assertEqual(result["original_owner_saved"], "yes")
        self.assertEqual(result["rm_calls"], "0")
        self.assertEqual(result["rmdir_calls"], "0")

    def test_lock_directory_replaced_with_dangling_symlink_fails_unclear(self):
        completed = self.run_lock_case(
            'mkdir "$LOCKDIR"\n'
            'printf "999999 12345\\n" > "$LOCKDIR/$OWNERFILE"\n',
            extra_functions=self.owner_alive_replacement_hook(
                'mv "$LOCKDIR" "$base/original-lock"\n'
                'ln -s "$base/missing" "$LOCKDIR"\n'
            ),
        )
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["lock_rc"], "1")
        self.assertEqual(result["is_link"], "yes")
        self.assertEqual(result["original_owner_saved"], "yes")
        self.assertEqual(result["rm_calls"], "0")
        self.assertEqual(result["rmdir_calls"], "0")
        self.assertIn("lock ownership is unclear", completed.stderr)

    def test_lock_directory_replaced_with_symlink_to_directory_fails_unclear(self):
        completed = self.run_lock_case(
            'mkdir "$LOCKDIR"\n'
            'printf "999999 12345\\n" > "$LOCKDIR/$OWNERFILE"\n',
            extra_functions=self.owner_alive_replacement_hook(
                'command mkdir "$base/target"\n'
                'printf "424242 22222\\n" > "$base/target/$OWNERFILE"\n'
                'mv "$LOCKDIR" "$base/original-lock"\n'
                'ln -s "$base/target" "$LOCKDIR"\n'
            ),
        )
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["lock_rc"], "1")
        self.assertEqual(result["is_link"], "yes")
        self.assertEqual(result["target_owner"], "yes")
        self.assertEqual(result["rm_calls"], "0")
        self.assertEqual(result["rmdir_calls"], "0")
        self.assertIn("lock ownership is unclear", completed.stderr)

    def test_lock_owner_replaced_with_symlink_fails_unclear(self):
        completed = self.run_lock_case(
            'mkdir "$LOCKDIR"\n'
            'printf "999999 12345\\n" > "$LOCKDIR/$OWNERFILE"\n',
            extra_functions=self.owner_alive_replacement_hook(
                'command rm -f "$LOCKDIR/$OWNERFILE"\n'
                'printf "424242 22222\\n" > "$base/owner-target"\n'
                'ln -s "$base/owner-target" "$LOCKDIR/$OWNERFILE"\n'
            ),
        )
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["lock_rc"], "1")
        self.assertEqual(result["owner_link"], "yes")
        self.assertEqual(result["rm_calls"], "0")
        self.assertEqual(result["rmdir_calls"], "0")
        self.assertIn("lock ownership is unclear", completed.stderr)

    def test_lock_owner_replaced_with_regular_file_is_preserved(self):
        completed = self.run_lock_case(
            'mkdir "$LOCKDIR"\n'
            'printf "999999 12345\\n" > "$LOCKDIR/$OWNERFILE"\n',
            extra_functions=self.owner_alive_replacement_hook(
                'command rm -f "$LOCKDIR/$OWNERFILE"\n'
                'printf "424242 22222\\n" > "$LOCKDIR/$OWNERFILE"\n'
            ),
        )
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["lock_rc"], "1")
        self.assertEqual(result["owner_line"], "424242 22222")
        self.assertEqual(result["rm_calls"], "0")
        self.assertEqual(result["rmdir_calls"], "0")
        self.assertIn("lock owner appears stale", completed.stderr)

    def test_lock_malformed_owner_fails_unclear_without_deletion(self):
        completed = self.run_lock_case(
            'mkdir "$LOCKDIR"\n'
            'printf "bad-owner\\n" > "$LOCKDIR/$OWNERFILE"\n'
        )
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["lock_rc"], "1")
        self.assertEqual(result["owner_line"], "bad-owner")
        self.assertEqual(result["rm_calls"], "0")
        self.assertEqual(result["rmdir_calls"], "0")
        self.assertIn("lock ownership is unclear", completed.stderr)

    def test_lock_missing_owner_fails_unclear_without_deletion(self):
        completed = self.run_lock_case('mkdir "$LOCKDIR"\n')
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["lock_rc"], "1")
        self.assertEqual(result["is_dir"], "yes")
        self.assertEqual(result["owner_exists"], "no")
        self.assertEqual(result["rm_calls"], "0")
        self.assertEqual(result["rmdir_calls"], "0")
        self.assertIn("lock ownership is unclear", completed.stderr)

    def test_lock_fifo_owner_fails_unclear_without_deletion(self):
        completed = self.run_lock_case(
            'mkdir "$LOCKDIR"\n'
            'mkfifo "$LOCKDIR/$OWNERFILE"\n'
        )
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["lock_rc"], "1")
        self.assertEqual(result["owner_fifo"], "yes")
        self.assertEqual(result["rm_calls"], "0")
        self.assertEqual(result["rmdir_calls"], "0")
        self.assertIn("lock ownership is unclear", completed.stderr)

    def test_lock_unreadable_owner_fails_without_deletion(self):
        completed = self.run_lock_case(
            'mkdir "$LOCKDIR"\n'
            'printf "999999 12345\\n" > "$LOCKDIR/$OWNERFILE"\n'
            'chmod 000 "$LOCKDIR/$OWNERFILE"\n'
        )
        result = self.parse_kv(completed.stdout)
        self.assertEqual(result["lock_rc"], "1")
        self.assertEqual(result["owner_file"], "yes")
        self.assertEqual(result["rm_calls"], "0")
        self.assertEqual(result["rmdir_calls"], "0")

    def test_stale_lock_blocks_start_stop_restart_without_child_action(self):
        for name in ("start", "stop", "restart"):
            with self.subTest(name=name):
                completed = self.run_shell_harness(
                    textwrap.dedent(
                        """
                        OWNERFILE="owner"
                        LOCK_OWNED=0
                        SIGNAL_CLEANUP_DONE=0
                        proc_start_time() { [ "$1" = "$$" ] && printf '%s\\n' 77777 || return 1; }
                        """
                    )
                    + self.lock_acquisition_blocks()
                    + self.function_block("lock_recovery_guidance")
                    + self.function_block("start")
                    + self.function_block("stop")
                    + self.function_block("restart")
                    + textwrap.dedent(
                        f"""
                        start_unlocked() {{ echo unexpected-start; return 0; }}
                        stop_unlocked() {{ echo unexpected-stop; return 0; }}
                        release_lock() {{ echo unexpected-release; return 0; }}
                        base="${{TMPDIR:-/tmp}}/routerkit-action-lock-$$"
                        LOCKDIR="$base/lock"
                        mkdir -p "$LOCKDIR"
                        printf "999999 12345\\n" > "$LOCKDIR/$OWNERFILE"
                        {name}
                        rc="$?"
                        printf 'action_rc=%s\\n' "$rc"
                        printf 'owner_line=%s\\n' "$(sed -n '1p' "$LOCKDIR/$OWNERFILE")"
                        command rm -rf "$base"
                        exit 0
                        """
                    )
                )
                result = self.parse_kv(completed.stdout)
                self.assertEqual(result["action_rc"], "1")
                self.assertEqual(result["owner_line"], "999999 12345")
                self.assertNotIn("unexpected-", completed.stdout)
                self.assertIn("lock owner appears stale", completed.stderr)

    def lock_acquisition_blocks(self):
        return (
            self.function_block("lock_path_is_real_dir")
            + self.function_block("lock_owner_file_is_safe")
            + self.function_block("read_lock_owner")
            + self.function_block("owner_is_alive")
            + self.function_block("stale_lock_owner_is_confirmed")
            + self.function_block("write_lock_owner")
            + self.function_block("lock")
        )

    def owner_alive_replacement_hook(self, replacement):
        return textwrap.dedent(
            f"""
            owner_is_alive() {{
                count_file="$base/owner-alive-count"
                count="$(cat "$count_file" 2>/dev/null || printf 0)"
                count=$((count + 1))
                printf '%s\\n' "$count" > "$count_file"
                read_lock_owner || return 2
                owner_result=1
                if [ "$(proc_start_time "$OWNER_PID" 2>/dev/null)" = "$OWNER_START" ]; then
                    kill -0 "$OWNER_PID" 2>/dev/null && owner_result=0
                fi
                if [ "$count" -eq 1 ]; then
                    {replacement}
                fi
                return "$owner_result"
            }}
            """
        )

    def run_lock_case(self, setup, *, extra_functions=""):
        completed = self.run_shell_harness(
            textwrap.dedent(
                """
                OWNERFILE="owner"
                LOCK_OWNED=0
                proc_start_time() {
                    case "$1" in
                        "$$") printf '%s\\n' 77777 ;;
                        "111") printf '%s\\n' 555 ;;
                        *) return 1 ;;
                    esac
                }
                """
            )
            + self.lock_acquisition_blocks()
            + extra_functions
            + textwrap.dedent(
                f"""
                base="${{TMPDIR:-/tmp}}/routerkit-lock-$$"
                LOCKDIR="$base/lock"
                mkdir -p "$base"
                {setup}
                RM_CALLS=0
                RMDIR_CALLS=0
                MKDIR_LOCK_CALLS=0
                SLEEP_CALLS=0
                rm() {{ RM_CALLS=$((RM_CALLS + 1)); command rm "$@"; }}
                rmdir() {{ RMDIR_CALLS=$((RMDIR_CALLS + 1)); command rmdir "$@"; }}
                mkdir() {{
                    if [ "$#" -eq 1 ] && [ "$1" = "$LOCKDIR" ]; then
                        MKDIR_LOCK_CALLS=$((MKDIR_LOCK_CALLS + 1))
                    fi
                    command mkdir "$@"
                }}
                sleep() {{ SLEEP_CALLS=$((SLEEP_CALLS + 1)); return 0; }}

                lock
                rc="$?"
                if [ -L "$LOCKDIR" ]; then is_link=yes; else is_link=no; fi
                if [ -e "$LOCKDIR" ]; then exists=yes; else exists=no; fi
                if [ -d "$LOCKDIR" ] && [ ! -L "$LOCKDIR" ]; then is_dir=yes; else is_dir=no; fi
                if [ -e "$LOCKDIR/$OWNERFILE" ]; then owner_exists=yes; else owner_exists=no; fi
                if [ -f "$LOCKDIR/$OWNERFILE" ] && [ ! -L "$LOCKDIR/$OWNERFILE" ]; then owner_file=yes; else owner_file=no; fi
                if [ -L "$LOCKDIR/$OWNERFILE" ]; then owner_link=yes; else owner_link=no; fi
                if [ -p "$LOCKDIR/$OWNERFILE" ]; then owner_fifo=yes; else owner_fifo=no; fi
                owner_line=""
                if [ -f "$LOCKDIR/$OWNERFILE" ] && [ ! -L "$LOCKDIR/$OWNERFILE" ] && [ -r "$LOCKDIR/$OWNERFILE" ]; then
                    owner_line="$(sed -n '1p' "$LOCKDIR/$OWNERFILE")"
                fi
                if [ -e "$base/original-lock/$OWNERFILE" ]; then original_owner_saved=yes; else original_owner_saved=no; fi
                if [ -e "$base/target/$OWNERFILE" ]; then target_owner=yes; else target_owner=no; fi
                printf 'lock_rc=%s\\n' "$rc"
                printf 'LOCK_OWNED=%s\\n' "$LOCK_OWNED"
                printf 'is_link=%s\\n' "$is_link"
                printf 'exists=%s\\n' "$exists"
                printf 'is_dir=%s\\n' "$is_dir"
                printf 'owner_exists=%s\\n' "$owner_exists"
                printf 'owner_file=%s\\n' "$owner_file"
                printf 'owner_link=%s\\n' "$owner_link"
                printf 'owner_fifo=%s\\n' "$owner_fifo"
                printf 'owner_line=%s\\n' "$owner_line"
                printf 'original_owner_saved=%s\\n' "$original_owner_saved"
                printf 'target_owner=%s\\n' "$target_owner"
                printf 'rm_calls=%s\\n' "$RM_CALLS"
                printf 'rmdir_calls=%s\\n' "$RMDIR_CALLS"
                printf 'mkdir_lock_calls=%s\\n' "$MKDIR_LOCK_CALLS"
                printf 'sleep_calls=%s\\n' "$SLEEP_CALLS"
                chmod -R u+rwX "$base" 2>/dev/null || true
                command rm -rf "$base"
                exit 0
                """
            )
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed

    def lock_function_blocks(self):
        return (
            self.function_block("lock_path_is_real_dir")
            + self.function_block("lock_dir_identity")
            + self.function_block("lock_owner_file_is_safe")
            + self.function_block("lock_owner_identity")
            + self.function_block("read_lock_owner")
            + self.function_block("release_lock")
        )

    def run_release_lock_case(self, setup, *, extra_functions=""):
        completed = self.run_shell_harness(
            textwrap.dedent(
                """
                OWNERFILE="owner"
                LOCK_OWNED=1
                proc_start_time() { printf '%s\\n' 12345; }
                """
            )
            + self.lock_function_blocks()
            + extra_functions
            + textwrap.dedent(
                f"""
                base="${{TMPDIR:-/tmp}}/routerkit-release-lock-$$"
                LOCKDIR="$base/lock"
                mkdir -p "$base"
                {setup}
                release_lock
                rc="$?"
                if [ -L "$LOCKDIR" ]; then is_link=yes; else is_link=no; fi
                if [ -e "$LOCKDIR" ]; then exists=yes; else exists=no; fi
                if [ -d "$LOCKDIR" ] && [ ! -L "$LOCKDIR" ]; then is_dir=yes; else is_dir=no; fi
                if [ -f "$LOCKDIR" ] && [ ! -L "$LOCKDIR" ]; then is_file=yes; else is_file=no; fi
                if [ -p "$LOCKDIR" ]; then is_fifo=yes; else is_fifo=no; fi
                if [ -f "$LOCKDIR/$OWNERFILE" ] && [ ! -L "$LOCKDIR/$OWNERFILE" ]; then owner_file=yes; else owner_file=no; fi
                if [ -L "$LOCKDIR/$OWNERFILE" ]; then owner_link=yes; else owner_link=no; fi
                if [ -d "$base/target" ]; then target_dir=yes; else target_dir=no; fi
                if [ -f "$base/target" ]; then target_file=yes; else target_file=no; fi
                printf 'release_rc=%s\\n' "$rc"
                printf 'LOCK_OWNED=%s\\n' "$LOCK_OWNED"
                printf 'is_link=%s\\n' "$is_link"
                printf 'exists=%s\\n' "$exists"
                printf 'is_dir=%s\\n' "$is_dir"
                printf 'is_file=%s\\n' "$is_file"
                printf 'is_fifo=%s\\n' "$is_fifo"
                printf 'owner_file=%s\\n' "$owner_file"
                printf 'owner_link=%s\\n' "$owner_link"
                printf 'target_dir=%s\\n' "$target_dir"
                printf 'target_file=%s\\n' "$target_file"
                if [ -L "$LOCKDIR" ]; then
                    rm -f "$LOCKDIR"
                elif [ -d "$LOCKDIR" ]; then
                    rm -f "$LOCKDIR/$OWNERFILE"
                    rmdir "$LOCKDIR" 2>/dev/null || true
                elif [ -e "$LOCKDIR" ]; then
                    rm -f "$LOCKDIR"
                fi
                rm -f "$base/target" "$base/owner-target" 2>/dev/null || true
                rmdir "$base/target" "$base" 2>/dev/null || true
                exit 0
                """
            )
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed

    def parse_kv(self, stdout):
        result = {}
        for line in stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                result[key] = value
        return result

    def function_block(self, name):
        marker = f"{name}() {{"
        start = self.text.index("\n" + marker) + 1
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
