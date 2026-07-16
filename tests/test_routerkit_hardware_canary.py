import ast
import contextlib
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import routerkit_hardware_canary as canary


PACKET_PATH = ROOT / "hardware" / "netcraze-canary-packet.v1.json"
EVIDENCE_SCHEMA_PATH = ROOT / "hardware" / "netcraze-canary-evidence.v1.schema.json"
WRAPPER = SCRIPTS / "routerkit-hardware-canary.py"
PROBE = SCRIPTS / "probe-netcraze-hardware-canary.sh"


FORBIDDEN_IMPORT_ROOTS = {
    "asyncio",
    "ftplib",
    "http",
    "multiprocessing",
    "paramiko",
    "requests",
    "socket",
    "subprocess",
    "telnetlib",
    "threading",
    "urllib",
}

FORBIDDEN_CALL_NAMES = {
    "exec",
    "eval",
    "compile",
    "__import__",
    "open",
}

FORBIDDEN_ATTRIBUTE_CALLS = {
    "write_bytes",
    "write_text",
    "touch",
    "mkdir",
    "unlink",
    "rename",
    "replace",
    "chmod",
}

FORBIDDEN_COMMAND_LITERALS = (
    "show ip dhcp bindings",
    "show associations",
    "show ip hotspot summary",
    "show ip arp",
    "/rci",
    "xkeen -start",
)


def load_packet():
    return json.loads(PACKET_PATH.read_text(encoding="utf-8"))


def find_no_live_violations(source):
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return ["syntax:{}".format(exc.lineno or 0)]
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in FORBIDDEN_IMPORT_ROOTS:
                    violations.append("import:{}".format(alias.name))
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if root in FORBIDDEN_IMPORT_ROOTS:
                violations.append("import:{}".format(node.module))
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALL_NAMES:
                violations.append("call:{}".format(node.func.id))
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in FORBIDDEN_ATTRIBUTE_CALLS
            ):
                violations.append("call:{}".format(node.func.attr))
        elif isinstance(node, ast.Attribute):
            if (
                isinstance(node.value, ast.Name)
                and node.value.id == "os"
                and node.attr in {"environ", "getenv", "system", "popen"}
            ):
                violations.append("os:{}".format(node.attr))
    lowered = source.casefold()
    for literal in FORBIDDEN_COMMAND_LITERALS:
        if literal in lowered:
            violations.append("literal:{}".format(literal))
    return sorted(set(violations))


