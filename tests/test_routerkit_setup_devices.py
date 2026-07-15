import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures" / "devices"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

def load_cli():
    import importlib.util

    path = SCRIPTS / "routerkit.py"
    name = "routerkit_setup_devices_cli"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cli = load_cli()


def private_file(path, text):
    path.write_text(text, encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    return path


class SetupDeviceArgumentTests(unittest.TestCase):
    def test_fixture_options_require_explicit_discovery(self):
        for form in (
            ["setup", "--device-inventory-file", "/private/inventory.json"],
            ["setup", "--device-choice", "0"],
        ):
            with self.subTest(form=form), self.assertRaises(cli.RouterkitCliError):
                cli.validate_setup_args(cli.parse_args(form))

    def test_devices_subcommand_delegates_to_wrapper(self):
        args = cli.parse_args(
            [
                "devices",
                "discover",
                "--inventory-file",
                "/private/inventory.json",
                "--json",
                "--public-evidence",
                "--redaction-salt",
                "salt",
                "--choice",
                "0",
            ]
        )

        self.assertEqual(
            cli.build_command(args, ROOT),
            [
                sys.executable,
                str(SCRIPTS / "routerkit-devices.py"),
                "discover",
                "--inventory-file",
                "/private/inventory.json",
                "--json",
                "--public-evidence",
                "--redaction-salt",
                "salt",
                "--choice",
                "0",
            ],
        )


class SetupDeviceDryRunTests(unittest.TestCase):
    def test_dry_run_renders_stage_without_running_discovery(self):
        stdout = io.StringIO()
        with mock.patch.object(cli, "run_setup_selection_stage", side_effect=AssertionError("no discovery")):
            with contextlib.redirect_stdout(stdout):
                code = cli.main(["--dry-run", "setup", "--discover-devices"])

        self.assertEqual(code, 0)
        self.assertIn("explicit read-only device discovery/selection", stdout.getvalue())


class SetupDeviceExecutionTests(unittest.TestCase):
    def make_inputs(self, directory):
        root = Path(directory)
        profiles = private_file(root / "profiles.json", '{"profiles": []}\n')
        inventory = private_file(root / "inventory.json", (FIXTURES / "mixed-inventory.json").read_text(encoding="utf-8"))
        return profiles, inventory

    def test_selection_runs_after_strict_plan_and_before_plan_summary(self):
        events = []
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            profiles, inventory = self.make_inputs(directory)
            args = cli.parse_args(
                [
                    "setup",
                    "--reuse-profiles",
                    str(profiles),
                    "--discover-devices",
                    "--device-inventory-file",
                    str(inventory),
                    "--device-choice",
                    "0",
                ]
            )

            def fake_run_steps(steps, **_kwargs):
                events.extend(step.name for step in steps)
                return 0

            def fake_selection_stage(**kwargs):
                events.append("device selection")
                self.assertEqual(kwargs["inventory_file"], str(inventory))
                self.assertEqual(kwargs["choice"], 0)
                return 0, SimpleNamespace(selected=False)

            with mock.patch.object(cli, "run_steps", side_effect=fake_run_steps):
                with mock.patch.object(cli, "run_setup_selection_stage", side_effect=fake_selection_stage):
                    with contextlib.redirect_stdout(stdout):
                        code = cli.run_setup(args, ROOT)

        self.assertEqual(code, 0)
        self.assertEqual(events, ["generator", "strict plan", "device selection"])
        self.assertIn("no Netcraze policy/device assignment was written", stdout.getvalue())

    def test_discovery_failure_stops_before_confirmation_and_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            profiles, inventory = self.make_inputs(directory)
            args = cli.parse_args(
                [
                    "setup",
                    "--reuse-profiles",
                    str(profiles),
                    "--apply",
                    "--discover-devices",
                    "--device-inventory-file",
                    str(inventory),
                ]
            )

            with mock.patch.object(cli, "run_steps", return_value=0):
                with mock.patch.object(cli, "run_setup_selection_stage", return_value=(3, None)):
                    with mock.patch.object(cli, "confirm_setup_apply", side_effect=AssertionError("no confirmation")):
                        with mock.patch.object(cli, "build_router_apply_steps", side_effect=AssertionError("no apply")):
                            self.assertEqual(cli.run_setup(args, ROOT), 3)

    def test_default_setup_path_does_not_run_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            profiles = private_file(Path(directory) / "profiles.json", '{"profiles": []}\n')
            args = cli.parse_args(["setup", "--reuse-profiles", str(profiles)])

            with mock.patch.object(cli, "run_steps", return_value=0):
                with mock.patch.object(cli, "run_setup_selection_stage", side_effect=AssertionError("no discovery")):
                    self.assertEqual(cli.run_setup(args, ROOT), 0)


if __name__ == "__main__":
    unittest.main()
