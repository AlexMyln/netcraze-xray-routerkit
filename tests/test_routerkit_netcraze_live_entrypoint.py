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
        argv = [
            "apply",
            "--manifest-file",
            "/private/manifest.json",
            "--yes",
            "--contract",
            entry.SUPPORTED_CONTRACT,
        ]
        with mock.patch.object(entry, "core_main", return_value=0) as core:
            code = entry.main(argv)
        self.assertEqual(code, 0)
        core.assert_called_once_with(argv)


if __name__ == "__main__":
    unittest.main()