class PacketContractTests(unittest.TestCase):
    def assert_error_contains(self, packet, text, repo_root=ROOT):
        errors = canary.validate_packet(packet, repo_root)
        self.assertTrue(
            any(text in error for error in errors),
            "{} not found in:\n{}".format(text, "\n".join(errors)),
        )

    def test_committed_packet_is_strict_and_ready(self):
        packet = load_packet()
        self.assertEqual(canary.validate_packet(packet, ROOT), [])
        contract = canary._structural_repository_contract(packet, ROOT)
        self.assertEqual(
            canary.evaluate_offline_hardware_readiness(packet, contract),
            canary.READY,
        )

    def test_exact_schema_baseline_and_phase_ids(self):
        packet = load_packet()
        self.assertEqual(packet["schema"], canary.PACKET_SCHEMA)
        self.assertEqual(packet["released_baseline"], canary.RELEASED_BASELINE)
        self.assertEqual(packet["expected_main"], canary.EXPECTED_MAIN)
        self.assertEqual(
            tuple(phase["id"] for phase in packet["phases"]),
            canary.PHASE_IDS,
        )

    def test_unknown_fields_rejected_at_every_contract_layer(self):
        mutations = []
        packet = load_packet()
        top = copy.deepcopy(packet)
        top["unexpected"] = True
        mutations.append((top, "packet: unknown fields"))
        target = copy.deepcopy(packet)
        target["target_scope"]["planned_model"]["unexpected"] = True
        mutations.append((target, "planned_model: unknown fields"))
        phase = copy.deepcopy(packet)
        phase["phases"][0]["unexpected"] = True
        mutations.append((phase, "phases[0]: unknown fields"))
        check = copy.deepcopy(packet)
        check["phases"][0]["checks"][0]["unexpected"] = True
        mutations.append((check, "checks[0]: unknown fields"))
        for mutated, text in mutations:
            with self.subTest(text=text):
                self.assert_error_contains(mutated, text)

    def test_baseline_mismatch_rejected(self):
        packet = load_packet()
        packet["expected_main"] = "0" * 40
        self.assert_error_contains(packet, "must match the exact released commit")

    def test_missing_duplicate_and_reordered_phase_ids_rejected(self):
        packet = load_packet()
        missing = copy.deepcopy(packet)
        missing["phases"].pop()
        self.assert_error_contains(missing, "must contain exactly 14 phases")

        duplicate = copy.deepcopy(packet)
        duplicate["phases"][1]["id"] = duplicate["phases"][0]["id"]
        self.assert_error_contains(duplicate, "expected P1_READ_ONLY_PLATFORM_INVENTORY")

        reordered = copy.deepcopy(packet)
        reordered["phases"][0], reordered["phases"][1] = (
            reordered["phases"][1],
            reordered["phases"][0],
        )
        self.assert_error_contains(reordered, "expected P0_OPERATOR_PREFLIGHT")

    def test_duplicate_check_ids_rejected(self):
        packet = load_packet()
        packet["phases"][0]["checks"][1]["id"] = packet["phases"][0]["checks"][0]["id"]
        self.assert_error_contains(packet, "duplicate check IDs")

    def test_dependency_cycle_and_missing_dependency_rejected(self):
        packet = load_packet()
        cycle = copy.deepcopy(packet)
        cycle["phases"][0]["dependencies"] = ["P1_READ_ONLY_PLATFORM_INVENTORY"]
        self.assert_error_contains(cycle, "dependency graph contains a cycle")

        missing = copy.deepcopy(packet)
        missing["phases"][1]["dependencies"] = ["P_DOES_NOT_EXIST"]
        self.assert_error_contains(missing, "unknown phases")

    def test_category_timeout_and_authorization_bounds(self):
        packet = load_packet()
        category = copy.deepcopy(packet)
        category["phases"][1]["category"] = "network_probe"
        self.assert_error_contains(category, "invalid category")

        timeout = copy.deepcopy(packet)
        timeout["phases"][1]["hard_timeout_minutes"] = 1
        self.assert_error_contains(timeout, "must be from estimate to 60")

        authorization = copy.deepcopy(packet)
        authorization["phases"][5]["required_operator_authorization"] = "none"
        self.assert_error_contains(
            authorization,
            "requires explicit disposable-write authorization",
        )

    def test_time_budget_and_cleanup_reserve(self):
        packet = load_packet()
        self.assertEqual(
            sum(phase["estimated_minutes"] for phase in packet["phases"]),
            packet["session_budget"]["hard_session_ceiling_minutes"],
        )
        self.assertGreaterEqual(
            packet["phases"][-1]["estimated_minutes"],
            packet["session_budget"]["cleanup_reserve_minutes"],
        )

        reserve = copy.deepcopy(packet)
        reserve["session_budget"]["cleanup_reserve_minutes"] = 14
        self.assert_error_contains(reserve, "must be at least 15")

        overrun = copy.deepcopy(packet)
        overrun["phases"][0]["estimated_minutes"] += 1
        self.assert_error_contains(overrun, "must exactly fill the hard session ceiling")

    def test_required_stop_condition_and_cleanup_route(self):
        packet = load_packet()
        missing = copy.deepcopy(packet)
        missing["stop_conditions"] = [
            item
            for item in missing["stop_conditions"]
            if item["id"] != "S_DEFAULT_POLICY_DELTA"
        ]
        self.assert_error_contains(missing, "missing required IDs")

        route = copy.deepcopy(packet)
        route["stop_conditions"][0]["route_to_phase"] = "P12_FINAL_INVARIANT_AUDIT"
        self.assert_error_contains(route, "must route to cleanup")

    def test_disposable_writes_require_rollback_and_default_audit(self):
        packet = load_packet()
        rollback = copy.deepcopy(packet)
        rollback["phases"][5]["rollback_check_ids"] = []
        self.assert_error_contains(rollback, "requires rollback checks")

        audit = copy.deepcopy(packet)
        audit["phases"][5]["checks"][2]["id"] = "P5_INVARIANT_CHECK"
        self.assert_error_contains(audit, "requires a default-policy audit")

    def test_evidence_mapping_and_secret_like_fields_rejected(self):
        packet = load_packet()
        evidence = copy.deepcopy(packet)
        evidence["phases"][0]["checks"][0]["evidence_category"] = "unmapped"
        self.assert_error_contains(evidence, "not declared by the phase")

        secret = copy.deepcopy(packet)
        secret["target_scope"]["password"] = "synthetic"
        errors = canary.validate_packet(secret, ROOT)
        self.assertTrue(any("prohibited machine-readable field" in error for error in errors))

        live = copy.deepcopy(packet)
        live["target_scope"]["execute_command"] = "vendor operation"
        errors = canary.validate_packet(live, ROOT)
        self.assertTrue(any("prohibited machine-readable field" in error for error in errors))

    def test_live_endpoint_like_values_rejected(self):
        packet = load_packet()
        packet["global_invariants"][0]["description"] = "Use https://device.invalid"
        self.assert_error_contains(packet, "live-endpoint-like value")

    def test_referenced_path_missing_is_offline_blocker(self):
        packet = load_packet()
        packet["references"][0] = "docs/missing-hardware-canary-file.md"
        errors = canary.validate_packet(packet, ROOT)
        self.assertTrue(any("referenced path is missing" in error for error in errors))
        contract = canary._structural_repository_contract(packet, ROOT)
        self.assertEqual(
            canary.evaluate_offline_hardware_readiness(packet, contract),
            canary.BLOCKED,
        )

    def test_phase_ordering_and_cleanup_reachability(self):
        packet = load_packet()
        phases = {item["id"]: item for item in packet["phases"]}
        self.assertEqual(
            phases["P5_DISPOSABLE_CONNECTION_CANARY"]["dependencies"],
            ["P4_OFF_DEVICE_COMPATIBILITY_DECISION"],
        )
        self.assertIn(
            "P6_DISPOSABLE_POLICY_CANARY",
            phases["P8_FULL_ROUTERKIT_INSTALL_CANARY"]["dependencies"],
        )
        self.assertEqual(
            phases["P13_CLEANUP_AND_DEVICE_RETURN"]["dependencies"],
            ["P0_OPERATOR_PREFLIGHT"],
        )
        self.assertTrue(
            all(
                item["route_to_phase"] == "P13_CLEANUP_AND_DEVICE_RETURN"
                for item in packet["stop_conditions"]
            )
        )

    def test_full_issue_16_matrix_is_present(self):
        packet = load_packet()
        checks = {
            check["id"]
            for phase in packet["phases"]
            for check in phase["checks"]
        }
        required = {
            "P8_PREREQUISITES",
            "P8_RUNTIME",
            "P8_EGRESS",
            "P9_RERUN",
            "P9_PROFILE_UPDATE",
            "P10_PLAN_FAILURE",
            "P10_BOOTSTRAP_PRECONDITION_FAILURE",
            "P10_PREFLIGHT_FAILURE",
            "P10_BACKUP_GATE_FAILURE",
            "P10_INSTALL_ROLLBACK",
            "P10_AUTOSTART_FAILURE",
            "P10_HEALTHCHECK_ROLLBACK",
            "P10_DISPOSABLE_WRITE_FAILURES",
            "P11_REBOOT",
            "P11_USB_RECOVERY",
            "P12_UNSUPPORTED_REJECTION",
            "P13_DEVICE_RETURN",
        }
        self.assertEqual(required - checks, set())

    def test_packet_cannot_set_readiness_directly(self):
        packet = load_packet()
        self.assertNotIn("verdict", packet)
        self.assertNotIn("ready", packet)
        self.assertNotIn(canary.READY, json.dumps(packet))


