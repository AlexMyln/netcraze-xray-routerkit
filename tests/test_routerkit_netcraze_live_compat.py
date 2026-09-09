import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import routerkit_netcraze_live as core
from routerkit_netcraze_plan import LocalEndpointManifest, LocalProxyProfile


def load_public_entrypoint():
    path = SCRIPTS / "routerkit-netcraze-live.py"
    spec = importlib.util.spec_from_file_location("routerkit_netcraze_live_public_compat_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def manifest_three():
    return LocalEndpointManifest(
        (
            LocalProxyProfile(1, "primary", "127.0.0.1", 1082, True),
            LocalProxyProfile(2, "fallback-1", "127.0.0.1", 1083, True),
            LocalProxyProfile(3, "fallback-2", "127.0.0.1", 1084, True),
        )
    )


class FirstHardwareInstallReuseTests(unittest.TestCase):
    def setUp(self):
        load_public_entrypoint()

    def test_human_named_live_objects_are_reused_by_semantics(self):
        state = core.LiveState(
            proxies=(
                core.ProxyState("Proxy0", "XRAY-NL", "socks5", "127.0.0.1", 1082, True, False),
                core.ProxyState("Proxy1", "XRAY-SMART-RU", "socks5", "127.0.0.1", 1083, True, False),
                core.ProxyState("Proxy2", "XRAY-US", "socks5", "127.0.0.1", 1084, True, False),
            ),
            policies=(
                core.PolicyState("Policy0", "VPN-NL", ("Proxy0",)),
                core.PolicyState("Policy1", "VPN-SMART-RU", ("Proxy1",)),
                core.PolicyState("Policy2", "VPN-US", ("Proxy2",)),
            ),
            assignments=(),
            default_guard=(),
        )
        plan = core.build_live_plan(manifest_three(), state)
        self.assertEqual([item.proxy_id for item in plan.bindings], ["Proxy0", "Proxy1", "Proxy2"])
        self.assertEqual([item.policy_id for item in plan.bindings], ["Policy0", "Policy1", "Policy2"])
        self.assertTrue(all(item.proxy_action == "reuse" for item in plan.bindings))
        self.assertTrue(all(item.policy_action == "reuse" for item in plan.bindings))

    def test_multiple_semantic_proxy_matches_fail_closed(self):
        state = core.LiveState(
            proxies=(
                core.ProxyState("Proxy0", "XRAY-NL", "socks5", "127.0.0.1", 1082, True, False),
                core.ProxyState("Proxy3", "duplicate", "socks5", "127.0.0.1", 1082, True, False),
            ),
            policies=(),
            assignments=(),
            default_guard=(),
        )
        one = LocalEndpointManifest((LocalProxyProfile(1, "primary", "127.0.0.1", 1082, True),))
        with self.assertRaises(core.LiveAdapterError):
            core.build_live_plan(one, state)

    def test_reused_human_names_pass_semantic_verification(self):
        state = core.LiveState(
            proxies=(core.ProxyState("Proxy0", "XRAY-NL", "socks5", "127.0.0.1", 1082, True, False),),
            policies=(core.PolicyState("Policy0", "VPN-NL", ("Proxy0",)),),
            assignments=(),
            default_guard=(),
        )
        one = LocalEndpointManifest((LocalProxyProfile(1, "primary", "127.0.0.1", 1082, True),))
        plan = core.build_live_plan(one, state)
        core._verify_plan_applied(one, plan, state, device_mac=None)


if __name__ == "__main__":
    unittest.main()
