import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_entrypoint():
    path = SCRIPTS / "routerkit-netcraze-live.py"
    spec = importlib.util.spec_from_file_location("routerkit_netcraze_live_entrypoint_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class LiveEntrypointTests(unittest.TestCase):
    def test_status_does_not_require_hardware_contract_argument(self):
        entry = load_entrypoint()
        with mock.patch.object(entry, "core_main", return_value=0) as core:
            code = entry.main(["status", "--json"])
        self.assertEqual(code, 0)
        core.assert_called_once_with(["status", "--json"])

    def test_apply_requires_explicit_supported_contract(self):
        entry = load_entrypoint()
        with mock.patch.object(entry, "core_main", side_effect=AssertionError("must not enter live core")):
            code = entry.main(["apply", "--manifest-file", "/private/manifest.json", "--yes"])
        self.assertEqual(code, 2)

    def test_apply_rejects_unknown_contract(self):
        entry = load_entrypoint()
        with mock.patch.object(entry, "core_main", side_effect=AssertionError("must not enter live core")):
            code = entry.main([
                "apply",
                "--manifest-file",
                "/private/manifest.json",
                "--yes",
                "--contract",
                "some-other-router",
            ])
        self.assertEqual(code, 2)

    def test_apply_forwards_explicit_supported_contract(self):
        entry = load_entrypoint()
        for contract in entry.SUPPORTED_CONTRACTS:
            with self.subTest(contract=contract):
                argv = ["apply", "--manifest-file", "/private/manifest.json", "--yes", "--contract", contract]
                with mock.patch.object(entry, "core_main", return_value=0) as core:
                    code = entry.main(argv)
                self.assertEqual(code, 0)
                core.assert_called_once_with(argv)

    def test_core_and_public_cli_reject_unlisted_firmware_and_other_models(self):
        entry = load_entrypoint()
        for contract in (
            "nc3812-netcrazeos-5.1.6", "nc3812-netcrazeos-5.1.7",
            "nc3812-netcrazeos-5.1.80", "nc3810-netcrazeos-5.1.8",
        ):
            with self.subTest(contract=contract), mock.patch.object(entry, "core_main") as main:
                argv = ["apply", "--manifest-file", "/private/manifest.json", "--contract", contract]
                self.assertEqual(entry.main(argv), 2)
                main.assert_not_called()
                with self.assertRaises(SystemExit) as raised:
                    entry.core.parse_args(argv)
                self.assertEqual(raised.exception.code, 2)

    def test_core_accepts_both_exact_contracts_and_keeps_legacy_default(self):
        entry = load_entrypoint()
        self.assertEqual(entry.core.parse_args(["status"]).contract, "nc3812-netcrazeos-5.1.5")
        for contract in entry.SUPPORTED_CONTRACTS:
            with self.subTest(contract=contract):
                parsed = entry.core.parse_args(["apply", "--manifest-file", "/private/manifest.json", "--contract", contract])
                self.assertEqual(parsed.contract, contract)


if __name__ == "__main__":
    unittest.main()
