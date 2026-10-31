"""Protected subscription refresh; native interfaces/policies are never mutated.

Only 04_outbounds.json is replaced. A durable single-generation journal makes
interrupted updates recoverable, including after power loss. Entware Vixie cron
invokes this short-lived worker every two hours; no laptop or AI is required.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import fcntl
import getpass
import hashlib
import json
import os
import platform
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from routerkit_autostart import AutostartPaths, verify_runtime
from routerkit_private_io import (
    PrivateFileError, ensure_private_directory, read_owner_only_text_file,
    remove_private_file_if_valid, write_private_bytes_atomic,
)
from routerkit_profile_network import (
    ProfileNetworkError, normalize_https_source_value, resolve_https_source, validate_https_url,
)
from routerkit_profile_source import ProfileSourceError, parse_compatible_nodes, validate_env_name
from routerkit_xray import build_vless_outbound


SCHEMA = "routerkit.subscription.v1"
MAX_BYTES = 2 * 1024 * 1024
OUTBOUND = "04_outbounds.json"
PORTS = (1082, 1083, 1084)
PROBE_URL = "https://www.gstatic.com/generate_204"
CRON = (
    "# RouterKit subscription refresh v1; managed by routerkit subscription\n"
    "SHELL=/bin/sh\n"
    "PATH=/opt/sbin:/opt/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
    "MAILTO=\"\"\n"
    "0 */2 * * * root /opt/bin/python3 /opt/lib/routerkit/subscription/"
    "routerkit-subscription.py run >/dev/null 2>&1\n"
)
BUNDLE = (
    "routerkit-subscription.py", "routerkit_subscription.py", "routerkit_xray.py",
    "routerkit_private_io.py", "routerkit_profile_source.py",
    "routerkit_profile_network.py", "routerkit_autostart.py",
)


class SubscriptionError(Exception):
    """Messages contain no source, node labels, credentials or helper output."""


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return read_owner_only_text_file(path, maximum_bytes=MAX_BYTES,
                                     description="Subscription private file").encode()


def publish(path, data):
    write_private_bytes_atomic(path, data, maximum_bytes=MAX_BYTES,
                               description="Subscription private file",
                               validate_existing_text=lambda text: None)


def remove(path):
    remove_private_file_if_valid(path, maximum_bytes=MAX_BYTES,
                                 description="Subscription private file",
                                 validate_text=lambda text: None)


def state_dir(root):
    return root / "var/lib/routerkit/subscription"


def source_path(root):
    return root / "etc/routerkit/subscription/source.json"


def cron_path(root):
    return root / "etc/cron.d/routerkit-subscription"


def conf_dir(root):
    return root / "etc/xray/configs"


@contextlib.contextmanager
def locked(root):
    directory = state_dir(root)
    ensure_private_directory(directory)
    path = directory / "lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        meta = os.fstat(fd)
        if (not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or
                meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) != 0o600):
            raise SubscriptionError("Subscription lock is unsafe.")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SubscriptionError("Another subscription operation is running.") from None
        yield
    finally:
        os.close(fd)


@contextlib.contextmanager
def interruptible():
    def interrupted(signum, frame):
        raise SubscriptionError("Subscription operation interrupted.")
    previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        for s in previous:
            signal.signal(s, interrupted)
        yield
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)


def require_router():
    if (platform.system() != "Linux" or platform.machine().lower() not in ("aarch64", "arm64")
            or os.geteuid() != 0):
        raise SubscriptionError("Subscription writes require root on the aarch64 target router.")
    # Do not recreate /opt directories on internal storage after USB removal.
    mounts = Path("/proc/mounts").read_text().splitlines()
    if not any(len(row) >= 4 and row[1] == "/opt" and row[2] == "ext4" and
               "rw" in row[3].split(",") for row in (line.split() for line in mounts)):
        raise SubscriptionError("Literal /opt must be mounted read-write from EXT4 storage.")


def run_command(command, timeout=60):
    env = os.environ.copy()
    # The saved subscription source is never put into a child environment.
    env = {key: value for key, value in env.items() if not key.startswith("ROUTERKIT_")}
    env["PATH"] = "/opt/sbin:/opt/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    env.pop("ALL_PROXY", None)
    env.pop("all_proxy", None)
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env=env, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise SubscriptionError("Subscription helper failed or timed out.") from None
    if result.returncode:
        raise SubscriptionError("Subscription helper rejected the operation.")


def load_source(root):
    value = json.loads(read(source_path(root)))
    if (not isinstance(value, dict) or set(value) != {"schema", "url", "bindings"}
            or value["schema"] != SCHEMA or not isinstance(value["bindings"], list)
            or not 1 <= len(value["bindings"]) <= 3):
        raise SubscriptionError("Subscription configuration is invalid.")
    validate_https_url(value["url"])
    names, labels = set(), set()
    for binding in value["bindings"]:
        if (not isinstance(binding, dict) or set(binding) != {"name", "label"}
                or not isinstance(binding["name"], str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", binding["name"])
                or not isinstance(binding["label"], str) or not binding["label"]
                or len(binding["label"]) > 1024
                or binding["name"] in names or binding["label"] in labels):
            raise SubscriptionError("Subscription profile bindings are invalid.")
        names.add(binding["name"])
        labels.add(binding["label"])
    return value


def fetch_nodes(url):
    return parse_compatible_nodes(resolve_https_source(url).payload)


def active_snapshot(root):
    directory = conf_dir(root)
    if directory.is_symlink() or not directory.is_dir():
        raise SubscriptionError("Xray config directory is unsafe.")
    result = {}
    for entry in directory.iterdir():
        if entry.suffix == ".json":
            if len(result) >= 32:
                raise SubscriptionError("Too many Xray config fragments.")
            result[entry.name] = read(entry)
    if not {"03_inbounds.json", OUTBOUND, "05_routing.json"}.issubset(result):
        raise SubscriptionError("RouterKit configuration is incomplete.")
    if sum(map(len, result.values())) > MAX_BYTES:
        raise SubscriptionError("Xray configuration exceeds the supported bound.")
    return result


def profiles_from_snapshot(snapshot):
    inbounds = json.loads(snapshot["03_inbounds.json"])["inbounds"]
    if not isinstance(inbounds, list) or not 1 <= len(inbounds) <= 3:
        raise SubscriptionError("Expected one to three RouterKit SOCKS profiles.")
    profiles = []
    for inbound in inbounds:
        tag, port = inbound.get("tag", ""), inbound.get("port")
        if (inbound.get("listen") != "127.0.0.1" or inbound.get("protocol") != "socks"
                or not re.fullmatch(r"socks-[a-z0-9][a-z0-9_-]{0,63}", tag)
                or type(port) is not int or port not in PORTS):
            raise SubscriptionError("Unsupported or non-loopback RouterKit profile.")
        profiles.append({"name": tag[6:], "port": port})
    if len({p["name"] for p in profiles}) != len(profiles) or len({p["port"] for p in profiles}) != len(profiles):
        raise SubscriptionError("Duplicate RouterKit SOCKS profile.")
    return profiles


def without_server_port(outbound):
    result = copy.deepcopy(outbound)
    result["settings"]["vnext"][0].pop("port", None)
    return result


def make_bindings(snapshot, nodes, selection=None):
    """Adopt existing choices, allowing the reported server-port drift only.

    Subsequent refreshes use exact source labels, never array indexes or fuzzy
    matches. Reordered subscriptions cannot silently move a client to a node.
    """
    profiles = profiles_from_snapshot(snapshot)
    outbounds = json.loads(snapshot[OUTBOUND])["outbounds"]
    if selection is not None:
        # An explicit, protected RouterKit-generated selection is the authority
        # for migrations where more than the server port has changed. Never
        # infer such a migration from source order, fuzzy labels or proximity.
        if (not isinstance(selection, dict) or set(selection) != {"outbounds"}
                or not isinstance(selection["outbounds"], list)):
            raise SubscriptionError("Explicit profile selection is invalid.")
        outbounds = selection["outbounds"]
        expected = {"vless-" + p["name"] for p in profiles} | {"direct", "block"}
        if (any(not isinstance(p, dict) or not isinstance(p.get("tag"), str) for p in outbounds)
                or len(outbounds) != len(expected) or {p["tag"] for p in outbounds} != expected
                or {"tag": "direct", "protocol": "freedom"} not in outbounds
                or {"tag": "block", "protocol": "blackhole"} not in outbounds):
            raise SubscriptionError("Explicit selection must match the installed RouterKit profile set.")
    bindings = []
    for profile in profiles:
        active = [out for out in outbounds if out.get("tag") == "vless-" + profile["name"]]
        if len(active) != 1:
            raise SubscriptionError("An active profile has no unique VLESS outbound.")
        exact = [node for node in nodes if build_vless_outbound(profile, node) == active[0]]
        matches = exact
        if selection is None and not matches:
            matches = [node for node in nodes if without_server_port(
                build_vless_outbound(profile, node)) == without_server_port(active[0])]
        if len(matches) != 1 or not matches[0].name:
            raise SubscriptionError("Cannot uniquely bind an active profile to the subscription.")
        label = matches[0].name
        if len([node for node in nodes if node.name == label]) != 1:
            raise SubscriptionError("Subscription node names must be unique.")
        bindings.append({"name": profile["name"], "label": label})
    return bindings


def updated_outbounds(snapshot, source, nodes):
    profiles = profiles_from_snapshot(snapshot)
    if {p["name"] for p in profiles} != {b["name"] for b in source["bindings"]}:
        raise SubscriptionError("Active profile set changed; reconfigure subscription bindings.")
    updated = json.loads(snapshot[OUTBOUND])
    for binding in source["bindings"]:
        matches = [node for node in nodes if node.name == binding["label"]]
        if len(matches) != 1:
            raise SubscriptionError("A selected subscription node is missing or ambiguous.")
        tag = "vless-" + binding["name"]
        indexes = [i for i, out in enumerate(updated["outbounds"]) if out.get("tag") == tag]
        if len(indexes) != 1:
            raise SubscriptionError("An active VLESS outbound is missing or ambiguous.")
        updated["outbounds"][indexes[0]] = build_vless_outbound(binding, matches[0])
    return updated, [p["port"] for p in profiles]


def xray_test(root, directory):
    run_command([str(root / "sbin/xray"), "run", "-test", "-confdir", str(directory)], 45)


def verify(root, ports, *, traffic=False):
    # The same PID/executable/socket ownership verifier as autostart, including
    # the bounded high-FD implementation. One/two-profile installs are valid.
    if not verify_runtime(AutostartPaths(root), expected_ports=ports).ok:
        raise SubscriptionError("Xray process or loopback listener verification failed.")
    if traffic:
        for port in ports:
            run_command([str(root / "bin/curl"), "--disable", "--noproxy", "",
                         "--proxy", "socks5h://127.0.0.1:%d" % port,
                         "--connect-timeout", "10", "--max-time", "30", "--fail", "--silent",
                         "--output", "/dev/null", PROBE_URL], 35)


def restart(root):
    run_command(["sh", str(root / "etc/init.d/S23xray-direct"), "restart"])


def recover(root):
    journal = state_dir(root) / "pending.json"
    if not journal.exists() and not journal.is_symlink():
        return False
    value = json.loads(read(journal))
    if (set(value) != {"schema", "before", "after_sha256", "ports"} or value["schema"] != SCHEMA
            or not isinstance(value["before"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["after_sha256"])
            or not isinstance(value["ports"], list) or not value["ports"]
            or any(type(p) is not int or p not in PORTS for p in value["ports"])):
        raise SubscriptionError("Subscription recovery journal is invalid.")
    before = value["before"].encode()
    current = read(conf_dir(root) / OUTBOUND)
    if current != before and digest(current) != value["after_sha256"]:
        raise SubscriptionError("Outbound configuration drift blocks automatic rollback.")
    # Includes recovery when publication succeeded but directory fsync failed.
    publish(conf_dir(root) / OUTBOUND, before)
    xray_test(root, conf_dir(root))
    restart(root)
    # Old upstream may already be unavailable (e.g. its port changed). Prove
    # local rollback, without pretending the old upstream works.
    verify(root, value["ports"])
    remove(journal)
    return True


def apply_update(root, snapshot, updated, ports):
    desired = encode(updated)
    if json.loads(snapshot[OUTBOUND]) == updated:
        verify(root, ports, traffic=True)
        return "unchanged"
    verify(root, ports)  # upstream may be broken; local runtime must be adopted safely
    with tempfile.TemporaryDirectory(prefix="stage-", dir=state_dir(root)) as temporary:
        stage = Path(temporary)
        for name, data in snapshot.items():
            publish(stage / name, desired if name == OUTBOUND else data)
        xray_test(root, stage)
    if active_snapshot(root) != snapshot:
        raise SubscriptionError("Active configuration changed during subscription validation.")
    # Write-ahead rollback journal, bounded to one generation on the USB drive.
    journal = state_dir(root) / "pending.json"
    publish(journal, encode({"schema": SCHEMA, "before": snapshot[OUTBOUND].decode(),
                             "after_sha256": digest(desired), "ports": ports}))
    try:
        publish(conf_dir(root) / OUTBOUND, desired)
        xray_test(root, conf_dir(root))
        restart(root)
        verify(root, ports, traffic=True)
        expected = dict(snapshot, **{OUTBOUND: desired})
        if active_snapshot(root) != expected:
            raise SubscriptionError("Configuration changed during subscription apply.")
    except BaseException:
        previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        try:
            for s in previous:
                signal.signal(s, signal.SIG_IGN)
            recover(root)
        except BaseException:
            raise SubscriptionError("Update failed; rollback is unproven. Recovery journal retained.") from None
        finally:
            for s, handler in previous.items():
                signal.signal(s, handler)
        raise SubscriptionError("Update failed; previous local configuration restored.") from None
    # Once the verified new configuration is committed, cleanup/durability
    # errors must not be described as a successful rollback.
    remove(journal)
    return "updated"


def refresh(root):
    status = {"last_attempt": int(time.time()), "result": "failed"}
    status_path = state_dir(root) / "status.json"
    if status_path.exists():
        previous = json.loads(read(status_path))
        if type(previous.get("last_success")) is int:
            status["last_success"] = previous["last_success"]
    try:
        recover(root)
        source = load_source(root)
        snapshot = active_snapshot(root)
        updated, ports = updated_outbounds(snapshot, source, fetch_nodes(source["url"]))
        result = apply_update(root, snapshot, updated, ports)
        status.update(result=result, last_success=int(time.time()))
        return result
    finally:
        publish(status_path, encode(status))


def configure(root, url, selection=None):
    if (state_dir(root) / "pending.json").exists():
        raise SubscriptionError("Recover the pending subscription transaction before reconfiguration.")
    url = validate_https_url(normalize_https_source_value(url)).normalized_url
    snapshot = active_snapshot(root)
    bindings = make_bindings(snapshot, fetch_nodes(url), selection)
    path = source_path(root)
    ensure_private_directory(path.parent)
    publish(path, encode({"schema": SCHEMA, "url": url, "bindings": bindings}))
    return len(bindings)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def verify_cron(root, proc_root=Path("/proc")):
    """Prove that the Entware binary, rather than another cron, is running."""
    expected = (root / "sbin/cron").stat()
    for count, entry in enumerate(proc_root.iterdir()):
        if count >= 8192:
            break
        if not entry.name.isdigit():
            continue
        try:
            actual = (entry / "exe").stat()
        except OSError:
            continue
        if (expected.st_dev, expected.st_ino) == (actual.st_dev, actual.st_ino):
            return True
    return False


def enable(root, scripts):
    # No package downloads, implicit opkg mutations or workstation execution.
    # The prerequisite is the vendor's Entware Vixie cron package.
    for path in (root / "sbin/cron", root / "etc/init.d/S10cron", root / "bin/python3"):
        if not path.is_file() or not os.access(path, os.X_OK):
            raise SubscriptionError("Enable requires Entware cron, S10cron and /opt/bin/python3.")
    path = cron_path(root)
    if (path.exists() or path.is_symlink()) and read(path) != CRON.encode():
        raise SubscriptionError("Existing subscription cron entry is not recognized.")
    refresh(root)  # prove resolution/apply before enabling unattended execution
    bundle = root / "lib/routerkit/subscription"
    ensure_private_directory(bundle)
    for name in BUNDLE:
        data = (scripts / name).read_bytes()
        if scripts / name != bundle / name:
            publish(bundle / name, data)
        if read(bundle / name) != data:
            raise SubscriptionError("Installed subscription worker verification failed.")
    run_command([str(root / "bin/python3"), str(bundle / "routerkit-subscription.py"), "status"])
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise SubscriptionError("Entware cron.d directory is missing or unsafe.")
    # cron.d is shared with other services; require no group/other write access.
    meta = path.parent.stat()
    if meta.st_uid != os.geteuid() or meta.st_mode & 0o022:
        raise SubscriptionError("Entware cron.d directory is unsafe.")
    # atomic publication helper requires a private parent. Stage privately and
    # rename the completed 0600 cron file into the shared cron.d directory.
    temporary = state_dir(root) / "cron-entry"
    publish(temporary, CRON.encode())
    os.replace(temporary, path)
    try:
        sync_directory(path.parent)
        run_command(["sh", str(root / "etc/init.d/S10cron"), "start"])
        if not verify_cron(root):
            raise SubscriptionError("Entware cron process verification failed.")
        if read(path) != CRON.encode():
            raise SubscriptionError("Subscription schedule read-back failed.")
    except BaseException:
        path.unlink()
        sync_directory(path.parent)
        raise


def disable(root):
    path = cron_path(root)
    if path.exists() or path.is_symlink():
        if read(path) != CRON.encode():
            raise SubscriptionError("Existing subscription cron entry is not recognized.")
        path.unlink()
        sync_directory(path.parent)


def inspect(root):
    status = {"configured": source_path(root).exists(), "enabled": False, "interval_hours": 2,
              "pending_recovery": (state_dir(root) / "pending.json").exists()}
    if status["configured"]:
        load_source(root)  # validate privately, without returning source or labels
    if cron_path(root).exists():
        status["enabled"] = read(cron_path(root)) == CRON.encode()
    if status["enabled"]:
        status["scheduler_running"] = verify_cron(root)
    path = state_dir(root) / "status.json"
    if path.exists():
        saved = json.loads(read(path))
        for key in ("last_attempt", "last_success"):
            if type(saved.get(key)) is int:
                status[key] = saved[key]
        if saved.get("result") in ("updated", "unchanged", "failed"):
            status["result"] = saved["result"]
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description="Refresh protected RouterKit subscriptions every two hours.")
    parser.add_argument("mode", choices=("configure", "run", "enable", "disable", "status"))
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--source-file")
    source.add_argument("--source-env")
    parser.add_argument("--selection-file", help="Owner-only RouterKit-generated outbound selection for an explicit migration.")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    root = Path("/opt")
    try:
        if args.mode != "configure" and (args.source_file or args.source_env or args.selection_file):
            raise SubscriptionError("Source input is accepted only by configure.")
        if args.mode == "status":
            print(json.dumps(inspect(root), sort_keys=True))
            return 0
        require_router()
        if args.mode != "run" and not args.yes:
            raise SubscriptionError("Configuration and schedule changes require --yes.")
        with interruptible(), locked(root):
            if args.mode == "configure":
                if args.source_file:
                    url = read(Path(args.source_file)).decode()
                elif args.source_env:
                    validate_env_name(args.source_env)
                    if not args.source_env.startswith("ROUTERKIT_"):
                        raise SubscriptionError("Use a dedicated ROUTERKIT_* source environment variable.")
                    url = os.environ.pop(args.source_env, "")
                else:
                    url = getpass.getpass("HTTPS subscription source (hidden): ")
                selection = json.loads(read(Path(args.selection_file))) if args.selection_file else None
                count = configure(root, url, selection)
                print("SUBSCRIPTION_CONFIGURED profiles=%d" % count)
            elif args.mode == "run":
                print("SUBSCRIPTION_%s" % refresh(root).upper())
            elif args.mode == "enable":
                enable(root, Path(__file__).resolve().parent)
                print("SUBSCRIPTION_ENABLED interval_hours=2")
            else:
                disable(root)
                print("SUBSCRIPTION_DISABLED")
        return 0
    except SubscriptionError as exc:
        print("routerkit-subscription: %s" % exc, file=sys.stderr)
    except (PrivateFileError, ProfileNetworkError, ProfileSourceError, OSError,
            ValueError, KeyError, TypeError, AttributeError, RecursionError):
        print("routerkit-subscription: protected operation failed; inspect source, state and connectivity locally.",
              file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
