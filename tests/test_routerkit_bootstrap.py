import contextlib
import importlib.util
import io
import json
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "manifests" / "xray-artifacts.json"
FIXTURES = ROOT / "tests" / "fixtures" / "bootstrap"


def load_module():
    path = ROOT / "scripts" / "routerkit-bootstrap.py"
    spec = importlib.util.spec_from_file_location("routerkit_bootstrap", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bootstrap = load_module()


def manifest_data():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def run_main(*args):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = bootstrap.main(list(args))
    return code, stdout.getvalue(), stderr.getvalue()


class ManifestValidationTests(unittest.TestCase):
    def test_valid_manifest_loads(self):
        data = bootstrap.load_manifest(MANIFEST_PATH)
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["upstream"]["release_tag"], "v26.3.27")

    def test_malformed_checksum_rejected(self):
        data = manifest_data()
        data["artifacts"]["linux-arm64"]["sha256"] = "not-a-checksum"
        with self.assertRaises(bootstrap.ManifestValidationError):
            bootstrap.validate_manifest(data)

    def test_uppercase_noncanonical_checksum_rejected(self):
        data = manifest_data()
        data["artifacts"]["linux-arm64"]["sha256"] = data["artifacts"][
            "linux-arm64"
        ]["sha256"].upper()
        with self.assertRaises(bootstrap.ManifestValidationError):
            bootstrap.validate_manifest(data)

    def test_latest_url_rejected(self):
        data = manifest_data()
        data["artifacts"]["linux-arm64"]["download_url"] = (
            "https://github.com/XTLS/Xray-core/releases/latest/download/"
            "Xray-linux-arm64-v8a.zip"
        )
        with self.assertRaises(bootstrap.ManifestValidationError):
            bootstrap.validate_manifest(data)

    def test_wrong_github_repository_url_rejected(self):
        data = manifest_data()
        data["artifacts"]["linux-arm64"]["download_url"] = data["artifacts"][
            "linux-arm64"
        ]["download_url"].replace("XTLS/Xray-core", "example/not-xray")
        with self.assertRaises(bootstrap.ManifestValidationError):
            bootstrap.validate_manifest(data)

    def test_version_tag_mismatch_rejected(self):
        data = manifest_data()
        data["upstream"]["release_tag"] = "v1.2.3"
        with self.assertRaises(bootstrap.ManifestValidationError):
            bootstrap.validate_manifest(data)

    def test_duplicate_machine_alias_rejected(self):
        data = manifest_data()
        data["artifacts"]["linux-arm64"]["uname_machines"] = [
            "aarch64",
            "arm64",
            "aarch64",
        ]
        with self.assertRaises(bootstrap.ManifestValidationError):
            bootstrap.validate_manifest(data)

    def test_missing_architecture_mapping_rejected(self):
        data = manifest_data()
        del data["artifacts"]["linux-arm64"]
        with self.assertRaises(bootstrap.ManifestValidationError):
            bootstrap.validate_manifest(data)


class ArtifactResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = bootstrap.load_manifest(MANIFEST_PATH)

    def test_aarch64_resolves_to_linux_arm64(self):
        key, _ = bootstrap.resolve_artifact(self.manifest, "Linux", "aarch64")
        self.assertEqual(key, "linux-arm64")

    def test_arm64_resolves_to_linux_arm64(self):
        key, _ = bootstrap.resolve_artifact(self.manifest, "Linux", "arm64")
        self.assertEqual(key, "linux-arm64")

    def test_x86_64_rejected(self):
        with self.assertRaises(bootstrap.UnsupportedEnvironmentError):
            bootstrap.resolve_artifact(self.manifest, "Linux", "x86_64")

    def test_mipsel_rejected(self):
        with self.assertRaises(bootstrap.UnsupportedEnvironmentError):
            bootstrap.resolve_artifact(self.manifest, "Linux", "mipsel")


