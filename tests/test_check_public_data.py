import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_public_data.py"
SPEC = importlib.util.spec_from_file_location("check_public_data", SCRIPT)
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)
NOREPLY = "12345+fixture@users.noreply.github.com"


class PublicDataGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="Fixture", GIT_COMMITTER_NAME="Fixture",
                        GIT_AUTHOR_EMAIL=NOREPLY, GIT_COMMITTER_EMAIL=NOREPLY)
        self.git("init", "-q", "--initial-branch=main")
        self.write("README.md", b"Synthetic public repository.\n")
        self.commit()

    def git(self, *args, env=None):
        return subprocess.check_output(["git", "-C", str(self.root), *args],
                                       env=env or self.env, stderr=subprocess.DEVNULL)

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def commit(self, env=None, message="Synthetic fixture"):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message, env=env)

    def rules(self):
        return {rule for oid, line, rule in GUARD.scan(self.root)[0]}

    def token(self):
        return ("gh" + "p_" + "A" * 36).encode()

    def test_clean_repo_and_reserved_example_email(self):
        self.write("examples.txt", b"operator@example.com\n" + NOREPLY.encode())
        self.commit()
        self.assertEqual(self.rules(), set())

    def test_provider_token_deleted_from_tip_still_fails(self):
        self.write("notes.txt", self.token())
        self.commit()
        (self.root / "notes.txt").unlink()
        self.commit()
        self.assertIn("github-token", self.rules())

    def test_other_branch_is_scanned(self):
        self.git("switch", "-q", "-c", "other")
        self.write("notes.txt", self.token())
        self.commit()
        self.git("switch", "-q", "main")
        self.assertIn("github-token", self.rules())

    def test_tag_only_history_is_scanned(self):
        self.write("notes.txt", self.token())
        self.commit()
        self.git("tag", "fixture")
        self.git("reset", "--hard", "HEAD~1")
        self.assertIn("github-token", self.rules())

    def test_forced_ignored_files_cannot_bypass_guard(self):
        self.write(".gitignore", b".env*\n")
        self.commit()
        self.write(".env.local", b"PRIVATE=synthetic\n")
        self.git("add", "-f", ".env.local")
        self.assertIn("forbidden-path", self.rules())

    def test_forbidden_historical_alias_of_safe_blob(self):
        self.write("safe.txt", b"Public placeholder\n")
        self.commit()
        self.write("id_ed25519", b"Public placeholder\n")
        self.commit()
        (self.root / "id_ed25519").unlink()
        self.commit()
        self.assertIn("forbidden-path", self.rules())

    def test_binary_is_not_skipped(self):
        self.write("opaque.bin", b"\x89PNG\x00\xff" + self.token())
        self.commit()
        self.assertIn("github-token", self.rules())

    def test_renamed_archive_is_rejected(self):
        self.write("opaque.bin", b"PK\x03\x04" + b"synthetic archive")
        self.commit()
        self.assertIn("archive-content", self.rules())

    def test_utf16_secret_is_detected(self):
        self.write("export.txt", self.token().decode().encode("utf-16"))
        self.commit()
        self.assertIn("github-token", self.rules())

    def test_index_and_working_copy_are_both_scanned(self):
        self.write("notes.txt", self.token())
        self.git("add", "notes.txt")
        self.write("notes.txt", b"Safe unstaged replacement\n")
        self.assertIn("github-token", self.rules())
        self.git("reset", "-q")
        self.write("README.md", self.token())
        self.assertIn("working-github-token", self.rules())

    def test_ignored_untracked_private_input_is_not_published(self):
        self.write(".gitignore", b".env*\n")
        self.commit()
        self.write(".env.local", self.token())
        self.assertEqual(self.rules(), set())

    def test_personal_author_or_committer_email_fails(self):
        personal = "fixture" + "@mailbox.invalid"
        for key in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
            with self.subTest(key=key):
                env = dict(self.env, **{key: personal})
                self.write("notes.txt", key.encode())
                self.commit(env=env)
                self.assertIn("commit-email-must-be-github-noreply", self.rules())

    def test_commit_message_is_scanned(self):
        self.write("notes.txt", b"Public\n")
        self.commit(message=self.token().decode())
        self.assertIn("github-token", self.rules())

    def test_annotated_tag_identity_and_message_are_scanned(self):
        env = dict(self.env, GIT_COMMITTER_EMAIL="fixture" + "@mailbox.invalid")
        self.git("tag", "-a", "fixture", "-m", self.token().decode(), env=env)
        rules = self.rules()
        self.assertIn("tag-email-must-be-github-noreply", rules)
        self.assertIn("github-token", rules)

    def test_shallow_clone_fails_closed(self):
        clone = Path(self.temp.name) / "shallow"
        subprocess.check_call(["git", "clone", "-q", "--depth=1", self.root.as_uri(), str(clone)], env=self.env)
        with self.assertRaises(GUARD.GuardError):
            GUARD.scan(clone)

    def test_oversized_blob_fails_closed(self):
        self.write("opaque.bin", b"A" * 256)
        self.commit()
        old = GUARD.MAX_BLOB_BYTES
        GUARD.MAX_BLOB_BYTES = 128
        try:
            self.assertIn("oversized-blob", self.rules())
        finally:
            GUARD.MAX_BLOB_BYTES = old

    def test_diagnostics_never_print_secret_or_filename(self):
        token = self.token()
        self.write(token.decode() + ".txt", token)
        self.commit()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(GUARD.main(["--repo", str(self.root)]), 1)
        self.assertNotIn(token.decode(), output.getvalue())
        self.assertIn("[REDACTED]", output.getvalue())

    def test_key_profile_and_assignment_rules(self):
        cases = {
            "private-key": b"-----BEGIN " + b"OPENSSH PRIVATE KEY-----",
            "vless-profile": b"vless" + b"://" + b"fixture@host.invalid",
            "reality-query": b"?p" + b"bk=" + b"A" * 43,
            "credential-assignment": b'api_key="' + b"B" * 32 + b'"',
            "aws-access-key": b"AK" + b"IA" + b"A" * 16,
        }
        for rule, value in cases.items():
            with self.subTest(rule=rule):
                self.assertIn(rule, {name for name, line in GUARD.content_findings(value)})

    def test_ignore_and_filename_guard_cover_private_inputs(self):
        actual_ignore = SCRIPT.parents[1] / ".gitignore"
        self.write(".gitignore", actual_ignore.read_bytes())
        for name in (".env", "local/.env.production", "private.env", "id_ed25519", "client.pem",
                     "vpn.key", "client.p12", "client.pfx", "secrets/input.txt", "backup.zip",
                     ".aws/credentials", "opt/etc/xray/config.json"):
            with self.subTest(name=name):
                self.assertTrue(GUARD.forbidden_path(name))
                self.assertEqual(self.git("check-ignore", "--", name).decode().strip(), name)


if __name__ == "__main__":
    unittest.main()
