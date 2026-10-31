import base64
import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import routerkit_subscription as sub
import routerkit_profile_network as network
from routerkit_profile_source import parse_compatible_nodes
from routerkit_xray import build_vless_outbound
from tests.test_routerkit_profile_source import fixture_nodes, make_link


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = sub.conf_dir(self.root)
        self.config.mkdir(parents=True, mode=0o700)
        sub.ensure_private_directory(sub.state_dir(self.root))
        self.links = [make_link(item) for item in fixture_nodes()[:3]]
        self.nodes = parse_compatible_nodes("\n".join(self.links))
        self.profiles = [{"name": name, "port": port} for name, port in
                         zip(("primary", "fallback-1", "fallback-2"), sub.PORTS)]
        self.outbounds = {"outbounds": [build_vless_outbound(p, n) for p, n in
                                       zip(self.profiles, self.nodes)] + [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ]}
        self.original = {
            "00_log.json": {"log": {"loglevel": "warning"}},
            "03_inbounds.json": {"inbounds": [{"tag": "socks-" + p["name"],
                "port": p["port"], "listen": "127.0.0.1", "protocol": "socks"}
                for p in self.profiles]},
            sub.OUTBOUND: self.outbounds,
            "05_routing.json": {"routing": {"rules": [
                {"type": "field", "domain": ["domain:example.org"], "outboundTag": "direct"}
            ]}},
        }
        for name, value in self.original.items():
            sub.publish(self.config / name, sub.encode(value))
        self.snapshot = sub.active_snapshot(self.root)
        self.source = {"schema": sub.SCHEMA, "url": "https://example.org/protected-source",
                       "bindings": sub.make_bindings(self.snapshot, self.nodes)}
        sub.ensure_private_directory(sub.source_path(self.root).parent)
        sub.publish(sub.source_path(self.root), sub.encode(self.source))

    def fresh_nodes(self, *, port=5443):
        links = list(self.links)
        links[0] = links[0].replace(":" + str(self.nodes[0].port) + "?", ":%d?" % port)
        return parse_compatible_nodes("\n".join(links))

    def desired(self):
        return sub.updated_outbounds(self.snapshot, self.source, self.fresh_nodes())

    def mocked_runtime(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        commands = stack.enter_context(mock.patch.object(sub, "run_command"))
        verify = stack.enter_context(mock.patch.object(sub, "verify_runtime", return_value=SimpleNamespace(ok=True)))
        return commands, verify

    def test_server_port_change_preserves_local_ports_and_routing(self):
        commands, _ = self.mocked_runtime()
        updated, ports = self.desired()
        self.assertEqual(sub.apply_update(self.root, self.snapshot, updated, ports), "updated")
        observed = sub.active_snapshot(self.root)
        self.assertEqual(json.loads(observed[sub.OUTBOUND])["outbounds"][0]["settings"]["vnext"][0]["port"], 5443)
        for name in self.snapshot.keys() - {sub.OUTBOUND}:
            self.assertEqual(observed[name], self.snapshot[name])
        self.assertEqual(ports, list(sub.PORTS))
        self.assertEqual(sum(c.args[0][-1] == "restart" for c in commands.call_args_list), 1)
        self.assertFalse((sub.state_dir(self.root) / "pending.json").exists())

    def test_reorder_does_not_change_slot_mapping(self):
        updated, _ = sub.updated_outbounds(self.snapshot, self.source, list(reversed(self.fresh_nodes())))
        self.assertEqual(updated["outbounds"][0]["settings"]["vnext"][0]["port"], 5443)
        self.assertEqual(updated["outbounds"][1], self.outbounds["outbounds"][1])

    def test_missing_node_never_selects_another_by_index(self):
        with self.assertRaisesRegex(sub.SubscriptionError, "missing or ambiguous"):
            sub.updated_outbounds(self.snapshot, self.source, self.nodes[1:])
        self.assertEqual(sub.active_snapshot(self.root), self.snapshot)

    def test_duplicate_label_is_rejected(self):
        nodes = self.nodes + parse_compatible_nodes(make_link(label=self.nodes[0].name))
        with self.assertRaises(sub.SubscriptionError):
            sub.updated_outbounds(self.snapshot, self.source, nodes)

    def test_enrollment_adopts_already_changed_server_port(self):
        self.assertEqual(sub.make_bindings(self.snapshot, self.fresh_nodes()), self.source["bindings"])

    def test_enrollment_does_not_adopt_changed_uuid(self):
        changed = parse_compatible_nodes(make_link(label=self.nodes[0].name)) + self.nodes[1:]
        with self.assertRaises(sub.SubscriptionError):
            sub.make_bindings(self.snapshot, changed)

    def test_explicit_protected_selection_can_migrate_a_replaced_endpoint(self):
        replacement = make_link(fixture_nodes()[1], label=self.nodes[0].name)
        nodes = parse_compatible_nodes(replacement) + self.nodes[1:]
        selection = {"outbounds": [build_vless_outbound(p, n) for p, n in zip(self.profiles, nodes)]
                     + self.outbounds["outbounds"][-2:]}
        with self.assertRaises(sub.SubscriptionError):
            sub.make_bindings(self.snapshot, nodes)
        self.assertEqual(sub.make_bindings(self.snapshot, list(reversed(nodes)), selection),
                         self.source["bindings"])

    def test_explicit_selection_requires_exact_current_source_parameters(self):
        with self.assertRaises(sub.SubscriptionError):
            sub.make_bindings(self.snapshot, self.fresh_nodes(), self.outbounds)

    def test_explicit_selection_rejects_duplicate_or_wrong_profile_tags(self):
        for replacement in ("vless-other", "vless-fallback-1"):
            selection = json.loads(json.dumps(self.outbounds))
            selection["outbounds"][0]["tag"] = replacement
            with self.assertRaises(sub.SubscriptionError):
                sub.make_bindings(self.snapshot, self.nodes, selection)

    def test_explicit_selection_rejects_unrelated_outbound_or_extra_fields(self):
        for selection in ({"outbounds": self.outbounds["outbounds"], "extra": True},
                          {"outbounds": self.outbounds["outbounds"] + [{"tag": "unrelated"}]}):
            with self.assertRaises(sub.SubscriptionError):
                sub.make_bindings(self.snapshot, self.nodes, selection)

    def test_unchanged_does_not_restart_or_write_configuration(self):
        commands, _ = self.mocked_runtime()
        updated, ports = sub.updated_outbounds(self.snapshot, self.source, self.nodes)
        before = (self.config / sub.OUTBOUND).stat().st_ino
        self.assertEqual(sub.apply_update(self.root, self.snapshot, updated, ports), "unchanged")
        self.assertEqual((self.config / sub.OUTBOUND).stat().st_ino, before)
        self.assertFalse(any(c.args[0][-1] == "restart" for c in commands.call_args_list))
        self.assertEqual(len(commands.call_args_list), 3)  # actual HTTPS probes still required

    def test_stage_rejection_never_publishes_or_restarts(self):
        commands, _ = self.mocked_runtime()
        commands.side_effect = sub.SubscriptionError("rejected")
        with self.assertRaises(sub.SubscriptionError):
            sub.apply_update(self.root, self.snapshot, *self.desired())
        self.assertEqual(sub.active_snapshot(self.root), self.snapshot)
        self.assertFalse((sub.state_dir(self.root) / "pending.json").exists())

    def test_failed_https_probe_rolls_back_without_requiring_old_upstream(self):
        commands, _ = self.mocked_runtime()
        def command(args, *unused):
            if args[-1] == sub.PROBE_URL:
                raise sub.SubscriptionError("network unavailable")
        commands.side_effect = command
        with self.assertRaisesRegex(sub.SubscriptionError, "previous local configuration restored"):
            sub.apply_update(self.root, self.snapshot, *self.desired())
        self.assertEqual(sub.active_snapshot(self.root), self.snapshot)
        self.assertEqual(sum(c.args[0][-1] == "restart" for c in commands.call_args_list), 2)
        self.assertFalse((sub.state_dir(self.root) / "pending.json").exists())

    def test_failed_restart_rolls_back(self):
        self.mocked_runtime()
        with mock.patch.object(sub, "restart", side_effect=[sub.SubscriptionError("failed"), None]):
            with self.assertRaisesRegex(sub.SubscriptionError, "previous local"):
                sub.apply_update(self.root, self.snapshot, *self.desired())
        self.assertEqual(sub.active_snapshot(self.root), self.snapshot)

    def test_failed_rollback_retains_recovery_journal(self):
        self.mocked_runtime()
        with mock.patch.object(sub, "restart", side_effect=sub.SubscriptionError("failed")):
            with self.assertRaisesRegex(sub.SubscriptionError, "rollback is unproven"):
                sub.apply_update(self.root, self.snapshot, *self.desired())
        self.assertTrue((sub.state_dir(self.root) / "pending.json").exists())

    def test_sigterm_during_apply_restores_previous_config(self):
        self.mocked_runtime()
        def interrupted():
            os.kill(os.getpid(), signal.SIGTERM)
        calls = []
        def restart(root):
            calls.append(root)
            if len(calls) == 1:
                interrupted()
        with mock.patch.object(sub, "restart", side_effect=restart):
            with sub.interruptible(), self.assertRaisesRegex(sub.SubscriptionError, "previous local"):
                sub.apply_update(self.root, self.snapshot, *self.desired())
        self.assertEqual(sub.active_snapshot(self.root), self.snapshot)

    def pending(self):
        updated, ports = self.desired()
        desired = sub.encode(updated)
        sub.publish(sub.state_dir(self.root) / "pending.json", sub.encode({
            "schema": sub.SCHEMA, "before": self.snapshot[sub.OUTBOUND].decode(),
            "after_sha256": sub.digest(desired), "ports": ports}))
        sub.publish(self.config / sub.OUTBOUND, desired)

    def test_power_loss_recovery_restores_before_fetch(self):
        self.mocked_runtime()
        self.pending()
        with mock.patch.object(sub, "fetch_nodes", side_effect=network.ProfileNetworkError("failed")):
            with self.assertRaises(network.ProfileNetworkError):
                sub.refresh(self.root)
        self.assertEqual(sub.active_snapshot(self.root), self.snapshot)
        self.assertFalse((sub.state_dir(self.root) / "pending.json").exists())

    def test_recovery_refuses_to_overwrite_external_drift(self):
        self.pending()
        sub.publish(self.config / sub.OUTBOUND, sub.encode({"outbounds": []}))
        with self.assertRaisesRegex(sub.SubscriptionError, "drift"):
            sub.recover(self.root)
        self.assertTrue((sub.state_dir(self.root) / "pending.json").exists())

    def test_network_failure_retains_configs_and_last_success(self):
        status = sub.state_dir(self.root) / "status.json"
        sub.publish(status, sub.encode({"last_success": 123, "result": "updated"}))
        with mock.patch.object(sub, "fetch_nodes", side_effect=network.ProfileNetworkError("failed")):
            with self.assertRaises(network.ProfileNetworkError):
                sub.refresh(self.root)
        self.assertEqual(sub.active_snapshot(self.root), self.snapshot)
        self.assertEqual(json.loads(sub.read(status))["last_success"], 123)
        self.assertEqual(json.loads(sub.read(status))["result"], "failed")

    def test_private_source_never_appears_in_status(self):
        output = json.dumps(sub.inspect(self.root))
        self.assertNotIn(self.source["url"], output)
        for node in self.nodes:
            self.assertNotIn(node.uuid, output)
            self.assertNotIn(node.name, output)

    def test_untrusted_symlink_in_config_fails_closed(self):
        target = self.config / sub.OUTBOUND
        target.unlink()
        target.symlink_to(self.config / "03_inbounds.json")
        with self.assertRaises(sub.PrivateFileError):
            sub.active_snapshot(self.root)

    def test_world_readable_source_is_rejected(self):
        sub.source_path(self.root).chmod(0o644)
        with self.assertRaises(sub.PrivateFileError):
            sub.load_source(self.root)

    def test_two_workers_cannot_run_together_and_stale_lock_is_reusable(self):
        with sub.locked(self.root):
            with self.assertRaisesRegex(sub.SubscriptionError, "Another subscription"):
                with sub.locked(self.root):
                    self.fail("second worker entered")
        with sub.locked(self.root):
            pass

    def test_single_profile_is_supported(self):
        one = dict(self.snapshot)
        one["03_inbounds.json"] = sub.encode({"inbounds": json.loads(one["03_inbounds.json"])["inbounds"][:1]})
        source = dict(self.source, bindings=self.source["bindings"][:1])
        updated, ports = sub.updated_outbounds(one, source, self.fresh_nodes())
        self.assertEqual(ports, [1082])

    def test_changed_active_profile_set_requires_rebinding(self):
        source = dict(self.source, bindings=self.source["bindings"][:1])
        with self.assertRaisesRegex(sub.SubscriptionError, "profile set changed"):
            sub.updated_outbounds(self.snapshot, source, self.nodes)

    def test_base64_source_uses_existing_protected_resolver(self):
        payload = base64.b64encode("\n".join(self.links).encode()).decode()
        with mock.patch.object(sub, "resolve_https_source", return_value=SimpleNamespace(payload=payload)) as resolver:
            self.assertEqual(sub.fetch_nodes(self.source["url"]), self.nodes)
        resolver.assert_called_once_with(self.source["url"])

    def test_probe_is_tls_verified_socks_remote_dns_and_proxy_env_cannot_bypass(self):
        commands, verifier = self.mocked_runtime()
        sub.verify(self.root, [1082], traffic=True)
        args = commands.call_args.args[0]
        self.assertIn("socks5h://127.0.0.1:1082", args)
        self.assertNotIn("-k", args)
        self.assertEqual(args[args.index("--noproxy") + 1], "")
        self.assertEqual(verifier.call_args.kwargs["expected_ports"], [1082])

    def test_unsafe_public_listener_is_rejected_before_refresh(self):
        snapshot = dict(self.snapshot)
        document = json.loads(snapshot["03_inbounds.json"])
        document["inbounds"][0]["listen"] = "0.0.0.0"
        snapshot["03_inbounds.json"] = sub.encode(document)
        with self.assertRaisesRegex(sub.SubscriptionError, "non-loopback"):
            sub.updated_outbounds(snapshot, self.source, self.nodes)

    def test_cli_writes_rejected_on_workstation_before_reading_secret(self):
        output = io.StringIO()
        with mock.patch.object(sub.platform, "system", return_value="Darwin"), \
                mock.patch.object(sub, "read") as read, contextlib.redirect_stderr(output):
            self.assertEqual(sub.main(["configure", "--source-file", "/secret", "--yes"]), 1)
        read.assert_not_called()

    def test_cli_does_not_print_network_exception_secrets(self):
        output = io.StringIO()
        with mock.patch.object(sub, "require_router"), mock.patch.object(sub, "locked", return_value=contextlib.nullcontext()), \
                mock.patch.object(sub, "refresh", side_effect=network.ProfileNetworkError(self.source["url"])), \
                contextlib.redirect_stderr(output):
            self.assertEqual(sub.main(["run"]), 1)
        self.assertNotIn(self.source["url"], output.getvalue())

    def test_cron_is_fixed_two_hour_schedule_with_no_secret(self):
        self.assertIn("0 */2 * * * root", sub.CRON)
        self.assertNotIn(self.source["url"], sub.CRON)
        self.assertNotIn("--source", sub.CRON)

    def scheduler_prerequisites(self):
        for relative in ("sbin/cron", "etc/init.d/S10cron", "bin/python3"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(0o755)
        (self.root / "etc/cron.d").mkdir(mode=0o755)

    def test_enable_installs_self_contained_worker_and_only_own_cron_file(self):
        self.scheduler_prerequisites()
        other = sub.cron_path(self.root).parent / "other-service"
        other.write_text("unrelated cron job\n")
        with mock.patch.object(sub, "refresh", return_value="unchanged"), \
                mock.patch.object(sub, "run_command"), mock.patch.object(sub, "verify_cron", return_value=True):
            sub.enable(self.root, SCRIPTS)
        self.assertEqual(sub.read(sub.cron_path(self.root)), sub.CRON.encode())
        self.assertEqual(other.read_text(), "unrelated cron job\n")
        for name in sub.BUNDLE:
            self.assertEqual(sub.read(self.root / "lib/routerkit/subscription" / name), (SCRIPTS / name).read_bytes())
        # Prove imports from the installed bundle away from the checkout.
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        worker = self.root / "lib/routerkit/subscription/routerkit-subscription.py"
        result = subprocess.run([sys.executable, str(worker), "--help"], cwd=self.root,
                                env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("configure", result.stdout)
        self.assertEqual(sub.cron_path(self.root).stat().st_mode & 0o777, 0o600)
        sub.disable(self.root)
        self.assertFalse(sub.cron_path(self.root).exists())
        self.assertTrue(sub.source_path(self.root).exists())
        self.assertEqual(other.read_text(), "unrelated cron job\n")

    def test_failed_initial_refresh_does_not_schedule_unattended_worker(self):
        self.scheduler_prerequisites()
        with mock.patch.object(sub, "refresh", side_effect=sub.SubscriptionError("failed")):
            with self.assertRaises(sub.SubscriptionError):
                sub.enable(self.root, SCRIPTS)
        self.assertFalse(sub.cron_path(self.root).exists())

    def test_failed_cron_start_removes_only_own_schedule(self):
        self.scheduler_prerequisites()
        with mock.patch.object(sub, "refresh"), mock.patch.object(sub, "run_command"), \
                mock.patch.object(sub, "verify_cron", return_value=False):
            with self.assertRaisesRegex(sub.SubscriptionError, "cron process"):
                sub.enable(self.root, SCRIPTS)
        self.assertFalse(sub.cron_path(self.root).exists())

    def test_conflicting_cron_entry_is_never_overwritten(self):
        self.scheduler_prerequisites()
        path = sub.cron_path(self.root)
        path.write_text("unknown user entry\n")
        path.chmod(0o600)
        with self.assertRaises(sub.SubscriptionError):
            sub.enable(self.root, SCRIPTS)
        with self.assertRaises(sub.SubscriptionError):
            sub.disable(self.root)
        self.assertEqual(path.read_text(), "unknown user entry\n")

    def test_cron_verification_requires_entware_executable_identity(self):
        self.scheduler_prerequisites()
        proc = self.root / "proc/123"
        proc.mkdir(parents=True)
        (proc / "exe").symlink_to(self.root / "bin/python3")
        self.assertFalse(sub.verify_cron(self.root, proc.parent))
        (proc / "exe").unlink()
        (proc / "exe").symlink_to(self.root / "sbin/cron")
        self.assertTrue(sub.verify_cron(self.root, proc.parent))

    def test_wrapper_delegates_without_interpreting_subscription(self):
        import routerkit
        args = routerkit.parse_args(["subscription", "configure", "--source-file", "/private/source", "--yes"])
        command = routerkit.build_command(args, SCRIPTS.parent)
        self.assertEqual(command[2:], ["configure", "--source-file", "/private/source", "--yes"])

    def test_wrapper_forwards_protected_explicit_selection(self):
        import routerkit
        args = routerkit.parse_args(["subscription", "configure", "--source-file", "/private/source",
                                     "--selection-file", "/private/selected-outbounds.json", "--yes"])
        command = routerkit.build_command(args, SCRIPTS.parent)
        self.assertIn("--selection-file", command)
        self.assertEqual(command[command.index("--selection-file") + 1], "/private/selected-outbounds.json")

    def test_selection_file_is_rejected_outside_configure(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output), mock.patch.object(sub, "require_router") as guard:
            self.assertEqual(sub.main(["run", "--selection-file", "/private/selection"]), 1)
        guard.assert_not_called()


if __name__ == "__main__":
    unittest.main()