class PlannerTests(unittest.TestCase):
    EXPECTED_TOOL_PACKAGES = {
        "curl": "curl",
        "unzip": "unzip",
        "sha256sum": "coreutils-sha256sum",
        "python3": "python3",
    }

    def test_later_commands_have_exact_package_mapping(self):
        self.assertEqual(bootstrap.LATER_TOOL_PACKAGES, self.EXPECTED_TOOL_PACKAGES)
        self.assertEqual(
            tuple(bootstrap.LATER_TOOL_PACKAGES),
            bootstrap.LATER_COMMANDS,
        )

    def test_required_packages_include_base_and_every_mapped_package_once(self):
        expected = ("ca-bundle", *self.EXPECTED_TOOL_PACKAGES.values())
        self.assertEqual(bootstrap.LATER_PACKAGES, expected)
        self.assertEqual(
            len(bootstrap.LATER_PACKAGES), len(set(bootstrap.LATER_PACKAGES))
        )

    def test_inventory_file_mode_performs_no_subprocesses(self):
        with mock.patch.object(
            bootstrap.subprocess, "run", side_effect=AssertionError("must not execute")
        ) as run:
            code, _, _ = run_main(
                "--inventory-file", str(FIXTURES / "supported-aarch64.json")
            )
        self.assertEqual(code, 0)
        run.assert_not_called()

    def test_supported_complete_inventory_returns_zero(self):
        for name in (
            "supported-aarch64.json",
            "supported-arm64.json",
            "existing-xray.json",
        ):
            with self.subTest(name=name):
                code, output, errors = run_main(
                    "--inventory-file", str(FIXTURES / name)
                )
                self.assertEqual(code, 0)
                self.assertEqual(errors, "")
                self.assertIn("RouterKit bootstrap plan", output)
                self.assertNotIn("Warnings:", output)

    def test_supported_missing_prerequisites_warns_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = list(Path(tmp).iterdir())
            code, output, _ = run_main(
                "--inventory-file",
                str(FIXTURES / "supported-missing-prerequisites.json"),
            )
            after = list(Path(tmp).iterdir())
        self.assertEqual(code, 0)
        self.assertEqual(before, after)
        self.assertIn("Warnings:", output)
        self.assertIn(
            "commands needed by a later apply stage are missing: curl, unzip, sha256sum",
            output,
        )
        self.assertIn(
            "Entware packages needed by later setup stages are missing or unconfirmed: "
            "ca-bundle, curl, unzip, coreutils-sha256sum",
            output,
        )
        self.assertIn("Would NOT in this release:", output)

    def test_text_output_exposes_command_package_mapping(self):
        code, output, errors = run_main(
            "--inventory-file", str(FIXTURES / "supported-aarch64.json")
        )
        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        self.assertIn("later command packages:", output)
        self.assertIn("sha256sum -> coreutils-sha256sum", output)
        self.assertIn("base packages: ca-bundle", output)

    def test_existing_xray_is_reported_without_modification(self):
        code, output, _ = run_main(
            "--inventory-file", str(FIXTURES / "existing-xray.json")
        )
        self.assertEqual(code, 0)
        self.assertIn("existing Xray: present", output)
        self.assertIn("Xray 25.1.30", output)

    def test_text_output_does_not_include_ignored_synthetic_secret_marker(self):
        code, output, errors = run_main(
            "--inventory-file", str(FIXTURES / "supported-aarch64.json")
        )
        self.assertEqual(code, 0)
        self.assertNotIn("SYNTHETIC_SECRET_MARKER", output + errors)

    def test_json_output_is_valid_and_deterministic(self):
        args = (
            "--inventory-file",
            str(FIXTURES / "supported-aarch64.json"),
            "--json",
        )
        first = run_main(*args)
        second = run_main(*args)
        self.assertEqual(first, second)
        self.assertEqual(first[0], 0)
        parsed = json.loads(first[1])
        self.assertEqual(parsed["mode"], "read-only")
        self.assertEqual(parsed["pinned_artifact"]["key"], "linux-arm64")
        requirements = parsed["requirements"]
        self.assertEqual(
            requirements["later_command_packages"], self.EXPECTED_TOOL_PACKAGES
        )
        self.assertEqual(
            requirements["later_commands_required_by_later_stages"],
            list(self.EXPECTED_TOOL_PACKAGES),
        )
        self.assertEqual(
            requirements["entware_packages_required_by_later_stages"],
            ["ca-bundle", *self.EXPECTED_TOOL_PACKAGES.values()],
        )

    def test_inventory_file_plan_cannot_install_download_or_write(self):
        with mock.patch.object(
            bootstrap.subprocess, "run", side_effect=AssertionError("must not execute")
        ) as run, mock.patch.object(
            Path, "write_text", side_effect=AssertionError("must not write")
        ) as write_text, mock.patch.object(
            Path, "write_bytes", side_effect=AssertionError("must not write")
        ) as write_bytes, mock.patch(
            "urllib.request.urlopen", side_effect=AssertionError("must not download")
        ) as urlopen:
            code, output, errors = run_main(
                "--inventory-file", str(FIXTURES / "supported-aarch64.json")
            )
        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        self.assertIn("read-only planning", output)
        run.assert_not_called()
        write_text.assert_not_called()
        write_bytes.assert_not_called()
        urlopen.assert_not_called()

    def test_unsupported_inventory_returns_one(self):
        for name in ("unsupported-x86_64.json", "unsupported-mipsel.json"):
            with self.subTest(name=name):
                code, output, errors = run_main(
                    "--inventory-file", str(FIXTURES / name)
                )
                self.assertEqual(code, 1)
                self.assertEqual(output, "")
                self.assertIn("unsupported environment", errors)

    def test_yes_without_apply_returns_two_before_manifest_or_subprocess_access(self):
        with mock.patch.object(
            bootstrap, "load_manifest", side_effect=AssertionError("must not load")
        ) as load_manifest, mock.patch.object(
            bootstrap.subprocess, "run", side_effect=AssertionError("must not execute")
        ) as run:
            code, output, errors = run_main("--yes")
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertEqual(errors, bootstrap.YES_REQUIRES_APPLY + "\n")
        load_manifest.assert_not_called()
        run.assert_not_called()

    def test_inventory_file_apply_is_rejected_before_inventory_access(self):
        with mock.patch.object(
            bootstrap, "load_inventory_file", side_effect=AssertionError("must not load")
        ) as load_inventory:
            code, output, errors = run_main(
                "--apply",
                "--inventory-file",
                str(FIXTURES / "supported-aarch64.json"),
            )
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertEqual(errors, bootstrap.INVENTORY_APPLY_CONFLICT + "\n")
        load_inventory.assert_not_called()

    def test_apply_refusal_starts_no_package_network_or_transaction_action(self):
        inventory = json.loads(
            (FIXTURES / "supported-aarch64.json").read_text(encoding="utf-8")
        )
        import routerkit_bootstrap_apply as apply_module

        with mock.patch.object(bootstrap, "collect_inventory", return_value=inventory), mock.patch.object(
            apply_module, "validate_apply_environment"
        ), mock.patch.object(apply_module, "resolve_opkg"), mock.patch.object(
            apply_module,
            "apply_bootstrap_transaction",
            side_effect=AssertionError("must not apply"),
        ) as transaction, mock.patch("builtins.input", return_value=""):
            code, output, errors = run_main("--apply")
        self.assertEqual(code, 1)
        self.assertIn("RouterKit bootstrap apply", output)
        self.assertIn("apply declined", errors)
        transaction.assert_not_called()

    def test_apply_confirmation_ctrl_c_starts_no_transaction_action(self):
        inventory = json.loads(
            (FIXTURES / "supported-aarch64.json").read_text(encoding="utf-8")
        )
        import routerkit_bootstrap_apply as apply_module

        with mock.patch.object(
            bootstrap, "collect_inventory", return_value=inventory
        ), mock.patch.object(
            apply_module, "validate_apply_environment"
        ), mock.patch.object(
            apply_module, "resolve_opkg"
        ), mock.patch.object(
            apply_module,
            "apply_bootstrap_transaction",
            side_effect=AssertionError("must not apply"),
        ) as transaction, mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            code, output, errors = run_main("--apply")

        self.assertEqual(code, 1)
        self.assertIn("RouterKit bootstrap apply", output)
        self.assertIn("apply cancelled", errors)
        transaction.assert_not_called()

    def test_apply_dry_run_has_no_confirmation_or_transaction(self):
        inventory = json.loads(
            (FIXTURES / "supported-aarch64.json").read_text(encoding="utf-8")
        )
        import routerkit_bootstrap_apply as apply_module

        with mock.patch.object(bootstrap, "collect_inventory", return_value=inventory), mock.patch.object(
            apply_module, "validate_apply_environment"
        ), mock.patch.object(apply_module, "resolve_opkg"), mock.patch.object(
            apply_module,
            "apply_bootstrap_transaction",
            side_effect=AssertionError("must not apply"),
        ) as transaction, mock.patch(
            "builtins.input", side_effect=AssertionError("must not prompt")
        ):
            code, output, errors = run_main("--apply", "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        self.assertIn("no-write apply preview", output)
        transaction.assert_not_called()

    def test_verified_signal_recovery_uses_conventional_exit_and_precise_message(self):
        inventory = json.loads(
            (FIXTURES / "supported-aarch64.json").read_text(encoding="utf-8")
        )
        import routerkit_bootstrap_apply as apply_module

        termination = apply_module.BootstrapTermination(
            getattr(signal, "SIGTERM", 15)
        )
        termination.recovery_verified = True
        with mock.patch.object(
            bootstrap, "collect_inventory", return_value=inventory
        ), mock.patch.object(
            apply_module, "validate_apply_environment"
        ), mock.patch.object(
            apply_module, "validate_existing_target_metadata"
        ), mock.patch.object(
            apply_module, "resolve_opkg"
        ), mock.patch.object(
            apply_module, "apply_bootstrap_transaction", side_effect=termination
        ):
            code, output, errors = run_main("--apply", "--yes")

        self.assertEqual(code, 128 + getattr(signal, "SIGTERM", 15))
        self.assertIn("verified binary recovery", errors)
        self.assertNotIn("Bootstrap apply result", output)

    def test_json_rollback_failure_has_no_success_result_object(self):
        inventory = json.loads(
            (FIXTURES / "supported-aarch64.json").read_text(encoding="utf-8")
        )
        import routerkit_bootstrap_apply as apply_module

        failure = apply_module.BootstrapRollbackError(
            "Signal-time replacement recovery could not be proven; backup: /synthetic/backup."
        )
        with mock.patch.object(
            bootstrap, "collect_inventory", return_value=inventory
        ), mock.patch.object(
            apply_module, "validate_apply_environment"
        ), mock.patch.object(
            apply_module, "validate_existing_target_metadata"
        ), mock.patch.object(
            apply_module, "resolve_opkg"
        ), mock.patch.object(
            apply_module, "apply_bootstrap_transaction", side_effect=failure
        ):
            code, output, errors = run_main("--apply", "--yes", "--json")

        self.assertEqual(code, 3)
        self.assertEqual(output, "")
        self.assertIn("could not be proven", errors)
        self.assertNotIn('"mode"', errors)

    def test_dry_run_is_explicitly_read_only(self):
        code, output, _ = run_main(
            "--inventory-file",
            str(FIXTURES / "supported-aarch64.json"),
            "--dry-run",
        )
        self.assertEqual(code, 0)
        self.assertIn("default and --dry-run perform the same non-mutating checks", output)


if __name__ == "__main__":
    unittest.main()
