import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import routerkit_devices as devices


ALLOWED_FIXTURE_MODULE_IMPORTS = {
    "__future__",
    "argparse",
    "dataclasses",
    "hashlib",
    "hmac",
    "ipaddress",
    "json",
    "os",
    "pathlib",
    "re",
    "routerkit_private_io",
    "secrets",
    "stat",
    "sys",
    "typing",
}

OS_EXECUTION_ATTRIBUTES = {
    "execv",
    "execve",
    "execvp",
    "execvpe",
    "fork",
    "forkpty",
    "killpg",
    "popen",
    "posix_spawn",
    "posix_spawnp",
    "spawnl",
    "spawnle",
    "spawnlp",
    "spawnlpe",
    "spawnv",
    "spawnve",
    "spawnvp",
    "spawnvpe",
    "startfile",
    "system",
}

PROCESS_THREAD_MODULES = {
    "asyncio.subprocess",
    "multiprocessing",
    "queue",
    "signal",
    "subprocess",
    "threading",
}

NETWORK_MODULES = {
    "ftplib",
    "http.client",
    "paramiko",
    "requests",
    "socket",
    "telnetlib",
    "urllib.request",
}

DYNAMIC_EXECUTION_NAMES = {"__import__", "compile", "eval", "exec"}
DYNAMIC_NAMESPACE_NAMES = {"__builtins__", "globals", "locals", "vars"}

CANDIDATE_COMMAND_LITERALS = (
    "show ip dhcp bindings",
    "show associations",
    "show ip hotspot summary",
    "show ip arp",
    "/rci",
    "ssh",
    "telnet",
    "curl",
    "wget",
    "nmap",
)

SHELL_LITERAL_INDICATORS = (
    "shell=true",
    "/bin/sh",
    "/bin/bash",
    "sh -c",
    "bash -c",
    "cmd.exe",
    "powershell",
)

LEGACY_EXECUTION_MARKERS = {
    "BoundedCommandRunner",
    "CommandExecutionError",
    "CommandResult",
}


def _matches_namespace(name: str, namespaces: Set[str]) -> bool:
    return any(name == namespace or name.startswith(namespace + ".") for namespace in namespaces)


def _qualified_name(node: ast.AST, module_aliases: Dict[str, str]) -> Optional[str]:
    if isinstance(node, ast.Name):
        return module_aliases.get(node.id)
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value, module_aliases)
        if parent is not None:
            return parent + "." + node.attr
    return None


