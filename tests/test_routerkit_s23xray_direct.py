import subprocess
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
        self.assertIn("trap 'release_lock' EXIT", self.text)
        self.assertIn("trap 'release_lock; exit 130' INT", self.text)
        self.assertNotIn("rm -rf", self.text)

    def test_pid_publication_uses_lock_directory_temp_and_cleans_child(self):
        self.assertIn('tmp="$LOCKDIR/xray-direct.pid.$$"', self.text)
        self.assertIn('publish_pid "$child"', self.text)
        self.assertIn('terminate_direct_child "$child"', self.text)
        self.assertNotIn('$PIDFILE.$$', self.text)


if __name__ == "__main__":
    unittest.main()
