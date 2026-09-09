import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from routerkit_netcraze_live import (
    LiveAdapterError,
    apply_live_plan,
    build_live_plan,
    parse_running_config,
    policy_create_commands,
    proxy_create_commands,
)
from routerkit_netcraze_plan import LocalEndpointManifest, LocalProxyProfile


DEVICE_MAC = "10:22:33:44:55:66"


def manifest_three():
    return LocalEndpointManifest(
        (
            LocalProxyProfile(1, "primary", "127.0.0.1", 1082, True),
            LocalProxyProfile(2, "fallback-1", "127.0.0.1", 1083, True),
            LocalProxyProfile(3, "fallback-2", "127.0.0.1", 1084, True),
        )
    )


class FakeNdmc:
    def __init__(self):
        self.proxies = {}
        self.policies = {}
        self.assignments = {}
        self.commands = []
        self.default_line = "ip hotspot default-policy permit"

    def render(self):
        lines = [self.default_line, "!"]
        for object_id in sorted(self.proxies):
            value = self.proxies[object_id]
            lines.extend(
                [
                    "interface %s" % object_id,
                    " description %s" % value.get("description", ""),
                    " security-level public",
                    " proxy protocol %s" % value.get("protocol", ""),
                    " proxy upstream %s %s" % (value.get("host", ""), value.get("port", 0)),
                ]
            )
            if value.get("up"):
                lines.append(" up")
            lines.append("!")
        for object_id in sorted(self.policies):
            value = self.policies[object_id]
            lines.extend(
                [
                    "ip policy %s" % object_id,
                    " description %s" % value.get("description", ""),
                ]
            )
            for interface in value.get("interfaces", []):
                lines.append(" permit global %s" % interface)
            lines.append("!")
        if self.assignments:
            lines.append("ip hotspot")
            for mac, policy in sorted(self.assignments.items()):
                lines.append(" host %s policy %s" % (mac, policy))
            lines.append("!")
        return "\n".join(lines) + "\n"

    def command(self, command):
        self.commands.append(command)
        if command == "show running-config":
            return self.render()
        if command == "system configuration save":
            return ""
        parts = command.split()
        if parts[:2] == ["no", "interface"]:
            self.proxies.pop(parts[2], None)
            return ""
        if parts[:3] == ["no", "ip", "policy"]:
            self.policies.pop(parts[3], None)
            return ""
        if parts[:4] == ["no", "ip", "hotspot", "host"]:
            self.assignments.pop(parts[4].lower(), None)
            return ""
        if parts[:2] == ["interface", parts[1]] and parts[1].startswith("Proxy"):
            object_id = parts[1]
            value = self.proxies.setdefault(object_id, {})
            if parts[2] == "description":
                value["description"] = " ".join(parts[3:])
            elif parts[2:4] == ["security-level", "public"]:
                pass
            elif parts[2:5] == ["proxy", "protocol", "socks5"]:
                value["protocol"] = "socks5"
            elif parts[2:4] == ["proxy", "upstream"]:
                value["host"] = parts[4]
                value["port"] = int(parts[5])
            elif parts[2] == "up":
                value["up"] = True
            return ""
        if parts[:2] == ["ip", "policy"]:
            object_id = parts[2]
            value = self.policies.setdefault(object_id, {"interfaces": []})
            if parts[3] == "description":
                value["description"] = " ".join(parts[4:])
            elif parts[3:5] == ["permit", "global"]:
                value.setdefault("interfaces", []).append(parts[5])
            return ""
        if parts[:3] == ["ip", "hotspot", "host"]:
            self.assignments[parts[3].lower()] = parts[5]
            return ""
        raise AssertionError("unexpected command: %s" % command)


class LiveParserTests(unittest.TestCase):
    def test_parses_proxy_policy_assignment_and_default_guard(self):
        text = """\
ip hotspot default-policy permit
!
interface Proxy2
 description RouterKit-SOCKS-1082
 security-level public
 proxy protocol socks5
 proxy upstream 127.0.0.1 1082
 up
!
ip policy Policy4
 description RouterKit-Policy-1082
 permit global Proxy2
!
ip hotspot
 host 10:22:33:44:55:66 policy Policy4
!
"""
        state = parse_running_config(text)
        self.assertEqual(state.proxies[0].object_id, "Proxy2")
        self.assertEqual(state.proxies[0].port, 1082)
        self.assertEqual(state.policies[0].global_interfaces, ("Proxy2",))
        self.assertEqual(state.assignment_map[DEVICE_MAC], "Policy4")
        self.assertEqual(state.default_guard, ("ip hotspot default-policy permit",))


