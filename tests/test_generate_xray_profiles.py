import base64
import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "generate-xray-profiles.py"
    spec = importlib.util.spec_from_file_location("generate_xray_profiles", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


generator = load_module()
import routerkit_profile_network as network
import routerkit_private_io as private_io


def fake_vless_link(name="Example", host="example.net", security="reality", network="tcp", user_id=None):
    scheme = "vl" + "ess"
    user_id = user_id or str(uuid.uuid4())
    query_parts = [
        f"security={security}",
        f"type={network}",
        "fp=chrome",
        ("pb" + "k") + "=" + "A" * 43,
        ("si" + "d") + "=00",
        f"sni={host}",
        "flow=xtls-rprx-vision",
    ]
    query = "&".join(query_parts)
    return f"{scheme}://{user_id}@{host}:443?{query}#{name}"


class ExtractVlessLinksTests(unittest.TestCase):
    def test_extracts_plain_text_link(self):
        link = fake_vless_link()

        self.assertEqual(generator.extract_vless_links(f"node: {link}"), [link])

    def test_extracts_base64_subscription_link(self):
        link = fake_vless_link()
        encoded = base64.b64encode((link + "\n").encode("utf-8")).decode("ascii")

        self.assertEqual(generator.extract_vless_links(encoded), [link])

    def test_extracts_json_link(self):
        link = fake_vless_link()
        text = json.dumps({"profiles": [{"url": link}]})

        self.assertEqual(generator.extract_vless_links(text), [link])


class SubscriptionAcquisitionTests(unittest.TestCase):
    def test_subscription_url_uses_safe_shared_resolver(self):
        source = "https://source.example/sub?token=synthetic"
        result = network.ResolvedPayload("payload", 7, 0)
        with mock.patch.object(generator, "resolve_https_source", return_value=result) as resolver:
            self.assertEqual(generator.get_subscription_text({"subscription_url": source}), "payload")
        resolver.assert_called_once_with(source)

    def test_environment_subscription_url_uses_safe_shared_resolver(self):
        source = "https://source.example/from-env"
        result = network.ResolvedPayload("payload", 7, 0)
        with mock.patch.dict(os.environ, {"SYNTHETIC_SUBSCRIPTION_URL": source}):
            with mock.patch.object(generator, "resolve_https_source", return_value=result) as resolver:
                self.assertEqual(
                    generator.get_subscription_text(
                        {"subscription_url_env": "SYNTHETIC_SUBSCRIPTION_URL"}
                    ),
                    "payload",
                )
        resolver.assert_called_once_with(source)

    def test_subscription_urls_normalize_outer_whitespace(self):
        source = "https://source.example/sub?token=synthetic"
        surrounded = " \t" + source + "\r\n"
        result = network.ResolvedPayload("payload", 7, 0)
        with mock.patch.object(generator, "resolve_https_source", return_value=result) as resolver:
            self.assertEqual(generator.get_subscription_text({"subscription_url": surrounded}), "payload")
        resolver.assert_called_once_with(source)

        with mock.patch.dict(os.environ, {"SYNTHETIC_SUBSCRIPTION_URL": surrounded}):
            with mock.patch.object(generator, "resolve_https_source", return_value=result) as resolver:
                self.assertEqual(
                    generator.get_subscription_text(
                        {"subscription_url_env": "SYNTHETIC_SUBSCRIPTION_URL"}
                    ),
                    "payload",
                )
        resolver.assert_called_once_with(source)

    def test_internal_whitespace_multiline_and_empty_urls_fail_generically(self):
        values = (
            "https://source.example/pa th?token=DO_NOT_LEAK_SPACE",
            "https://source.example/one\nhttps://source.example/two",
            " \r\n\t",
        )
        for value in values:
            with self.subTest(value=repr(value)), self.assertRaises(SystemExit) as caught:
                generator.get_subscription_text({"subscription_url": value})
            self.assertNotIn(value, str(caught.exception))

    def test_generator_interrupt_is_not_rewritten_as_resolver_error(self):
        def interrupting_resolver(*_args, **_kwargs):
            raise KeyboardInterrupt

        def call_real_resolver(value):
            return network.resolve_https_source(value, resolver=interrupting_resolver)

        with mock.patch.object(generator, "resolve_https_source", side_effect=call_real_resolver):
            with self.assertRaises(KeyboardInterrupt):
                generator.get_subscription_text({"subscription_url": "https://source.example/sub"})

    def test_network_failure_is_generic_and_hides_source(self):
        source = "https://source.example/path?token=DO_NOT_LEAK_GENERATOR"
        with mock.patch.object(
            generator,
            "resolve_https_source",
            side_effect=network.UrlPolicyError("HTTPS source URL is not allowed by policy."),
        ):
            with self.assertRaises(SystemExit) as caught:
                generator.get_subscription_text({"subscription_url": source})
        self.assertNotIn(source, str(caught.exception))

    def test_local_file_and_direct_vless_paths_remain_offline(self):
        link = fake_vless_link()
        with tempfile.TemporaryDirectory() as directory:
            source_file = Path(directory) / "source.txt"
            source_file.write_text(link, encoding="utf-8")
            with mock.patch.object(
                generator, "resolve_https_source", side_effect=AssertionError("network attempted")
            ):
                self.assertEqual(
                    generator.get_subscription_text({"subscription_file": str(source_file)}), link
                )
                self.assertEqual(generator.get_subscription_text({"vless": link}), link)

    def test_urllib_request_urlopen_is_absent_from_generator(self):
        source = (ROOT / "scripts" / "generate-xray-profiles.py").read_text(encoding="utf-8")
        self.assertNotIn("urllib.request", source)
        self.assertNotIn("urlopen", source)


class ParseVlessTests(unittest.TestCase):
    def test_parses_expected_fields(self):
        node = generator.parse_vless(fake_vless_link())

        self.assertEqual(node["host"], "example.net")
        self.assertEqual(node["port"], 443)
        self.assertEqual(node["security"], "reality")
        self.assertEqual(node["network"], "tcp")
        self.assertEqual(node["sni"], "example.net")
        self.assertEqual(node["fp"], "chrome")
        self.assertEqual(node["pbk"], "A" * 43)
        self.assertEqual(node["sid"], "00")
        self.assertEqual(node["flow"], "xtls-rprx-vision")


class SelectNodeTests(unittest.TestCase):
    def setUp(self):
        self.links = [
            fake_vless_link(name="Alpha", host="alpha.example.net"),
            fake_vless_link(name="Beta", host="beta.example.com"),
            fake_vless_link(name="Grpc", network="grpc"),
        ]

    def test_selects_by_index(self):
        node = generator.select_node(self.links, {"index": 1})

        self.assertEqual(node["name"], "Beta")

    def test_selects_by_name_contains(self):
        node = generator.select_node(self.links, {"name_contains": "alp"})

        self.assertEqual(node["host"], "alpha.example.net")

    def test_selects_by_host_contains(self):
        node = generator.select_node(self.links, {"host_contains": "example.com"})

        self.assertEqual(node["name"], "Beta")

    def test_requires_security_and_network(self):
        node = generator.select_node(
            self.links,
            {
                "require_security": "reality",
                "require_network": "tcp",
                "host_contains": "alpha",
            },
        )

        self.assertEqual(node["network"], "tcp")
        self.assertEqual(node["security"], "reality")


class BuildConfigTests(unittest.TestCase):
    @staticmethod
    def manifest_value(profile_count=1):
        return generator.build_local_endpoint_manifest(
            [
                {"name": "raw-profile-%d" % slot, "port": 1081 + slot}
                for slot in range(1, profile_count + 1)
            ]
        )

    @staticmethod
    def write_prior_manifest(path, profile_count=1):
        path.write_text(
            json.dumps(BuildConfigTests.manifest_value(profile_count), indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(path, 0o600)

    def test_build_inbounds_use_loopback_socks_ports(self):
        profiles = [{"name": "alpha", "port": 1082}, {"name": "beta", "port": 1083}]

        inbounds = generator.build_inbounds(profiles)["inbounds"]

        self.assertEqual([inbound["listen"] for inbound in inbounds], ["127.0.0.1", "127.0.0.1"])
        self.assertEqual([inbound["port"] for inbound in inbounds], [1082, 1083])
        self.assertEqual([inbound["protocol"] for inbound in inbounds], ["socks", "socks"])

    def test_build_routing_maps_inbound_to_expected_outbound(self):
        profiles = [{"name": "alpha", "port": 1082}, {"name": "beta", "port": 1083}]

        rules = generator.build_routing(profiles)["routing"]["rules"]

        self.assertEqual(
            rules,
            [
                {"type": "field", "inboundTag": ["socks-alpha"], "outboundTag": "vless-alpha"},
                {"type": "field", "inboundTag": ["socks-beta"], "outboundTag": "vless-beta"},
            ],
        )

    def test_profiles_document_from_shared_core_generates_fragments_and_safe_manifest(self):
        import routerkit_profile_source as core

        nodes = [core.parse_vless(fake_vless_link(name=name, host=f"{name}.example")) for name in ("one", "two", "three")]
        document = core.build_profiles_document(core.select_nodes(nodes, 1, [2, 3]))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profiles = root / "profiles-input.json"
            output = root / "generated"
            core.write_private_json(profiles, document)
            old_argv = sys.argv
            try:
                sys.argv = ["generate-xray-profiles.py", "--profiles", str(profiles), "--out", str(output)]
                self.assertEqual(generator.main(), 0)
            finally:
                sys.argv = old_argv
            self.assertEqual(
                sorted(path.name for path in output.iterdir()),
                [
                    "03_inbounds.json",
                    "04_outbounds.json",
                    "05_routing.json",
                    "routerkit-local-endpoints.json",
                ],
            )
            manifest = json.loads((output / "routerkit-local-endpoints.json").read_text())
            self.assertEqual(manifest["schema"], "routerkit.local-endpoints.v1")
            self.assertEqual([item["port"] for item in manifest["profiles"]], [1082, 1083, 1084])
            self.assertEqual(
                [item["label"] for item in manifest["profiles"]],
                ["primary", "fallback-1", "fallback-2"],
            )
            serialized = json.dumps(manifest).casefold()
            for forbidden in ("uuid", "publickey", "shortid", "subscription", "remote", "sni"):
                self.assertNotIn(forbidden, serialized)
            if os.name == "posix":
                self.assertEqual((output / "routerkit-local-endpoints.json").stat().st_mode & 0o777, 0o600)

    def test_manifest_rejects_ports_duplicates_and_unsafe_destination(self):
        with self.assertRaises(SystemExit):
            generator.build_local_endpoint_manifest([{"name": "bad", "port": 9999}])
        with self.assertRaises(SystemExit):
            generator.build_local_endpoint_manifest(
                [{"name": "one", "port": 1082}, {"name": "two", "port": 1082}]
            )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "manifest.json"
            real = root / "real.json"
            real.write_text("{}", encoding="utf-8")
            target.symlink_to(real)
            with self.assertRaises(SystemExit):
                generator.write_private_json_atomic(
                    target,
                    generator.build_local_endpoint_manifest([{"name": "one", "port": 1082}]),
                )

    def test_manifest_labels_are_code_owned_and_ignore_raw_profile_names(self):
        raw_names = (
            "host.example.net",
            "123e4567" + "-e89b-12d3-a456-" + "426614174000",
            "SyntheticProvider",
            "vless://synthetic",
            "Юникод",
            "escape-\x1b-control",
            "x" * 10000,
        )
        for raw_name in raw_names:
            with self.subTest(raw_name=repr(raw_name)):
                manifest = generator.build_local_endpoint_manifest(
                    [{"name": raw_name, "port": 1082}]
                )
                serialized = json.dumps(manifest, ensure_ascii=False)
                self.assertEqual(manifest["profiles"][0]["label"], "primary")
                self.assertNotIn(raw_name, serialized)

    def test_manifest_publication_absent_and_valid_prior_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "manifest.json"
            generator.write_private_json_atomic(target, self.manifest_value(1))
            self.assertEqual(json.loads(target.read_text()), self.manifest_value(1))
            self.write_prior_manifest(target, 1)
            generator.write_private_json_atomic(target, self.manifest_value(2))
            self.assertEqual(json.loads(target.read_text()), self.manifest_value(2))
            if os.name == "posix":
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_unrecognized_existing_targets_are_never_replaced(self):
        invalid_values = (
            b"UNRELATED",
            b"{not-json",
            json.dumps({"schema": "other", "profiles": []}).encode("utf-8"),
        )
        for original in invalid_values:
            with self.subTest(original=original):
                with tempfile.TemporaryDirectory() as directory:
                    target = Path(directory) / "manifest.json"
                    target.write_bytes(original)
                    os.chmod(target, 0o600)
                    with self.assertRaises(SystemExit):
                        generator.write_private_json_atomic(target, self.manifest_value())
                    self.assertEqual(target.read_bytes(), original)

    def test_unsafe_target_types_and_permissions_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            self.write_prior_manifest(source)

            targets = []
            symlink = root / "symlink.json"
            symlink.symlink_to(source)
            targets.append(symlink)
            dangling = root / "dangling.json"
            dangling.symlink_to(root / "missing.json")
            targets.append(dangling)
            hardlink = root / "hardlink.json"
            os.link(source, hardlink)
            targets.append(hardlink)
            fifo = root / "fifo.json"
            os.mkfifo(fifo)
            targets.append(fifo)
            directory_target = root / "directory.json"
            directory_target.mkdir()
            targets.append(directory_target)

            for target in targets:
                with self.subTest(target=target.name), self.assertRaises(SystemExit):
                    generator.write_private_json_atomic(target, self.manifest_value())

        if os.name == "posix":
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "manifest.json"
                self.write_prior_manifest(target)
                os.chmod(target, 0o644)
                original = target.read_bytes()
                with self.assertRaises(SystemExit):
                    generator.write_private_json_atomic(target, self.manifest_value(2))
                self.assertEqual(target.read_bytes(), original)

    def test_unsafe_or_symlink_parent_is_rejected(self):
        if os.name == "posix":
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                unsafe = root / "unsafe"
                unsafe.mkdir()
                os.chmod(unsafe, 0o777)
                with self.assertRaises(SystemExit):
                    generator.write_private_json_atomic(
                        unsafe / "manifest.json", self.manifest_value()
                    )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir()
            linked = root / "linked"
            linked.symlink_to(real, target_is_directory=True)
            with self.assertRaises(SystemExit):
                generator.write_private_json_atomic(
                    linked / "manifest.json", self.manifest_value()
                )

    @unittest.skipUnless(os.name == "posix", "POSIX mode contract")
    def test_private_directories_require_exact_0700_without_modification_or_residue(self):
        rejected_modes = (
            0o755,
            0o750,
            0o711,
            0o701,
            0o770,
            0o777,
            0o500,
            0o300,
            0o1700,
            0o2700,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            accepted = root / "accepted"
            private_io.ensure_private_directory(
                accepted, description="Synthetic private directory"
            )
            self.assertEqual(accepted.stat().st_mode & 0o7777, 0o700)
            private_io.ensure_private_directory(
                accepted, description="Synthetic private directory"
            )
            fd = private_io._open_private_directory(
                accepted, description="Synthetic private directory"
            )
            os.close(fd)

            for mode in rejected_modes:
                metadata = type(
                    "SyntheticDirectoryMetadata",
                    (),
                    {
                        "st_mode": stat.S_IFDIR | mode,
                        "st_uid": os.geteuid(),
                    },
                )()
                with self.subTest(metadata_mode=oct(mode)):
                    with self.assertRaises(private_io.PrivateFileError):
                        private_io._validate_private_directory_metadata(
                            metadata,
                            description="Synthetic private directory",
                        )

            for mode in rejected_modes:
                with self.subTest(mode=oct(mode)):
                    candidate = root / ("mode-%04o" % mode)
                    candidate.mkdir(mode=0o700)
                    os.chmod(candidate, mode)
                    before = candidate.stat().st_mode & 0o7777
                    if before == 0o700:
                        candidate.rmdir()
                        continue
                    with self.assertRaises(private_io.PrivateFileError):
                        private_io.ensure_private_directory(
                            candidate,
                            description="Synthetic private directory",
                        )
                    with self.assertRaises(private_io.PrivateFileError):
                        private_io._open_private_directory(
                            candidate,
                            description="Synthetic private directory",
                        )
                    with self.assertRaises(SystemExit):
                        generator.write_private_json_atomic(
                            candidate / "manifest.json",
                            self.manifest_value(),
                        )
                    self.assertEqual(candidate.stat().st_mode & 0o7777, before)
                    os.chmod(candidate, 0o700)
                    self.assertEqual(list(candidate.iterdir()), [])

    @unittest.skipUnless(os.name == "posix", "POSIX mode contract")
    def test_direct_generator_rejects_reused_nonprivate_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profiles = root / "profiles.json"
            profiles.write_text('{"profiles": []}\n', encoding="utf-8")
            os.chmod(profiles, 0o600)
            for mode in (0o755, 0o711):
                with self.subTest(mode=oct(mode)):
                    output = root / ("generated-%04o" % mode)
                    output.mkdir(mode=0o700)
                    os.chmod(output, mode)
                    old_argv = sys.argv
                    try:
                        sys.argv = [
                            "generate-xray-profiles.py",
                            "--profiles",
                            str(profiles),
                            "--out",
                            str(output),
                        ]
                        with self.assertRaises(SystemExit):
                            generator.main()
                    finally:
                        sys.argv = old_argv
                    self.assertEqual(output.stat().st_mode & 0o7777, mode)
                    os.chmod(output, 0o700)
                    self.assertEqual(list(output.iterdir()), [])

    def test_publication_failures_preserve_old_bytes_and_cleanup_temp(self):
        failure_patches = (
            ("short_write", mock.patch.object(private_io.os, "write", return_value=0)),
            (
                "file_fsync",
                mock.patch.object(private_io.os, "fsync", side_effect=OSError("fsync")),
            ),
            (
                "replace",
                mock.patch.object(private_io.os, "replace", side_effect=OSError("replace")),
            ),
        )
        for name, patcher in failure_patches:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    target = root / "manifest.json"
                    self.write_prior_manifest(target)
                    original = target.read_bytes()
                    with patcher, self.assertRaises(SystemExit):
                        generator.write_private_json_atomic(target, self.manifest_value(2))
                    self.assertEqual(target.read_bytes(), original)
                    self.assertEqual(
                        [path.name for path in root.iterdir() if path.name.startswith(".routerkit-private-")],
                        [],
                    )

    def test_interruption_before_replace_preserves_old_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "manifest.json"
            self.write_prior_manifest(target)
            original = target.read_bytes()
            with mock.patch.object(
                private_io.os, "replace", side_effect=KeyboardInterrupt
            ):
                with self.assertRaises(KeyboardInterrupt):
                    generator.write_private_json_atomic(target, self.manifest_value(2))
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(
                [path.name for path in root.iterdir() if path.name.startswith(".routerkit-private-")],
                [],
            )

    def test_parent_directory_is_fsynced_after_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "manifest.json"
            with mock.patch.object(
                private_io.os, "fsync", wraps=private_io.os.fsync
            ) as fsync:
                generator.write_private_json_atomic(target, self.manifest_value())
            self.assertGreaterEqual(fsync.call_count, 2)

    def test_parent_fsync_failure_reports_uncertain_durability(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "manifest.json"
            self.write_prior_manifest(target)
            with mock.patch.object(
                private_io.os, "fsync", side_effect=(None, OSError("directory fsync"))
            ):
                with self.assertRaises(SystemExit) as caught:
                    generator.write_private_json_atomic(target, self.manifest_value(2))
            self.assertIn("may already be visible", str(caught.exception))
            self.assertEqual(json.loads(target.read_text()), self.manifest_value(2))

    def test_stale_manifest_is_retired_before_current_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / generator.LOCAL_ENDPOINT_MANIFEST
            self.write_prior_manifest(target)
            generator.retire_stale_local_endpoint_manifest(target)
            self.assertFalse(target.exists())

    def test_failed_current_generation_cannot_leave_stale_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "generated"
            output.mkdir(mode=0o700)
            target = output / generator.LOCAL_ENDPOINT_MANIFEST
            self.write_prior_manifest(target)
            profiles = root / "profiles.json"
            profiles.write_text(
                json.dumps({"profiles": [{"name": "primary", "port": 1082}]}),
                encoding="utf-8",
            )
            old_argv = sys.argv
            try:
                sys.argv = [
                    "generate-xray-profiles.py",
                    "--profiles",
                    str(profiles),
                    "--out",
                    str(output),
                ]
                with self.assertRaises(SystemExit):
                    generator.main()
            finally:
                sys.argv = old_argv
            self.assertFalse(target.exists())

    def test_generator_reexports_shared_parser(self):
        import routerkit_profile_source as core

        self.assertIs(generator.parse_vless, core.parse_vless)


if __name__ == "__main__":
    unittest.main()
