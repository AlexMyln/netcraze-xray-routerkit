import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import routerkit_devices as devices


class DeviceDiscoveryExecutionGuardTests(unittest.TestCase):
    def test_fixture_first_module_has_no_live_execution_primitive(self):
        source_path = SCRIPTS / "routerkit_devices.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(source_path))

        imported_modules = set()
        imported_names = set()
        called_names = set()
        attribute_names = set()
        string_literals = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_modules.add(node.module.split(".", 1)[0])
                imported_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Name):
                    called_names.add(target.id)
                elif isinstance(target, ast.Attribute):
                    attribute_names.add(target.attr)
                    if isinstance(target.value, ast.Name):
                        called_names.add("%s.%s" % (target.value.id, target.attr))
            elif isinstance(node, ast.Attribute):
                attribute_names.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                string_literals.add(node.value)

        self.assertFalse({"subprocess", "threading", "queue", "signal", "socket", "http", "urllib"} & imported_modules)
        self.assertFalse({"Popen", "run"} & imported_names)
        self.assertFalse({"Popen", "run", "system", "spawn", "execv", "execve", "killpg"} & called_names)
        self.assertFalse({"Popen", "run", "killpg"} & attribute_names)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("BoundedCommandRunner", source)
        self.assertNotIn("CommandResult", source)
        self.assertNotIn("CommandExecutionError", source)
        self.assertNotIn("assignment_stable", source)
        self.assertFalse(
            any(
                "ROUTERKIT" in literal and ("ENABLE" in literal or "ADAPTER" in literal or "VENDOR" in literal)
                for literal in string_literals
            )
        )


class InventoryFileTests(unittest.TestCase):
    def write_private(self, directory, name, data):
        path = Path(directory) / name
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8")
        if os.name == "posix":
            path.chmod(0o600)
        return path

    def test_private_inventory_rejects_invalid_encoding_and_oversize(self):
        with tempfile.TemporaryDirectory() as directory:
            bad_encoding = self.write_private(directory, "bad.json", b"\xff")
            with self.assertRaises(devices.DeviceDiscoveryError) as caught:
                devices.load_result_from_inventory_file(bad_encoding)
            self.assertEqual(caught.exception.state, devices.STATE_MALFORMED_OUTPUT)

            oversized = self.write_private(directory, "large.json", "{}")
            with mock.patch.object(devices, "MAX_INVENTORY_BYTES", 1):
                with self.assertRaises(devices.DeviceDiscoveryError) as caught:
                    devices.load_result_from_inventory_file(oversized)
            self.assertEqual(caught.exception.state, devices.STATE_OUTPUT_TOO_LARGE)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_private_inventory_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.write_private(directory, "target.json", "{}")
            link = Path(directory) / "link.json"
            link.symlink_to(target)

            with self.assertRaises(devices.PrivateFileError):
                devices.read_private_inventory_file(link)

    @unittest.skipUnless(hasattr(os, "link"), "hard links are unavailable")
    def test_private_inventory_rejects_hardlink(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.write_private(directory, "source.json", "{}")
            hardlink = Path(directory) / "hardlink.json"
            os.link(source, hardlink)

            with self.assertRaises(devices.PrivateFileError):
                devices.read_private_inventory_file(source)

    def test_contract_pending_adapter_never_collects(self):
        adapter = devices.ContractPendingAdapter()
        result = adapter.result()

        self.assertEqual(result.adapter_state, devices.STATE_CONTRACT_UNVERIFIED)
        with self.assertRaises(devices.DeviceDiscoveryError) as caught:
            adapter.collect()
        self.assertEqual(caught.exception.state, devices.STATE_CONTRACT_UNVERIFIED)


if __name__ == "__main__":
    unittest.main()