def _constant_string(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _constant_string(node.left)
        right = _constant_string(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def find_live_execution_guard_violations(source: str) -> List[str]:
    """Return conservative, deterministic violations for the fixture-first module.

    In particular, every built-in ``getattr`` call is rejected because this module
    has no legitimate reflective-access requirement.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return ["syntax-error: line {}: invalid Python source".format(error.lineno or 0)]

    findings: Set[Tuple[str, int, int, str]] = set()
    module_aliases: Dict[str, str] = {}
    imported_bindings: Dict[str, Tuple[str, str]] = {}

    def add(category: str, node: ast.AST, detail: str) -> None:
        findings.add(
            (
                category,
                getattr(node, "lineno", 0),
                getattr(node, "col_offset", 0),
                detail,
            )
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".", 1)[0]
                module_aliases[local_name] = alias.name if alias.asname else local_name
                top_level = alias.name.split(".", 1)[0]
                if top_level not in ALLOWED_FIXTURE_MODULE_IMPORTS:
                    add("forbidden-import", node, "module {!r} is not allowlisted".format(alias.name))
                if alias.name == "importlib" or alias.name.startswith("importlib."):
                    add("dynamic-import", node, "import of {!r}".format(alias.name))
                if _matches_namespace(alias.name, PROCESS_THREAD_MODULES):
                    add("process-thread-import", node, "import of {!r}".format(alias.name))
                if _matches_namespace(alias.name, NETWORK_MODULES):
                    add("network-import", node, "import of {!r}".format(alias.name))

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            top_level = module.split(".", 1)[0]
            if not module or top_level not in ALLOWED_FIXTURE_MODULE_IMPORTS:
                add("forbidden-import", node, "module {!r} is not allowlisted".format(module))
            if module == "importlib" or module.startswith("importlib."):
                add("dynamic-import", node, "import from {!r}".format(module))
            if _matches_namespace(module, PROCESS_THREAD_MODULES):
                add("process-thread-import", node, "import from {!r}".format(module))
            if _matches_namespace(module, NETWORK_MODULES):
                add("network-import", node, "import from {!r}".format(module))

            for alias in node.names:
                if alias.name == "*":
                    add("wildcard-import", node, "wildcard import from {!r}".format(module))
                    continue
                local_name = alias.asname or alias.name
                imported_bindings[local_name] = (module, alias.name)
                qualified = module + "." + alias.name if module else alias.name
                if module == "os" and alias.name in OS_EXECUTION_ATTRIBUTES:
                    add("os-execution-reference", node, "import of {!r}".format(qualified))
                if qualified == "importlib.import_module":
                    add("dynamic-import", node, "import of {!r}".format(qualified))
                if _matches_namespace(qualified, PROCESS_THREAD_MODULES):
                    add("process-thread-import", node, "import of {!r}".format(qualified))
                if _matches_namespace(qualified, NETWORK_MODULES):
                    add("network-import", node, "import of {!r}".format(qualified))

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id in LEGACY_EXECUTION_MARKERS:
                add("legacy-execution-marker", node, "identifier {!r}".format(node.id))
            if not isinstance(node.ctx, ast.Load):
                continue
            if node.id in DYNAMIC_EXECUTION_NAMES:
                add("dynamic-execution", node, "reference to {!r}".format(node.id))
            if node.id in DYNAMIC_NAMESPACE_NAMES:
                add("dynamic-namespace", node, "reference to {!r}".format(node.id))

            binding = imported_bindings.get(node.id)
            if binding is not None:
                module, imported_name = binding
                qualified = module + "." + imported_name if module else imported_name
                if module == "os" and imported_name in OS_EXECUTION_ATTRIBUTES:
                    add("os-execution-reference", node, "reference to {!r}".format(qualified))
                if qualified == "importlib.import_module":
                    add("dynamic-import", node, "reference to {!r}".format(qualified))
                if _matches_namespace(qualified, PROCESS_THREAD_MODULES):
                    add("process-thread-reference", node, "reference to {!r}".format(qualified))
                if _matches_namespace(qualified, NETWORK_MODULES):
                    add("network-reference", node, "reference to {!r}".format(qualified))

        elif isinstance(node, ast.Attribute):
            if node.attr in LEGACY_EXECUTION_MARKERS:
                add("legacy-execution-marker", node, "attribute {!r}".format(node.attr))
            parent = _qualified_name(node.value, module_aliases)
            qualified = _qualified_name(node, module_aliases)
            if parent == "os" and node.attr in OS_EXECUTION_ATTRIBUTES:
                add("os-execution-reference", node, "reference to {!r}".format(qualified))
            if qualified == "importlib.import_module":
                add("dynamic-import", node, "reference to {!r}".format(qualified))
            if qualified is not None and _matches_namespace(qualified, PROCESS_THREAD_MODULES):
                add("process-thread-reference", node, "reference to {!r}".format(qualified))
            if qualified is not None and _matches_namespace(qualified, NETWORK_MODULES):
                add("network-reference", node, "reference to {!r}".format(qualified))

        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in LEGACY_EXECUTION_MARKERS:
                add("legacy-execution-marker", node, "definition {!r}".format(node.name))

        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "getattr":
                add("reflective-access", node, "built-in getattr call is forbidden")
            for keyword in node.keywords:
                if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                    add("shell-indicator", keyword.value, "shell=True")

        literal = _constant_string(node)
        if literal is not None:
            lowered = literal.casefold()
            for candidate in CANDIDATE_COMMAND_LITERALS:
                if candidate in lowered:
                    add("candidate-command", node, "literal contains {!r}".format(candidate))
            for indicator in SHELL_LITERAL_INDICATORS:
                if indicator in lowered:
                    add("shell-indicator", node, "literal contains {!r}".format(indicator))
            if "assignment_stable" in literal:
                add("deprecated-trust-marker", node, "literal contains 'assignment_stable'")
            if any(marker in literal for marker in LEGACY_EXECUTION_MARKERS):
                add("legacy-execution-marker", node, "literal contains an execution marker")
            upper_literal = literal.upper()
            if "ROUTERKIT" in upper_literal and any(
                marker in upper_literal for marker in ("ENABLE", "ADAPTER", "VENDOR")
            ):
                add("adapter-feature-flag", node, "literal can enable a live adapter")

    return [
        "{}: line {}: {}".format(category, line, detail)
        for category, line, _column, detail in sorted(findings)
    ]


class DeviceDiscoveryExecutionGuardTests(unittest.TestCase):
    def test_fixture_first_module_has_no_live_execution_primitive(self):
        source_path = SCRIPTS / "routerkit_devices.py"
        source = source_path.read_text(encoding="utf-8")
        findings = find_live_execution_guard_violations(source)

        self.assertEqual(
            findings,
            [],
            "fixture-first module contains live-execution primitives:\n" + "\n".join(findings),
        )

    def test_guard_mutations_and_safe_controls(self):
        mutations = (
            ("direct forbidden import", "import subprocess", "process-thread-import"),
            ("aliased forbidden import", "import subprocess as process", "process-thread-import"),
            ("imported os alias", "from os import system as shell", "os-execution-reference"),
            ("os.system", "import os\nos.system('echo x')", "os-execution-reference"),
            (
                "aliased os module",
                "import os as operating_system\noperating_system.system('echo x')",
                "os-execution-reference",
            ),
            (
                "assigned os.system alias",
                "import os\nrunner = os.system\nrunner('echo x')",
                "os-execution-reference",
            ),
            ("dunder import", "__import__('subprocess')", "dynamic-execution"),
            (
                "importlib import_module",
                "import importlib\nimportlib.import_module('subprocess')",
                "dynamic-import",
            ),
            (
                "reflective os.system",
                "import os\ngetattr(os, 'system')('echo x')",
                "reflective-access",
            ),
            ("eval", "eval('1 + 1')", "dynamic-execution"),
            ("exec", "exec(\"value = 1\")", "dynamic-execution"),
            ("compile", "compile('1 + 1', '<guard>', 'eval')", "dynamic-execution"),
            (
                "dunder builtins import",
                "__builtins__['__import__']('subprocess')",
                "dynamic-namespace",
            ),
            (
                "local shell helper",
                "import router_shell_helper\nrouter_shell_helper.run('fixture command')",
                "forbidden-import",
            ),
            (
                "candidate command literal",
                "command = 'show ip ' + 'dhcp bindings'",
                "candidate-command",
            ),
            ("rci endpoint literal", "endpoint = '/rci'", "candidate-command"),
            ("shell true", "runner(['fixture'], shell=True)", "shell-indicator"),
            ("sh command literal", "command = 'sh -c fixture'", "shell-indicator"),
            ("network client import", "import socket", "network-import"),
            ("wildcard import", "from os import *", "wildcard-import"),
        )

        for name, source, expected_category in mutations:
            with self.subTest(mutation=name):
                findings = find_live_execution_guard_violations(source)
                self.assertTrue(
                    any(finding.startswith(expected_category + ":") for finding in findings),
                    "{} was not categorized as {}: {}".format(name, expected_category, findings),
                )

        safe_controls = (
            (
                "exact allowed imports",
                "\n".join(
                    (
                        "from __future__ import annotations",
                        "import argparse",
                        "import hashlib",
                        "import hmac",
                        "import ipaddress",
                        "import json",
                        "import os",
                        "import re",
                        "import secrets",
                        "import stat",
                        "import sys",
                        "from dataclasses import dataclass",
                        "from pathlib import Path",
                        "from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple",
                        "from routerkit_private_io import PrivateFileError",
                    )
                ),
            ),
            ("ordinary os.name", "import os\nplatform_name = os.name"),
            ("ordinary os.O_RDONLY", "import os\nflags = os.O_RDONLY"),
            (
                "Path.read_text",
                "from pathlib import Path\ntext = Path('fixture.json').read_text(encoding='utf-8')",
            ),
            ("JSON parsing", "import json\ndata = json.loads('{}')"),
            ("hashing", "import hashlib\ndigest = hashlib.sha256(b'fixture').hexdigest()"),
            (
                "fixture normalization helper",
                "def normalize_fixture(records):\n"
                "    return tuple(sorted(str(record['id']) for record in records))",
            ),
        )

        for name, source in safe_controls:
            with self.subTest(safe_control=name):
                self.assertEqual(find_live_execution_guard_violations(source), [])


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