class LivePlanTests(unittest.TestCase):
    def test_empty_state_allocates_native_slots_deterministically(self):
        state = parse_running_config("ip hotspot default-policy permit\n")
        plan = build_live_plan(manifest_three(), state)
        self.assertEqual([item.proxy_id for item in plan.bindings], ["Proxy0", "Proxy1", "Proxy2"])
        self.assertEqual([item.policy_id for item in plan.bindings], ["Policy0", "Policy1", "Policy2"])
        self.assertTrue(all(item.proxy_action == "create" for item in plan.bindings))
        self.assertTrue(all(item.policy_action == "create" for item in plan.bindings))
        self.assertFalse(plan.default_policy_targeted)

    def test_exact_existing_objects_are_reused(self):
        fake = FakeNdmc()
        fake.proxies["Proxy0"] = {
            "description": "RouterKit-SOCKS-1082",
            "protocol": "socks5",
            "host": "127.0.0.1",
            "port": 1082,
            "up": True,
        }
        fake.policies["Policy0"] = {
            "description": "RouterKit-Policy-1082",
            "interfaces": ["Proxy0"],
        }
        one = LocalEndpointManifest((LocalProxyProfile(1, "primary", "127.0.0.1", 1082, True),))
        plan = build_live_plan(one, parse_running_config(fake.render()))
        self.assertEqual(plan.bindings[0].proxy_action, "reuse")
        self.assertEqual(plan.bindings[0].policy_action, "reuse")

    def test_conflicting_owned_name_fails_closed(self):
        fake = FakeNdmc()
        fake.proxies["Proxy0"] = {
            "description": "RouterKit-SOCKS-1082",
            "protocol": "http",
            "host": "127.0.0.1",
            "port": 1082,
            "up": True,
        }
        with self.assertRaises(LiveAdapterError):
            build_live_plan(manifest_three(), parse_running_config(fake.render()))

    def test_device_move_requires_explicit_authorization(self):
        fake = FakeNdmc()
        fake.assignments[DEVICE_MAC] = "Policy9"
        state = parse_running_config(fake.render())
        with self.assertRaises(LiveAdapterError):
            build_live_plan(manifest_three(), state, device_mac=DEVICE_MAC, profile_slot=2)
        plan = build_live_plan(
            manifest_three(), state, device_mac=DEVICE_MAC, profile_slot=2, allow_move=True
        )
        self.assertEqual(plan.assignment_action, "move")

    def test_rendered_commands_never_target_default_policy(self):
        plan = build_live_plan(manifest_three(), parse_running_config("ip hotspot default-policy permit\n"))
        profile = manifest_three().profiles[0]
        commands = proxy_create_commands(plan.bindings[0], profile) + policy_create_commands(plan.bindings[0], profile)
        joined = "\n".join(commands).casefold()
        self.assertNotIn("default-policy", joined)
        self.assertNotIn("xkeen", joined)
        self.assertNotIn("tproxy", joined)
        self.assertNotIn("redirect", joined)


class LiveApplyTests(unittest.TestCase):
    def test_create_assign_save_verify_and_idempotent_rerun(self):
        fake = FakeNdmc()
        manifest = manifest_three()
        before = parse_running_config(fake.render())
        plan = build_live_plan(manifest, before, device_mac=DEVICE_MAC, profile_slot=2)
        with tempfile.TemporaryDirectory() as directory:
            result = apply_live_plan(
                fake,
                manifest,
                plan,
                device_mac=DEVICE_MAC,
                backup_root=Path(directory),
            )
        self.assertTrue(result.verified)
        self.assertEqual(len(result.created_proxies), 3)
        self.assertEqual(len(result.created_policies), 3)
        self.assertTrue(result.assignment_changed)
        self.assertEqual(fake.assignments[DEVICE_MAC], "Policy1")
        self.assertEqual(fake.default_line, "ip hotspot default-policy permit")

        rerun = build_live_plan(
            manifest,
            parse_running_config(fake.render()),
            device_mac=DEVICE_MAC,
            profile_slot=2,
        )
        self.assertTrue(all(item.proxy_action == "reuse" for item in rerun.bindings))
        self.assertTrue(all(item.policy_action == "reuse" for item in rerun.bindings))
        self.assertEqual(rerun.assignment_action, "reuse")


if __name__ == "__main__":
    unittest.main()
