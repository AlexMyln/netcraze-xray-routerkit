import argparse
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import routerkit_live_install as live_install


MAC = "02:22:33:44:55:66"
ARTIFACT = {
    "artifact_key": "linux-arm64",
    "release": "v26.3.27",
    "archive_sha256": "a" * 64,
}


def make_intent(transport="external"):
    return live_install.build_intent(
        hardware_contract=live_install.DEFAULT_HARDWARE_CONTRACT,
        transport_mode=transport,
        artifact=ARTIFACT,
        selected_device_mac=MAC,
        profile_slot=2,
        move_device_authorized=False,
        controlled_reboot_authorized=False,
    )


def make_receipt(transport="external"):
    return live_install.initial_receipt(make_intent(transport))


def make_evidence(
    *,
    epoch=0,
    state_change="none",
    component_present=True,
    component_install_required=False,
    reboot_required=False,
    reboot_completed=False,
    dns_availability="any",
    dns_bound=None,
    dns_policy=None,
    client=True,
):
    return {
        "schema": live_install.EVIDENCE_SCHEMA,
        "hardware_contract": live_install.DEFAULT_HARDWARE_CONTRACT,
        "epoch": epoch,
        "state_change": state_change,
        "checks": {name: "PASS" for name in live_install.EVIDENCE_CHECKS},
        "component": {
            "present": component_present,
            "install_required": component_install_required,
            "reboot_required": reboot_required,
        },
        "reboot": {"completed": reboot_completed},
        "dns": {
            "protected": True,
            "availability": dns_availability,
            "bound_interfaces": list(dns_bound or []),
            "policy_interfaces": list(dns_policy or ["Proxy1"]),
            "verified": True,
        },
        "client": {
            "selected_assignment": client,
            "domain_dns": client,
            "domain_https": client,
            "probe_transport": "external-agent" if client else "none",
        },
    }


