import copy
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import routerkit_netcraze_external as external
import routerkit_netcraze_live as core
from routerkit_netcraze_plan import LocalEndpointManifest, LocalProxyProfile


DEVICE_MAC = "02:22:33:44:55:66"


def manifest_three():
    return LocalEndpointManifest(
        (
            LocalProxyProfile(1, "primary", "127.0.0.1", 1082, True),
            LocalProxyProfile(2, "fallback-1", "127.0.0.1", 1083, True),
            LocalProxyProfile(3, "fallback-2", "127.0.0.1", 1084, True),
        )
    )


def brownfield_state(profile_count=3, policy_count=3, assignments=()):
    proxy_names = ("XRAY-NL", "XRAY-SMART-RU", "XRAY-US")
    policy_names = ("VPN-NL", "VPN-SMART-RU", "VPN-US")
    proxies = tuple(
        core.ProxyState(
            "Proxy%d" % index,
            proxy_names[index],
            "socks5",
            "127.0.0.1",
            1082 + index,
            True,
            False,
        )
        for index in range(profile_count)
    )
    policies = tuple(
        core.PolicyState(
            "Policy%d" % index,
            policy_names[index],
            ("Proxy%d" % index,),
        )
        for index in range(policy_count)
    )
    return core.LiveState(
        proxies=proxies,
        policies=policies,
        assignments=tuple(assignments),
        default_guard=("ip hotspot default-policy permit",),
    )


def render_state(state):
    lines = list(state.default_guard) + ["!"]
    for proxy in state.proxies:
        lines.extend(
            [
                "interface %s" % proxy.object_id,
                " description %s" % proxy.description,
                " proxy protocol %s" % proxy.protocol,
                " proxy upstream %s %d" % (proxy.host, proxy.port),
            ]
        )
        if proxy.authentication_configured:
            lines.append(" authentication password hidden")
        if proxy.enabled:
            lines.append(" up")
        lines.append("!")
    for policy in state.policies:
        lines.extend(
            [
                "ip policy %s" % policy.object_id,
                " description %s" % policy.description,
            ]
        )
        for interface in policy.global_interfaces:
            lines.append(" permit global %s" % interface)
        lines.append("!")
    if state.assignments:
        lines.append("ip hotspot")
        for mac, policy in state.assignments:
            lines.append(" host %s policy %s" % (mac, policy))
        lines.append("!")
    return "\n".join(lines) + "\n"


