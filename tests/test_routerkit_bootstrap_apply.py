import hashlib
import io
import json
import os
import signal
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import routerkit_artifact_network as artifact_network
import routerkit_bootstrap_apply as apply


MANIFEST_PATH = ROOT / "manifests" / "xray-artifacts.json"


def manifest_data():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def write_executable(path, version):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nprintf '%s\\n' 'Xray {}'\n".format(version), encoding="utf-8")
    path.chmod(0o755)


def write_opkg(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = status ]; then\n"
        "  printf '%s\\n' 'Package: synthetic' 'Status: install ok installed'\n"
        "  exit 0\n"
        "fi\n"
        "exit 91\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def synthetic_archive(path, version="26.3.27", extra=None):
    payload = "#!/bin/sh\nprintf '%s\\n' 'Xray {}'\n".format(version).encode("utf-8")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xray", payload)
        for name, value in extra or []:
            archive.writestr(name, value)
    return payload


def local_downloader(source_url, destination, *, expected_url, archive_path):
    if source_url != expected_url:
        raise AssertionError("manifest URL mismatch")
    data = Path(archive_path).read_bytes()
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    return artifact_network.ArtifactDownload(
        byte_count=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        redirect_count=0,
    )


class OpkgPolicyTests(unittest.TestCase):
    def test_required_directory_chain_rejects_intermediate_symlink(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            (root / "sbin").symlink_to(Path(outside))
            with self.assertRaises(apply.BootstrapApplyError):
                apply.validate_apply_environment(root, create=False)

    def test_fixed_opt_scoped_opkg_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_opkg(root / "bin/opkg")
            handle = apply.resolve_opkg(root)
        self.assertEqual(handle.path, root / "bin/opkg")

    def test_symlink_resolving_outside_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            root.joinpath("bin").mkdir()
            external = Path(outside) / "opkg"
            write_opkg(external)
            (root / "bin/opkg").symlink_to(external)
            with self.assertRaises(apply.BootstrapApplyError):
                apply.resolve_opkg(root)

    def test_missing_opkg_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(apply.BootstrapApplyError):
                apply.resolve_opkg(Path(tmp))

    def test_only_missing_fixed_packages_are_installed_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_opkg(root / "bin/opkg")
            handle = apply.resolve_opkg(root)
            installed = {"ca-bundle", "unzip", "python3"}
            commands = []

            def runner(command, **kwargs):
                commands.append(list(command))
                if command[1] == "status":
                    package = command[2]
                    output = b"Status: install ok installed\n" if package in installed else b""
                    return apply.ProcessResult(0 if package in installed else 1, output, b"")
                self.assertEqual(command[1], "install")
                installed.update(command[2:])
                return apply.ProcessResult(0, b"SYNTHETIC_SECRET_MARKER", b"")

            lifecycle = apply.BootstrapSignalLifecycle()
            already, added = apply.ensure_required_packages(
                root, handle, lifecycle=lifecycle, runner=runner
            )

        self.assertEqual(already, ["ca-bundle", "unzip", "python3"])
        self.assertEqual(added, ["curl", "coreutils-sha256sum"])
        installs = [command for command in commands if command[1] == "install"]
        self.assertEqual(
            installs,
            [[str(handle.path), "install", "curl", "coreutils-sha256sum"]],
        )
        self.assertFalse(any("upgrade" in command for command in commands))

    def test_all_present_runs_no_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_opkg(root / "bin/opkg")
            handle = apply.resolve_opkg(root)
            commands = []

            def runner(command, **kwargs):
                commands.append(list(command))
                return apply.ProcessResult(0, b"Status: install ok installed\n", b"")

            _, added = apply.ensure_required_packages(
                root,
                handle,
                lifecycle=apply.BootstrapSignalLifecycle(),
                runner=runner,
            )
        self.assertEqual(added, [])
        self.assertFalse(any(command[1] == "install" for command in commands))


class ArchiveTests(unittest.TestCase):
    def test_extracts_only_root_xray(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_path = root / "archive.zip"
            expected = synthetic_archive(
                archive_path, extra=[("README.md", b"ignored"), ("geoip.dat", b"ignored")]
            )
            candidate = root / "candidate"
            digest = apply.extract_xray_candidate(archive_path, candidate)
            self.assertEqual(candidate.read_bytes(), expected)
            self.assertEqual(digest, hashlib.sha256(expected).hexdigest())
            self.assertTrue(os.access(str(candidate), os.X_OK))

    def test_duplicate_normalized_xray_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "archive.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("xray", b"one")
                archive.writestr("./xray", b"two")
            with self.assertRaises(apply.BootstrapApplyError):
                apply.extract_xray_candidate(archive_path, Path(tmp) / "candidate")

    def test_traversal_absolute_and_backslash_names_are_rejected(self):
        for name in ("../xray", "/xray", "folder\\xray"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                archive_path = Path(tmp) / "archive.zip"
                with zipfile.ZipFile(archive_path, "w") as archive:
                    archive.writestr(name, b"bad")
                with self.assertRaises(apply.BootstrapApplyError):
                    apply.extract_xray_candidate(archive_path, Path(tmp) / "candidate")

    def test_symlink_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "archive.zip"
            info = zipfile.ZipInfo("xray")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(info, b"target")
            with self.assertRaises(apply.BootstrapApplyError):
                apply.extract_xray_candidate(archive_path, Path(tmp) / "candidate")

    def test_entry_count_member_size_and_ratio_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "many.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for index in range(apply.MAX_ZIP_ENTRIES + 1):
                    archive.writestr("entry-{}".format(index), b"x")
            with self.assertRaises(apply.BootstrapApplyError):
                apply.extract_xray_candidate(archive_path, Path(tmp) / "candidate")

        info = mock.Mock(filename="xray", file_size=apply.MAX_XRAY_MEMBER_BYTES + 1)
        info.flag_bits = 0
        info.compress_type = zipfile.ZIP_STORED
        info.external_attr = stat.S_IFREG << 16
        info.is_dir.return_value = False
        with mock.patch.object(zipfile, "ZipFile") as zip_file:
            zip_file.return_value.__enter__.return_value.infolist.return_value = [info]
            with self.assertRaises(apply.BootstrapApplyError):
                apply.extract_xray_candidate(Path("ignored"), Path("candidate"))


class VersionAndReceiptTests(unittest.TestCase):
    def test_exact_pinned_version_only(self):
        self.assertEqual(
            apply.validate_version_output(b"Xray 26.3.27\n", "Xray 26.3.27"),
            "Xray 26.3.27",
        )
        for value in (b"Xray 26.3.28\n", b"Xray 26.3.27 extra\n", b"not xray\n"):
            with self.subTest(value=value), self.assertRaises(apply.BootstrapApplyError):
                apply.validate_version_output(value, "Xray 26.3.27")

    def test_receipt_is_deterministic_private_and_contains_no_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state/bootstrap-state.json"
            receipt = {
                "schema_version": 1,
                "release": "v26.3.27",
                "archive_sha256": "a" * 64,
                "installed_binary_sha256": "b" * 64,
                "installed_version": "Xray 26.3.27",
                "backup_path": None,
                "backup_sha256": None,
                "packages_installed_by_routerkit": [],
            }
            apply._atomic_receipt(path, receipt, target_root=Path(tmp))
            first = path.read_bytes()
            mode = stat.S_IMODE(path.stat().st_mode)
            apply._atomic_receipt(path, receipt, target_root=Path(tmp))
            second = path.read_bytes()
        self.assertEqual(first, second)
        self.assertEqual(mode, 0o600)
        self.assertNotIn(b"http", first)


class TransactionIntegrationTests(unittest.TestCase):
    def make_target(self, directory, *, existing=True):
        root = Path(directory)
        write_opkg(root / "bin/opkg")
        if existing:
            write_executable(root / "sbin/xray", "25.1.30")
        return root

    def make_manifest_and_archive(self, directory, version="26.3.27"):
        archive = Path(directory) / "synthetic.zip"
        synthetic_archive(archive, version=version)
        manifest = manifest_data()
        manifest["artifacts"]["linux-arm64"]["sha256"] = hashlib.sha256(
            archive.read_bytes()
        ).hexdigest()
        return manifest, archive

    def test_existing_binary_is_backed_up_replaced_and_rerun_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_target(tmp)
            manifest, archive = self.make_manifest_and_archive(tmp)
            downloader = lambda *args, **kwargs: local_downloader(
                *args, **kwargs, archive_path=archive
            )

            first = apply.apply_bootstrap_transaction(
                manifest, target_root=root, downloader=downloader
            )
            receipt = json.loads(
                (root / apply.STATE_RELATIVE_PATH).read_text(encoding="utf-8")
            )
            backup = Path(receipt["backup_path"])
            second = apply.apply_bootstrap_transaction(
                manifest,
                target_root=root,
                downloader=mock.Mock(side_effect=AssertionError("must not download")),
            )
            backup_exists = backup.exists()
            backup_bytes = backup.read_bytes()

        self.assertTrue(first.replacement_performed)
        self.assertTrue(first.post_install_verified)
        self.assertEqual(first.backup_created_or_reused, "created")
        self.assertTrue(backup_exists)
        self.assertIn(b"Xray 25.1.30", backup_bytes)
        self.assertTrue(second.idempotent_noop)
        self.assertFalse(second.replacement_performed)

    def test_clean_install_creates_no_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_target(tmp, existing=False)
            manifest, archive = self.make_manifest_and_archive(tmp)
            result = apply.apply_bootstrap_transaction(
                manifest,
                target_root=root,
                downloader=lambda *args, **kwargs: local_downloader(
                    *args, **kwargs, archive_path=archive
                ),
            )
        self.assertFalse(result.existing_binary_present)
        self.assertIsNone(result.backup_path)
        self.assertTrue(result.post_install_verified)

    def test_checksum_failure_leaves_original_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_target(tmp)
            original = (root / "sbin/xray").read_bytes()
            manifest, archive = self.make_manifest_and_archive(tmp)
            manifest["artifacts"]["linux-arm64"]["sha256"] = "0" * 64
            with self.assertRaises(apply.BootstrapApplyError):
                apply.apply_bootstrap_transaction(
                    manifest,
                    target_root=root,
                    downloader=lambda *args, **kwargs: local_downloader(
                        *args, **kwargs, archive_path=archive
                    ),
                )
            self.assertEqual((root / "sbin/xray").read_bytes(), original)
            self.assertFalse((root / apply.BACKUP_RELATIVE_DIR).exists())

    def test_candidate_version_failure_leaves_original_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_target(tmp)
            original = (root / "sbin/xray").read_bytes()
            manifest, archive = self.make_manifest_and_archive(tmp, version="26.3.28")
            with self.assertRaises(apply.BootstrapApplyError):
                apply.apply_bootstrap_transaction(
                    manifest,
                    target_root=root,
                    downloader=lambda *args, **kwargs: local_downloader(
                        *args, **kwargs, archive_path=archive
                    ),
                )
            self.assertEqual((root / "sbin/xray").read_bytes(), original)

    def test_post_install_failure_restores_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_target(tmp)
            original = (root / "sbin/xray").read_bytes()
            manifest, archive = self.make_manifest_and_archive(tmp)
            target_calls = 0

            def runner(command, **kwargs):
                nonlocal target_calls
                if Path(command[0]) == root / "sbin/xray":
                    target_calls += 1
                    if target_calls == 3:
                        return apply.ProcessResult(0, b"Xray wrong\n", b"")
                return apply.run_bounded_process(command, **kwargs)

            with self.assertRaises(apply.BootstrapApplyError):
                apply.apply_bootstrap_transaction(
                    manifest,
                    target_root=root,
                    downloader=lambda *args, **kwargs: local_downloader(
                        *args, **kwargs, archive_path=archive
                    ),
                    runner=runner,
                )
            restored = (root / "sbin/xray").read_bytes()
        self.assertEqual(restored, original)

    def test_termination_during_download_cleans_staging_and_restores_handlers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_target(tmp)
            manifest, _ = self.make_manifest_and_archive(tmp)
            previous = {value: signal.getsignal(value) for value in apply.BootstrapSignalLifecycle.handled_signals()}

            def terminate(*args, **kwargs):
                raise apply.BootstrapTermination(getattr(signal, "SIGTERM", 15))

            with self.assertRaises(apply.BootstrapTermination):
                apply.apply_bootstrap_transaction(
                    manifest, target_root=root, downloader=terminate
                )
            remaining = list((root / apply.STAGING_RELATIVE_DIR).iterdir())
            current = {value: signal.getsignal(value) for value in previous}
        self.assertEqual(remaining, [])
        self.assertEqual(current, previous)


if __name__ == "__main__":
    unittest.main()