class ReadinessFunctionTests(unittest.TestCase):
    def setUp(self):
        self.packet = load_packet()
        self.contract = canary._structural_repository_contract(self.packet, ROOT)

    def test_each_missing_repository_condition_changes_verdict(self):
        for key in (
            "static_guard_contract_present",
            "test_contract_present",
            "review_gate_present",
        ):
            with self.subTest(key=key):
                contract = dict(self.contract)
                contract[key] = False
                self.assertEqual(
                    canary.evaluate_offline_hardware_readiness(self.packet, contract),
                    canary.CHANGES_REQUIRED,
                )

    def test_missing_references_are_blocked(self):
        contract = dict(self.contract)
        contract["referenced_paths_present"] = False
        self.assertEqual(
            canary.evaluate_offline_hardware_readiness(self.packet, contract),
            canary.BLOCKED,
        )

    def test_baseline_mismatch_is_changes_required(self):
        for key, value in (
            ("baseline_release", "v0.0.0"),
            ("baseline_commit", "0" * 40),
        ):
            with self.subTest(key=key):
                contract = dict(self.contract)
                contract[key] = value
                self.assertEqual(
                    canary.evaluate_offline_hardware_readiness(self.packet, contract),
                    canary.CHANGES_REQUIRED,
                )

    def test_unknown_repository_contract_field_rejected(self):
        contract = dict(self.contract)
        contract["ready"] = True
        self.assertEqual(
            canary.evaluate_offline_hardware_readiness(self.packet, contract),
            canary.CHANGES_REQUIRED,
        )