class ExternalTransactionTests(unittest.TestCase):
    def test_external_snapshot_three_profile_reuse_is_noop(self):
        state = brownfield_state()
        packet = external.build_external_transaction(manifest_three(), state)
        self.assertEqual(packet["schema"], external.EXTERNAL_TRANSACTION_SCHEMA)
        self.assertEqual(
            [item["proxy_id"] for item in packet["bindings"]],
            ["Proxy0", "Proxy1", "Proxy2"],
        )
        self.assertEqual(
            [item["policy_id"] for item in packet["bindings"]],
            ["Policy0", "Policy1", "Policy2"],
        )
        self.assertTrue(all(item["proxy_action"] == "reuse" for item in packet["bindings"]))
        self.assertTrue(all(item["policy_action"] == "reuse" for item in packet["bindings"]))
        self.assertEqual(packet["assignment"]["action"], "none")
        self.assertFalse(packet["backup_required"])
        self.assertFalse(packet["write_required"])
        self.assertFalse(packet["save_required"])
        self.assertEqual(packet["commands"], [])
        result = external.verify_external_transaction(
            packet, manifest_three(), state, phase="running"
        )
        self.assertTrue(result["verified"])
        self.assertTrue(result["noop"])
        self.assertFalse(result["save_authorized"])

    def test_one_missing_proxy_has_exact_deterministic_create_commands(self):
        state = brownfield_state(profile_count=2, policy_count=2)
        packet = external.build_external_transaction(manifest_three(), state)
        self.assertEqual(packet["bindings"][2]["proxy_action"], "create")
        self.assertEqual(packet["bindings"][2]["policy_action"], "create")
        self.assertEqual(
            packet["commands"],
            [
                "interface Proxy2 description RouterKit-SOCKS-1084",
                "interface Proxy2 security-level public",
                "interface Proxy2 proxy protocol socks5",
                "interface Proxy2 proxy upstream 127.0.0.1 1084",
                "interface Proxy2 up",
                "ip policy Policy2 description RouterKit-Policy-1084",
                "ip policy Policy2 permit global Proxy2",
            ],
        )

    def test_one_missing_policy_has_exact_deterministic_create_commands(self):
        state = brownfield_state(profile_count=3, policy_count=2)
        packet = external.build_external_transaction(manifest_three(), state)
        self.assertTrue(all(item["proxy_action"] == "reuse" for item in packet["bindings"]))
        self.assertEqual(packet["bindings"][2]["policy_action"], "create")
        self.assertEqual(
            packet["commands"],
            [
                "ip policy Policy2 description RouterKit-Policy-1084",
                "ip policy Policy2 permit global Proxy2",
            ],
        )

    def test_assignment_create_is_an_exact_transaction_command(self):
        state = brownfield_state()
        packet = external.build_external_transaction(
            manifest_three(), state, device_mac=DEVICE_MAC, profile_slot=2
        )
        self.assertEqual(packet["assignment"]["action"], "assign")
        self.assertEqual(
            packet["commands"],
            ["ip hotspot host %s policy Policy1" % DEVICE_MAC],
        )
        self.assertTrue(packet["pre_save_verification_required"])
        self.assertTrue(packet["backup_required"])
        self.assertEqual(packet["save_command"], "system configuration save")

    def test_assignment_reuse_remains_noop_without_save(self):
        state = brownfield_state(assignments=((DEVICE_MAC, "Policy1"),))
        packet = external.build_external_transaction(
            manifest_three(), state, device_mac=DEVICE_MAC, profile_slot=2
        )
        self.assertEqual(packet["assignment"]["action"], "reuse")
        self.assertEqual(packet["commands"], [])
        self.assertFalse(packet["write_required"])
        self.assertFalse(packet["save_required"])
        result = external.verify_external_transaction(packet, manifest_three(), state)
        self.assertTrue(result["verified"])
        self.assertTrue(result["noop"])

    def test_assignment_move_requires_explicit_authorization(self):
        state = brownfield_state(assignments=((DEVICE_MAC, "Policy0"),))
        with self.assertRaises(core.LiveAdapterError):
            external.build_external_transaction(
                manifest_three(), state, device_mac=DEVICE_MAC, profile_slot=2
            )
        packet = external.build_external_transaction(
            manifest_three(),
            state,
            device_mac=DEVICE_MAC,
            profile_slot=2,
            allow_move=True,
        )
        self.assertEqual(packet["assignment"]["action"], "move")
        self.assertEqual(
            packet["rollback_commands"],
            ["ip hotspot host %s policy Policy0" % DEVICE_MAC],
        )

    def test_ambiguous_semantic_proxy_fails_closed(self):
        state = brownfield_state()
        duplicate = core.ProxyState(
            "Proxy3", "duplicate", "socks5", "127.0.0.1", 1082, True, False
        )
        state = core.LiveState(
            state.proxies + (duplicate,), state.policies, state.assignments, state.default_guard
        )
        with self.assertRaises(core.LiveAdapterError):
            external.build_external_transaction(manifest_three(), state)

    def test_ambiguous_semantic_policy_fails_closed(self):
        state = brownfield_state()
        duplicate = core.PolicyState("Policy3", "duplicate", ("Proxy0",))
        state = core.LiveState(
            state.proxies, state.policies + (duplicate,), state.assignments, state.default_guard
        )
        with self.assertRaises(core.LiveAdapterError):
            external.build_external_transaction(manifest_three(), state)

    def test_routerkit_owned_name_with_conflicting_semantics_fails_closed(self):
        state = brownfield_state()
        conflict = core.ProxyState(
            "Proxy3", "RouterKit-SOCKS-1082", "http", "127.0.0.1", 1082, True, False
        )
        state = core.LiveState(
            state.proxies + (conflict,), state.policies, state.assignments, state.default_guard
        )
        with self.assertRaises(core.LiveAdapterError):
            external.build_external_transaction(manifest_three(), state)

    def test_default_guard_drift_fails_verifier(self):
        state = brownfield_state()
        packet = external.build_external_transaction(manifest_three(), state)
        drifted = core.LiveState(
            state.proxies,
            state.policies,
            state.assignments,
            ("ip hotspot default-policy deny",),
        )
        with self.assertRaises(core.LiveAdapterError):
            external.verify_external_transaction(packet, manifest_three(), drifted)

    def test_stale_and_tampered_transactions_fail_closed(self):
        state = brownfield_state()
        packet = external.build_external_transaction(manifest_three(), state)
        tampered = copy.deepcopy(packet)
        tampered["commands"].append("system configuration save")
        with self.assertRaises(core.LiveAdapterError):
            external.verify_external_transaction(tampered, manifest_three(), state)

        stale = core.LiveState(
            state.proxies
            + (core.ProxyState("Proxy9", "other", "http", "192.0.2.2", 8080, True, False),),
            state.policies,
            state.assignments,
            state.default_guard,
        )
        with self.assertRaises(core.LiveAdapterError):
            external.verify_external_transaction(packet, manifest_three(), stale)

    def test_rehashed_json_type_confusion_still_fails_closed(self):
        packet = external.build_external_transaction(manifest_three(), brownfield_state())
        tampered = copy.deepcopy(packet)
        tampered["bindings"][0]["endpoint"]["enabled"] = 1
        unsigned = dict(tampered)
        del unsigned["transaction_fingerprint"]
        tampered["transaction_fingerprint"] = external._fingerprint(unsigned)
        with self.assertRaises(core.LiveAdapterError):
            external.validate_external_transaction(tampered, manifest_three())

    def test_command_order_and_transaction_fingerprint_are_deterministic(self):
        state = brownfield_state(profile_count=1, policy_count=1)
        first = external.build_external_transaction(manifest_three(), state)
        second = external.build_external_transaction(manifest_three(), state)
        self.assertEqual(first["commands"], second["commands"])
        self.assertEqual(first["transaction_fingerprint"], second["transaction_fingerprint"])
        proxy_commands = [command for command in first["commands"] if command.startswith("interface ")]
        policy_commands = [command for command in first["commands"] if command.startswith("ip policy ")]
        self.assertEqual(first["commands"], proxy_commands + policy_commands)

    def test_packet_contains_no_raw_config_or_authentication_secret(self):
        raw = render_state(brownfield_state()).replace(
            " up\n", " authentication password DontPrintThis\n up\n", 1
        )
        state = core.parse_running_config(raw)
        packet = external.build_external_transaction(manifest_three(), state)
        encoded = json.dumps(packet, sort_keys=True)
        self.assertNotIn(raw, encoded)
        self.assertNotIn("DontPrintThis", encoded)
        self.assertFalse(packet["raw_running_config_included"])

    def test_checked_in_schema_tracks_packet_fields(self):
        packet = external.build_external_transaction(manifest_three(), brownfield_state())
        schema = json.loads(
            (ROOT / "hardware" / "routerkit-netcraze-external-transaction.v1.schema.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(schema["properties"]["schema"]["const"], packet["schema"])
        self.assertEqual(set(schema["required"]), set(packet))
        binding_required = set(schema["$defs"]["binding"]["required"])
        self.assertEqual(binding_required, set(packet["bindings"][0]))

    def test_external_verify_passes_after_expected_state(self):
        state = brownfield_state(profile_count=2, policy_count=2)
        manifest = manifest_three()
        packet = external.build_external_transaction(manifest, state)
        plan = external.validate_external_transaction(packet, manifest)
        post = external._expected_post_state(manifest, plan, state, device_mac=None)
        running = external.verify_external_transaction(packet, manifest, post, phase="running")
        self.assertTrue(running["verified"])
        self.assertTrue(running["save_authorized"])
        saved = external.verify_external_transaction(packet, manifest, post, phase="saved")
        self.assertTrue(saved["saved_state_verified"])

    def test_external_verify_fails_after_silent_noop(self):
        state = brownfield_state(profile_count=2, policy_count=2)
        packet = external.build_external_transaction(manifest_three(), state)
        with self.assertRaises(core.LiveAdapterError):
            external.verify_external_transaction(packet, manifest_three(), state, phase="running")


class ExternalPrivateFileTests(unittest.TestCase):
    def test_snapshot_and_transaction_files_are_owner_only(self):
        state = brownfield_state()
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "running.txt"
            snapshot.write_text(render_state(state), encoding="utf-8")
            if os.name == "posix":
                snapshot.chmod(0o600)
            loaded = external.load_running_config_snapshot(snapshot)
            self.assertEqual(external.state_fingerprint(loaded), external.state_fingerprint(state))

            packet = external.build_external_transaction(manifest_three(), loaded)
            transaction = Path(directory) / "transaction.json"
            external.write_external_transaction(transaction, packet)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(transaction.stat().st_mode), 0o600)
            reloaded = external.load_external_transaction(transaction)
            external.validate_external_transaction(reloaded, manifest_three())

    @unittest.skipUnless(os.name == "posix", "POSIX permission contract")
    def test_public_or_symlink_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "private.txt"
            private.write_text(render_state(brownfield_state()), encoding="utf-8")
            private.chmod(0o644)
            with self.assertRaises(core.LiveAdapterError):
                external.load_running_config_snapshot(private)
            private.chmod(0o600)
            link = Path(directory) / "link.txt"
            link.symlink_to(private)
            with self.assertRaises(core.LiveAdapterError):
                external.load_running_config_snapshot(link)

    @unittest.skipUnless(os.name == "posix", "POSIX hard-link contract")
    def test_hard_link_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "running.txt"
            snapshot.write_text(render_state(brownfield_state()), encoding="utf-8")
            snapshot.chmod(0o600)
            os.link(snapshot, Path(directory) / "alias.txt")
            with self.assertRaises(core.LiveAdapterError):
                external.load_running_config_snapshot(snapshot)

    def test_non_utf8_and_oversized_snapshots_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "running.txt"
            snapshot.write_bytes(b"\xff")
            if os.name == "posix":
                snapshot.chmod(0o600)
            with self.assertRaises(core.LiveAdapterError):
                external.load_running_config_snapshot(snapshot)

            snapshot.write_bytes(b"x" * (external.MAX_SNAPSHOT_BYTES + 1))
            if os.name == "posix":
                snapshot.chmod(0o600)
            with self.assertRaises(core.LiveAdapterError):
                external.load_running_config_snapshot(snapshot)


class ExternalCliTests(unittest.TestCase):
    def test_plan_and_verify_use_only_private_snapshot_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "running.txt"
            snapshot.write_text(render_state(brownfield_state()), encoding="utf-8")
            manifest = root / "endpoints.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema": "routerkit.local-endpoints.v1",
                        "profiles": [
                            {
                                "slot": index,
                                "label": label,
                                "listen": "127.0.0.1",
                                "port": 1081 + index,
                                "enabled": True,
                                "protocol": "socks5",
                            }
                            for index, label in enumerate(
                                ("primary", "fallback-1", "fallback-2"), start=1
                            )
                        ],
                    }
                ),
                encoding="utf-8",
            )
            if os.name == "posix":
                snapshot.chmod(0o600)
                manifest.chmod(0o600)
            transaction = root / "transaction.json"

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = external.main(
                    [
                        "plan",
                        "--snapshot-file",
                        str(snapshot),
                        "--manifest-file",
                        str(manifest),
                        "--transaction-file",
                        str(transaction),
                    ]
                )
            self.assertEqual(code, 0)
            receipt = json.loads(stdout.getvalue())
            self.assertFalse(receipt["write_required"])
            self.assertEqual(receipt["command_count"], 0)

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = external.main(
                    [
                        "verify",
                        "--snapshot-file",
                        str(snapshot),
                        "--manifest-file",
                        str(manifest),
                        "--transaction-file",
                        str(transaction),
                    ]
                )
            self.assertEqual(code, 0)
            verified = json.loads(stdout.getvalue())
            self.assertTrue(verified["verified"])
            self.assertTrue(verified["noop"])

    def test_transaction_output_is_exclusive_and_not_overwritten(self):
        state = brownfield_state()
        packet = external.build_external_transaction(manifest_three(), state)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transaction.json"
            external.write_external_transaction(path, packet)
            original = path.read_bytes()
            with self.assertRaises(core.LiveAdapterError):
                external.write_external_transaction(path, packet)
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