def args(**overrides):
    values = {
        "mode": "apply",
        "repo_root": str(ROOT),
        "transport": "external",
        "receipt_file": "/private/receipt.json",
        "target_root": "/opt",
        "generated": "generated",
        "hardware_contract": live_install.DEFAULT_HARDWARE_CONTRACT,
        "artifact_manifest": None,
        "source_env": None,
        "source_file": None,
        "reuse_profiles": None,
        "primary_index": None,
        "fallback_index": [],
        "selected_device_mac": MAC,
        "profile_slot": 2,
        "move_device": False,
        "ndmc_path": None,
        "evidence_file": None,
        "authorize_reboot": False,
        "external_pre_snapshot_file": None,
        "external_post_snapshot_file": None,
        "external_saved_snapshot_file": None,
        "transaction_file": None,
        "yes": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def completed(returncode=0, stdout=""):
    return type("Completed", (), {"returncode": returncode, "stdout": stdout})()


class LiveInstallPlanTests(unittest.TestCase):
    def test_full_happy_path_stage_order(self):
        self.assertEqual(
            live_install.STAGE_ORDER,
            (
                "network_preflight",
                "usb_ext4_entware_readiness",
                "pinned_xray_bootstrap",
                "protected_profile_source",
                "generate",
                "strict_plan",
                "backup",
                "install",
                "healthcheck",
                "autostart",
                "native_proxy_component",
                "controlled_reboot",
                "post_reboot_proof",
                "native_proxy_policy_transaction",
                "protected_dns_gate",
                "selected_client_acceptance",
                "final_result",
            ),
        )

    def test_full_happy_path_executes_existing_owners_in_order(self):
        receipt = make_receipt("local-ndmc")
        evidence = make_evidence()
        delegated = []

        def fake_run(command, **_kwargs):
            delegated.append(Path(command[1] if command[0] == sys.executable else command[1]).name)
            if any(str(item).endswith("routerkit-autostart.py") for item in command):
                return completed(0, json.dumps({"runtime_verified": True}))
            return completed(0, "{}")

        def fake_local(_args, _root, _path, active):
            active["native_transaction"]["status"] = "PASS"
            active["native_transaction"]["write_required"] = False
            active["native_transaction"]["save_required"] = False
            active["native_transaction"]["pre_epoch"] = 0
            live_install.mark_stage(active, "native_proxy_policy_transaction", "PASS")
            active["final_verification"]["native_routing"] = "PASS"

        fake_manifest = object()
        with mock.patch.object(live_install, "_run", side_effect=fake_run), mock.patch.object(
            live_install, "_persist"
        ), mock.patch.object(live_install, "_endpoint_manifest", return_value=fake_manifest), mock.patch.object(
            live_install.external, "manifest_fingerprint", return_value="e" * 64
        ), mock.patch.object(live_install, "_local_native_stage", side_effect=fake_local):
            live_install._run_installation_stages(
                args(transport="local-ndmc"),
                ROOT,
                Path("/private/receipt.json"),
                receipt,
                evidence,
            )
            live_install._component_and_reboot(
                args(transport="local-ndmc"),
                Path("/private/receipt.json"),
                receipt,
                evidence,
            )
            live_install._native_dns_client_stages(
                args(transport="local-ndmc"),
                ROOT,
                Path("/private/receipt.json"),
                receipt,
                evidence,
            )

        self.assertEqual(
            delegated,
            [
                "preflight.sh",
                "routerkit-bootstrap.py",
                "routerkit.py",
                "backup.sh",
                "install-xray-direct.sh",
                "healthcheck.sh",
                "routerkit-autostart.py",
            ],
        )
        self.assertTrue(all(live_install.stage_done(receipt, stage) for stage in live_install.STAGE_ORDER))

    def test_plan_only_has_no_writes_or_prompts(self):
        output = io.StringIO()
        with mock.patch.object(live_install, "artifact_identity", return_value=ARTIFACT), mock.patch.object(
            live_install, "write_receipt"
        ) as write, mock.patch.object(live_install, "_run") as run, mock.patch.object(
            live_install, "confirm_scope"
        ) as confirm, contextlib.redirect_stdout(output):
            code = live_install.main(
                ["plan", "--transport", "external", "--selected-device-mac", MAC, "--profile-slot", "2"]
            )
        self.assertEqual(code, 0)
        write.assert_not_called()
        run.assert_not_called()
        confirm.assert_not_called()
        self.assertIn("Plan mode performs no writes", output.getvalue())
        self.assertIn(live_install.selected_device_fingerprint(MAC), output.getvalue())
        self.assertNotIn(MAC, output.getvalue())

    def test_apply_uses_one_scope_confirmation(self):
        answers = []

        def input_fn(_prompt):
            answers.append(1)
            return "yes"

        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "private" / "receipt.json"
            with mock.patch.object(live_install, "artifact_identity", return_value=ARTIFACT), mock.patch.object(
                live_install, "write_receipt"
            ), mock.patch.object(live_install, "_run_installation_stages"), mock.patch.object(
                live_install, "_component_and_reboot"
            ), mock.patch.object(live_install, "_native_dns_client_stages"), mock.patch.object(
                live_install, "_print_status"
            ):
                code = live_install.main(
                    [
                        "apply",
                        "--receipt-file",
                        str(receipt),
                        "--selected-device-mac",
                        MAC,
                        "--profile-slot",
                        "2",
                    ],
                    input_fn=input_fn,
                )
        self.assertEqual(code, 0)
        self.assertEqual(len(answers), 1)


class ReceiptAndEpochTests(unittest.TestCase):
    def test_no_repeated_discovery_in_same_epoch(self):
        receipt = make_receipt()
        evidence = make_evidence()
        live_install.accept_discovery_evidence(receipt, evidence)
        first = dict(receipt["discovery"])
        live_install.accept_discovery_evidence(receipt, evidence)
        self.assertEqual(receipt["discovery"], first)

    def test_contradictory_discovery_in_same_epoch_is_rejected(self):
        receipt = make_receipt()
        live_install.accept_discovery_evidence(receipt, make_evidence())
        changed = make_evidence(client=False)
        with self.assertRaises(live_install.LiveInstallError):
            live_install.accept_discovery_evidence(receipt, changed)

    def test_post_reboot_starts_new_epoch(self):
        receipt = make_receipt()
        live_install.accept_discovery_evidence(receipt, make_evidence())
        rebooted = make_evidence(
            epoch=1,
            state_change="reboot",
            reboot_required=True,
            reboot_completed=True,
        )
        live_install.accept_discovery_evidence(receipt, rebooted)
        self.assertEqual(receipt["state_epoch"], 1)

    def test_resume_from_owner_only_receipt(self):
        receipt = make_receipt()
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "state"
            path = parent / "receipt.json"
            live_install.write_receipt(path, receipt)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            loaded = live_install.load_receipt(path)
            live_install.validate_intent_compatible(loaded, make_intent())
            self.assertEqual(loaded["schema"], live_install.RECEIPT_SCHEMA)

    def test_stale_incompatible_receipt_is_rejected(self):
        receipt = make_receipt()
        other = make_intent("local-ndmc")
        with self.assertRaises(live_install.LiveInstallError):
            live_install.validate_intent_compatible(receipt, other)

    def test_resume_allows_only_monotonic_explicit_authority_upgrade(self):
        receipt = make_receipt()
        upgraded = live_install.build_intent(
            hardware_contract=live_install.DEFAULT_HARDWARE_CONTRACT,
            transport_mode="external",
            artifact=ARTIFACT,
            selected_device_mac=MAC,
            profile_slot=2,
            controlled_reboot_authorized=True,
        )
        self.assertTrue(live_install.validate_intent_compatible(receipt, upgraded))
        self.assertTrue(receipt["controlled_reboot_authorized"])
        self.assertFalse(live_install.validate_intent_compatible(receipt, upgraded))

    def test_secrets_absent_from_receipt_and_status_output(self):
        receipt = make_receipt()
        encoded = json.dumps(receipt, sort_keys=True).lower()
        for marker in live_install.PROHIBITED_TEXT_MARKERS:
            self.assertNotIn(marker, encoded)
        self.assertNotIn(MAC, encoded)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            live_install._print_status(receipt)
        self.assertNotIn(MAC, output.getvalue())

    def test_checked_in_state_schema_tracks_receipt_fields(self):
        schema = json.loads(
            (ROOT / "hardware" / "routerkit-live-install.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        receipt = make_receipt()
        self.assertEqual(schema["properties"]["schema"]["const"], receipt["schema"])
        self.assertEqual(set(schema["required"]), set(receipt))
        self.assertEqual(
            set(schema["properties"]["stages"]["required"]), set(receipt["stages"])
        )

    def test_checked_in_evidence_schema_tracks_evidence_fields(self):
        schema = json.loads(
            (ROOT / "hardware" / "routerkit-live-install-evidence.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        evidence = make_evidence()
        self.assertEqual(schema["properties"]["schema"]["const"], evidence["schema"])
        self.assertEqual(set(schema["required"]), set(evidence))
        self.assertEqual(
            set(schema["properties"]["checks"]["required"]), set(evidence["checks"])
        )


class ComponentAndRebootTests(unittest.TestCase):
    def test_component_already_present_continues_without_reboot(self):
        receipt = make_receipt()
        with mock.patch.object(live_install, "_persist"):
            live_install._component_and_reboot(
                args(), Path("/private/receipt.json"), receipt, make_evidence()
            )
        self.assertEqual(receipt["stages"]["native_proxy_component"]["status"], "PASS")
        self.assertEqual(receipt["stages"]["controlled_reboot"]["status"], "SKIPPED")

    def test_component_install_emits_explicit_handoff(self):
        receipt = make_receipt()
        evidence = make_evidence(
            component_present=False, component_install_required=True
        )
        with mock.patch.object(live_install, "_persist"), self.assertRaises(
            live_install.LiveInstallHandoff
        ) as raised:
            live_install._component_and_reboot(
                args(), Path("/private/receipt.json"), receipt, evidence
            )
        self.assertEqual(raised.exception.action, "PROXY_COMPONENT_INSTALL_REQUIRED")

    def test_reboot_required_continuation_is_bounded(self):
        receipt = make_receipt()
        evidence = make_evidence(reboot_required=True)
        with mock.patch.object(live_install, "_persist"), self.assertRaises(
            live_install.LiveInstallHandoff
        ) as raised:
            live_install._component_and_reboot(
                args(authorize_reboot=True), Path("/private/receipt.json"), receipt, evidence
            )
        self.assertEqual(raised.exception.action, "CONTROLLED_REBOOT_REQUIRED")

    def test_reboot_completion_requires_fresh_epoch(self):
        receipt = make_receipt()
        evidence = make_evidence(reboot_required=True, reboot_completed=True)
        with mock.patch.object(live_install, "_persist"), self.assertRaises(
            live_install.LiveInstallError
        ):
            live_install._component_and_reboot(
                args(authorize_reboot=True), Path("/private/receipt.json"), receipt, evidence
            )


class NativeCompositionTests(unittest.TestCase):
    def test_local_ndmc_native_stage_composes_existing_adapter(self):
        receipt = make_receipt("local-ndmc")
        calls = []

        def fake_run(command, **_kwargs):
            calls.append(command)
            if "apply" in command:
                return completed(0, json.dumps({"verified": True, "write_required": False}))
            return completed(0, "{}")

        with mock.patch.object(live_install, "_run", side_effect=fake_run), mock.patch.object(
            live_install, "_persist"
        ):
            live_install._local_native_stage(
                args(transport="local-ndmc"), ROOT, Path("/private/receipt.json"), receipt
            )
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0][1].endswith("routerkit-netcraze-live.py"))
        self.assertIn("plan", calls[0])
        self.assertIn("apply", calls[1])
        self.assertIn("--contract", calls[1])

    def test_external_noop_requires_fresh_readback_and_never_save(self):
        receipt = make_receipt("external")
        receipt["native_transaction"].update(
            {
                "fingerprint": "b" * 64,
                "status": "PLANNED",
                "write_required": False,
                "save_required": False,
                "pre_epoch": 0,
                "dns_refresh_required": False,
            }
        )
        packet = {"transaction_fingerprint": "b" * 64}
        with mock.patch.object(live_install, "_endpoint_manifest"), mock.patch.object(
            live_install.external, "load_external_transaction", return_value=packet
        ), mock.patch.object(live_install.external, "validate_external_transaction"), mock.patch.object(
            live_install, "_persist"
        ), self.assertRaises(live_install.LiveInstallHandoff) as raised:
            live_install._external_native_stage(
                args(), Path("/private/receipt.json"), receipt
            )
        self.assertEqual(raised.exception.action, "EXTERNAL_NATIVE_NOOP_READBACK_REQUIRED")
        self.assertIn("Execute no commands and do not save.", raised.exception.expectations)

    def test_external_mutation_emits_exact_delivery_handoff(self):
        receipt = make_receipt("external")
        receipt["native_transaction"].update(
            {
                "fingerprint": "c" * 64,
                "status": "PLANNED",
                "write_required": True,
                "save_required": True,
                "pre_epoch": 0,
                "dns_refresh_required": True,
            }
        )
        packet = {"transaction_fingerprint": "c" * 64}
        with mock.patch.object(live_install, "_endpoint_manifest"), mock.patch.object(
            live_install.external, "load_external_transaction", return_value=packet
        ), mock.patch.object(live_install.external, "validate_external_transaction"), mock.patch.object(
            live_install, "_persist"
        ), self.assertRaises(live_install.LiveInstallHandoff) as raised:
            live_install._external_native_stage(args(), Path("/private/receipt.json"), receipt)
        self.assertEqual(raised.exception.action, "EXTERNAL_NATIVE_TRANSACTION_DELIVERY_REQUIRED")
        self.assertTrue(any("transport success is not verification" in item for item in raised.exception.expectations))

    def test_transport_result_is_not_trusted(self):
        receipt = make_receipt("external")
        receipt["native_transaction"]["status"] = "PLANNED"
        receipt["native_transaction"]["fingerprint"] = "d" * 64
        receipt["native_transaction"]["write_required"] = True
        receipt["native_transaction"]["save_required"] = True
        receipt["native_transaction"]["pre_epoch"] = 0
        packet = {"transaction_fingerprint": "d" * 64}
        with mock.patch.object(live_install, "_endpoint_manifest"), mock.patch.object(
            live_install.external, "load_external_transaction", return_value=packet
        ), mock.patch.object(live_install.external, "validate_external_transaction"), mock.patch.object(
            live_install.external, "load_running_config_snapshot"
        ), mock.patch.object(
            live_install.external,
            "verify_external_transaction",
            side_effect=live_install.live.LiveAdapterError("silent no-op"),
        ), mock.patch.object(live_install, "_persist"), self.assertRaises(
            live_install.live.LiveAdapterError
        ):
            live_install._external_native_stage(
                args(external_post_snapshot_file="/private/post.txt"),
                Path("/private/receipt.json"),
                receipt,
            )


class DnsAndClientAcceptanceTests(unittest.TestCase):
    def test_dns_existing_good_reuse(self):
        accepted, reason = live_install.dns_gate(make_evidence())
        self.assertTrue(accepted)
        self.assertIn("reusable", reason)

    def test_pppoe_bound_only_dns_rejected_for_proxy_policy(self):
        evidence = make_evidence(
            dns_availability="interfaces",
            dns_bound=["PPPoE0"],
            dns_policy=["Proxy1"],
        )
        accepted, reason = live_install.dns_gate(evidence)
        self.assertFalse(accepted)
        self.assertIn("absent", reason)

    def test_any_and_unbound_dns_are_accepted(self):
        for availability in ("any", "unbound"):
            with self.subTest(availability=availability):
                accepted, _reason = live_install.dns_gate(
                    make_evidence(dns_availability=availability)
                )
                self.assertTrue(accepted)

    def test_selected_client_assignment_is_required(self):
        evidence = make_evidence()
        evidence["client"]["selected_assignment"] = False
        self.assertFalse(live_install.client_acceptance_gate(evidence))

    def test_domain_functional_gate_is_required(self):
        evidence = make_evidence()
        evidence["client"]["domain_https"] = False
        self.assertFalse(live_install.client_acceptance_gate(evidence))


class FailureAndCompatibilityTests(unittest.TestCase):
    def test_persistent_local_routing_state_is_preserved_by_existing_installer(self):
        script = (ROOT / "scripts" / "install-xray-direct.sh").read_text(encoding="utf-8")
        self.assertIn("routing-overrides.json", script)
        self.assertIn('python3 "$ROUTING_HELPER" reconcile --yes', script)
        command = live_install._source_setup_command(args(), ROOT)
        self.assertNotIn("--add-service", command)

    def test_failure_stops_all_later_stages(self):
        receipt = make_receipt("local-ndmc")
        calls = []

        def fail_first(command, **_kwargs):
            calls.append(command)
            return completed(9)

        with mock.patch.object(live_install, "_run", side_effect=fail_first), mock.patch.object(
            live_install, "_persist"
        ), self.assertRaises(live_install.LiveInstallError):
            live_install._run_installation_stages(
                args(transport="local-ndmc"),
                ROOT,
                Path("/private/receipt.json"),
                receipt,
                make_evidence(),
            )
        self.assertEqual(len(calls), 1)
        with mock.patch.object(live_install, "_run", side_effect=fail_first), mock.patch.object(
            live_install, "_persist"
        ), self.assertRaises(live_install.LiveInstallHandoff) as raised:
            live_install._run_installation_stages(
                args(transport="local-ndmc"),
                ROOT,
                Path("/private/receipt.json"),
                receipt,
                make_evidence(),
            )
        self.assertEqual(raised.exception.action, "FAILED_PREFLIGHT_STATE_CHANGE_REQUIRED")
        self.assertEqual(len(calls), 1)
        self.assertEqual(receipt["stages"]["pinned_xray_bootstrap"]["status"], "PENDING")

    def test_existing_setup_behavior_stays_separate(self):
        command = [
            sys.executable,
            str(ROOT / "scripts" / "routerkit.py"),
            "setup",
            "--dry-run",
        ]
        result = __import__("subprocess").run(command, check=False, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Would run setup pipeline:", result.stdout)
        self.assertNotIn("live-install", result.stdout)


if __name__ == "__main__":
    unittest.main()