class EvidenceSchemaTests(unittest.TestCase):
    def valid_manifest(self):
        return {
            "schema": "routerkit.netcraze.hardware-evidence.v1",
            "session_id": "canary-session-01",
            "packet_version": 1,
            "release": canary.RELEASED_BASELINE,
            "commit": canary.EXPECTED_MAIN,
            "started_at": "2026-07-16T20:00:00+04:00",
            "ended_at": None,
            "expected_target": {
                "model": "Netcraze Hopper 4G+ NC-2312",
                "firmware": "5.00.C.12.0-0",
                "architecture": "aarch64",
                "storage_state": "EXT4 USB with Entware",
                "comparison": "expected_unverified",
            },
            "observed_target": {
                "model": "not_observed",
                "firmware": "not_observed",
                "architecture": "not_observed",
                "storage_state": "not_observed",
                "comparison": "unknown",
            },
            "phases": [
                {
                    "phase_id": "P0_OPERATOR_PREFLIGHT",
                    "started_at": "2026-07-16T20:00:00+04:00",
                    "ended_at": None,
                    "outcome": "not_started",
                    "check_ids": ["P0_BASELINE"],
                    "notes_category": "none",
                }
            ],
            "artifacts": [
                {
                    "artifact_id": "baseline-metadata",
                    "phase_id": "P0_OPERATOR_PREFLIGHT",
                    "check_id": "P0_BASELINE",
                    "reference_kind": "opaque_reference",
                    "reference": "baseline-metadata-01",
                    "byte_size": 0,
                    "sha256": "0" * 64,
                    "sensitivity_class": "local_sensitive",
                    "retention_decision": "retain_private",
                    "redaction_status": "not_started",
                    "notes_category": "result",
                }
            ],
            "cleanup_status": "not_started",
            "retention_decision": "retain_private",
        }

    def test_schema_is_strict_metadata_only(self):
        self.assertEqual(
            canary.validate_private_manifest_schema(EVIDENCE_SCHEMA_PATH),
            [],
        )
        schema = json.loads(EVIDENCE_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            schema["properties"]["schema"]["const"],
            "routerkit.netcraze.hardware-evidence.v1",
        )
        artifact = schema["$defs"]["artifact"]
        self.assertFalse(artifact["additionalProperties"])
        self.assertNotIn("contents", artifact["properties"])
        self.assertNotIn("path", artifact["properties"])

    def test_sensitivity_sha_and_reference_contract(self):
        schema = json.loads(EVIDENCE_SCHEMA_PATH.read_text(encoding="utf-8"))
        artifact = schema["$defs"]["artifact"]["properties"]
        self.assertEqual(artifact["sha256"]["pattern"], "^[0-9a-f]{64}$")
        self.assertEqual(
            set(artifact["sensitivity_class"]["enum"]),
            {
                "public_safe",
                "local_sensitive",
                "secret_bearing",
                "router_backup",
                "device_inventory",
                "credential_adjacent",
            },
        )
        self.assertNotIn("/", artifact["reference"]["pattern"])

    def test_private_manifest_validator_accepts_metadata_only(self):
        self.assertEqual(
            canary.validate_private_manifest(self.valid_manifest(), load_packet()),
            [],
        )

    def test_private_manifest_rejects_raw_content_duplicate_ids_and_paths(self):
        raw = self.valid_manifest()
        raw["artifacts"][0]["raw_contents"] = "forbidden"
        errors = canary.validate_private_manifest(raw, load_packet())
        self.assertTrue(any("unknown fields" in error for error in errors))

        duplicate = self.valid_manifest()
        duplicate["artifacts"].append(copy.deepcopy(duplicate["artifacts"][0]))
        errors = canary.validate_private_manifest(duplicate, load_packet())
        self.assertTrue(any("duplicate artifact IDs" in error for error in errors))

        traversal = self.valid_manifest()
        traversal["artifacts"][0]["reference"] = "../private-output"
        errors = canary.validate_private_manifest(traversal, load_packet())
        self.assertTrue(any("without traversal" in error for error in errors))

        absolute = self.valid_manifest()
        absolute["artifacts"][0]["reference"] = "/private/output"
        errors = canary.validate_private_manifest(absolute, load_packet())
        self.assertTrue(any("without traversal" in error for error in errors))

    def test_private_manifest_rejects_duplicate_phase_and_wrong_check_mapping(self):
        duplicate_phase = self.valid_manifest()
        duplicate_phase["phases"].append(copy.deepcopy(duplicate_phase["phases"][0]))
        errors = canary.validate_private_manifest(duplicate_phase, load_packet())
        self.assertTrue(any("duplicate phase IDs" in error for error in errors))

        wrong_check = self.valid_manifest()
        wrong_check["artifacts"][0]["check_id"] = "P1_TARGET"
        errors = canary.validate_private_manifest(wrong_check, load_packet())
        self.assertTrue(any("does not belong to the phase" in error for error in errors))


class CliAndProbeTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(WRAPPER), *args],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def test_status_reads_no_packet(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(canary, "load_packet", side_effect=AssertionError("read")):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = canary.run_cli(["status"])
        self.assertEqual(code, 0)
        self.assertIn(canary.STATUS, stdout.getvalue())
        self.assertIn("hardware_validated=false", stdout.getvalue())
        self.assertIn("live_contract_confirmed=false", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_validate_render_and_matrix(self):
        validate = self.run_cli("validate", "--json")
        render = self.run_cli("render")
        matrix = self.run_cli("matrix", "--json")
        self.assertEqual(validate.returncode, 0, validate.stderr)
        payload = json.loads(validate.stdout)
        self.assertEqual(payload["verdict"], canary.READY)
        self.assertFalse(payload["hardware_validated"])
        self.assertFalse(payload["live_contract_confirmed"])
        self.assertEqual(payload["errors"], [])
        self.assertEqual(render.returncode, 0, render.stderr)
        self.assertIn("P13_CLEANUP_AND_DEVICE_RETURN", render.stdout)
        self.assertNotIn("private evidence reference", render.stdout.casefold())
        self.assertEqual(matrix.returncode, 0, matrix.stderr)
        self.assertEqual(len(json.loads(matrix.stdout)), 14)

    def test_consolidated_probe_is_inert(self):
        syntax = subprocess.run(["sh", "-n", str(PROBE)], check=False)
        readiness = subprocess.run(
            ["sh", str(PROBE), "--print-readiness"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        phases = subprocess.run(
            ["sh", str(PROBE), "--print-phase-list"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(syntax.returncode, 0)
        self.assertEqual(readiness.returncode, 0)
        self.assertIn(canary.READY, readiness.stdout)
        self.assertIn("hardware_validated=false", readiness.stdout)
        self.assertEqual(phases.stdout.splitlines(), list(canary.PHASE_IDS))

    def test_probe_has_no_network_command_or_hidden_switch(self):
        source = PROBE.read_text(encoding="utf-8")
        for token in (
            "curl ",
            "wget ",
            "ssh ",
            "telnet ",
            "/rci",
            "ROUTERKIT_ENABLE",
            "eval ",
            "source ",
            "tee ",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, source)


class StaticGuardTests(unittest.TestCase):
    def test_production_helpers_have_no_live_primitive(self):
        for path in (
            SCRIPTS / "routerkit_hardware_canary.py",
            SCRIPTS / "routerkit-hardware-canary.py",
        ):
            with self.subTest(path=path.name):
                self.assertEqual(
                    find_no_live_violations(path.read_text(encoding="utf-8")),
                    [],
                )

    def test_guard_mutations(self):
        mutations = (
            ("process", "import subprocess\n", "import:subprocess"),
            ("socket", "import socket\n", "import:socket"),
            ("thread", "import threading\n", "import:threading"),
            ("dynamic", "eval('1')\n", "call:eval"),
            ("write", "from pathlib import Path\nPath('x').write_text('y')\n", "call:write_text"),
            ("environment", "import os\nvalue = os.environ\n", "os:environ"),
            ("candidate", "value = 'show associations'\n", "literal:show associations"),
        )
        for label, source, expected in mutations:
            with self.subTest(label=label):
                self.assertIn(expected, find_no_live_violations(source))


class HardwareCanaryDocsTests(unittest.TestCase):
    def test_bilingual_docs_contain_all_phase_ids_and_verdicts(self):
        runbooks = (
            ROOT / "docs" / "hardware" / "netcraze-hardware-canary.md",
            ROOT / "docs" / "hardware" / "netcraze-hardware-canary.ru.md",
        )
        for path in runbooks:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                for phase_id in canary.PHASE_IDS:
                    self.assertIn(phase_id, text)
                for outcome in canary.REQUIRED_FINAL_OUTCOMES:
                    self.assertIn(outcome, text)

        checklists = (
            ROOT / "docs" / "hardware" / "netcraze-canary-checklist.md",
            ROOT / "docs" / "hardware" / "netcraze-canary-checklist.ru.md",
        )
        for path in checklists:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                for phase_id in canary.PHASE_IDS:
                    self.assertIn(phase_id.split("_", 1)[0], text)
                for outcome in canary.REQUIRED_FINAL_OUTCOMES:
                    self.assertIn(outcome, text)

    def test_readiness_docs_keep_non_claims(self):
        for name in (
            "hardware-canary-readiness.md",
            "hardware-canary-readiness.ru.md",
        ):
            text = (ROOT / "docs" / "architecture" / name).read_text(encoding="utf-8")
            self.assertIn(canary.READY, text)
            self.assertIn("hardware_validated=false", text)
            self.assertIn("live_contract_confirmed=false", text)
            self.assertIn("READ_CONTRACT_CONFIRMED", text)
            self.assertIn("WRITE_CONTRACT_CONFIRMED", text)
            self.assertIn("HARDWARE_CANARY_PASS", text)

    def test_public_templates_have_redaction_disclaimer_and_forbidden_fields(self):
        for name in (
            "netcraze-canary-public-evidence-template.md",
            "netcraze-canary-public-evidence-template.ru.md",
        ):
            text = (ROOT / "docs" / "hardware" / name).read_text(encoding="utf-8")
            self.assertIn("Redaction", text)
            self.assertIn("credentials", text)
            self.assertIn("MAC", text)
            self.assertIn("raw", text)

    def test_compatibility_patch_is_narrow_and_off_device(self):
        for name in (
            "netcraze-canary-compatibility-patch.md",
            "netcraze-canary-compatibility-patch.ru.md",
        ):
            text = (ROOT / "docs" / "hardware" / name).read_text(encoding="utf-8")
            self.assertIn("OFF_DEVICE_NARROW_PATCH_REQUIRED", text)
            self.assertIn("off-device", text)
            self.assertIn("full", text)
            self.assertIn("static", text)

    def test_relative_links_in_new_markdown_exist(self):
        docs = (
            ROOT / "docs" / "hardware" / "netcraze-hardware-canary.md",
            ROOT / "docs" / "hardware" / "netcraze-hardware-canary.ru.md",
        )
        for path in docs:
            text = path.read_text(encoding="utf-8")
            targets = []
            start = 0
            while True:
                opening = text.find("](", start)
                if opening < 0:
                    break
                closing = text.find(")", opening + 2)
                self.assertGreater(closing, opening)
                targets.append(text[opening + 2 : closing])
                start = closing + 1
            for target in targets:
                with self.subTest(path=path.name, target=target):
                    self.assertFalse(target.startswith("http"))
                    self.assertTrue((path.parent / target).resolve().is_file())

    def test_final_user_gate_sentence_is_not_committed(self):
        forbidden = (
            "Саша, у нас всё готово, что можно было подготовить без реального устройства. "
            "Дальше нужен Keenetic/Netcraze для аппаратного canary."
        )
        for path in ROOT.rglob("*.md"):
            self.assertNotIn(forbidden, path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
