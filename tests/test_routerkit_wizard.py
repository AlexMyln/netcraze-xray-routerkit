import contextlib
import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "routerkit-wizard.py"
    spec = importlib.util.spec_from_file_location("routerkit_wizard", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


wizard = load_module()


class WriteProfilesTests(unittest.TestCase):
    def test_writes_valid_json_private_file_with_expected_fields(self):
        profiles = [
            {
                "name": "alpha",
                "port": 1082,
                "subscription_url_env": "ALPHA_SUB_URL",
                "select": {"index": 0, "require_security": "reality", "require_network": "tcp"},
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.json"
            wizard.write_profiles(path, profiles)

            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data, {"profiles": profiles})

            mode = stat.S_IMODE(path.stat().st_mode)
            if os.name == "posix":
                self.assertEqual(mode, 0o600)


class WizardCliTests(unittest.TestCase):
    def test_no_generator_prompt_writes_profiles_without_prompt_or_subprocess(self):
        profiles = [
            {
                "name": "alpha",
                "port": 1082,
                "subscription_url_env": "SYNTHETIC_SUB_URL",
                "select": {"index": 0, "require_security": "reality", "require_network": "tcp"},
            }
        ]
        stdout = io.StringIO()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.json"
            with mock.patch.object(wizard, "build_profiles", return_value=profiles):
                with mock.patch.object(wizard, "ask_yes_no", return_value=True) as ask_yes_no:
                    with mock.patch.object(wizard.subprocess, "run") as run:
                        with contextlib.redirect_stdout(stdout):
                            code = wizard.main(["--profiles", str(path), "--no-generator-prompt"])

            self.assertEqual(code, 0)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"profiles": profiles})

        run.assert_not_called()
        ask_yes_no.assert_called_once()
        self.assertNotIn("Run the local generator now?", stdout.getvalue())


class SourceTypeTests(unittest.TestCase):
    def test_detects_hidden_url(self):
        self.assertEqual(wizard.source_type({"subscription_url": "https://example.net/sub"}), "hidden URL")

    def test_detects_environment_variable(self):
        self.assertEqual(wizard.source_type({"subscription_url_env": "ALPHA_SUB_URL"}), "environment variable")

    def test_detects_local_file(self):
        self.assertEqual(wizard.source_type({"subscription_file": "/tmp/example-subscription.txt"}), "local file")


class SelectorSummaryTests(unittest.TestCase):
    def test_index_summary(self):
        self.assertEqual(wizard.selector_summary({"index": 2}), "index 2")

    def test_name_contains_summary_masks_value(self):
        self.assertEqual(wizard.selector_summary({"name_contains": "alpha-node"}), "name contains al...de")

    def test_host_contains_summary_masks_value(self):
        self.assertEqual(wizard.selector_summary({"host_contains": "example.net"}), "host contains ex...et")


class ValidationRegexTests(unittest.TestCase):
    def test_accepts_valid_profile_names(self):
        valid = ["alpha", "alpha-1", "alpha_1", "a0", "0alpha"]

        for name in valid:
            with self.subTest(name=name):
                self.assertIsNotNone(wizard.NAME_RE.match(name))

    def test_rejects_invalid_profile_names(self):
        invalid = ["", "Alpha", "-alpha", "_alpha", "alpha.example", "alpha example"]

        for name in invalid:
            with self.subTest(name=name):
                self.assertIsNone(wizard.NAME_RE.match(name))


if __name__ == "__main__":
    unittest.main()
