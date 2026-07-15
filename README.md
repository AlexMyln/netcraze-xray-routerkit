# netcraze-xray-routerkit

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![CI](https://github.com/AlexMyln/netcraze-xray-routerkit/actions/workflows/ci.yml/badge.svg)](https://github.com/AlexMyln/netcraze-xray-routerkit/actions/workflows/ci.yml)
[![Shell](https://img.shields.io/badge/Shell-POSIX%20sh-4EAA25.svg)](scripts)
[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB.svg)](scripts/generate-xray-profiles.py)

Safe public starter kit for running Xray VLESS/Reality client profiles on Netcraze/Keenetic-style routers with USB storage, Entware/OPKG, local SOCKS listeners, and Web UI connection policies.

Русская версия: [README.ru.md](README.ru.md)

Changelog: [CHANGELOG.md](CHANGELOG.md)

Guided installer docs: [docs/guided-installer.md](docs/guided-installer.md)

Bootstrap design: [execution-model ADR](docs/architecture/bootstrap-execution-model.md) · [pinned Xray verification](docs/xray-artifact-pin.md)

## Repository Media

- GitHub social preview asset: [assets/social-preview.png](assets/social-preview.png)

## Architecture

```text
+-----------------------------+
| Entware on USB storage      |
+-------------+---------------+
              |
              v
+-----------------------------+
| Xray direct init script     |
| no xkeen firewall wrapper   |
+-------------+---------------+
              |
              v
+-----------------------------+
| localhost SOCKS listeners   |
| 127.0.0.1:1082 / 1083 / ... |
+-------------+---------------+
              |
              v
+-----------------------------+
| Netcraze proxy connections  |
+-------------+---------------+
              |
              v
+-----------------------------+
| Per-device policies         |
| selected clients only       |
+-----------------------------+
```

## Why This Exists

Router-side proxy setups often drift into hard-to-audit firewall modes, broad default policies, and scattered secret files. This kit keeps the model small:

- generate Xray config fragments from local, ignored profile input;
- run Xray directly from Entware;
- bind SOCKS listeners to `127.0.0.1`;
- switch only selected clients through Web UI policies;
- keep public repository contents secret-free.

## Safety By Default

- Xray listens on loopback only.
- No public SOCKS port is created.
- The direct init script does not call `xkeen -start`.
- No TPROXY, REDIRECT, or transparent firewall mode is installed by this project.
- The default router policy remains untouched.
- Generated configs, local profile files, router backups, and archives are ignored.
- CI includes syntax checks and a repository secret guard.

## Quick Start

The v0.2-alpha guided setup now integrates profile-source acquisition, private generation, strict planning, and explicitly confirmed apply stages. See the [guided installer documentation](docs/guided-installer.md).

### Unified CLI

```sh
python3 scripts/routerkit.py setup
python3 scripts/routerkit.py setup --apply
python3 scripts/routerkit.py setup --apply --bootstrap-apply
python3 scripts/routerkit.py setup --apply --yes
```

Standalone bootstrap is read-only by default and now has a separately gated transactional apply mode:

```sh
python3 scripts/routerkit.py bootstrap
python3 scripts/routerkit.py bootstrap --dry-run
python3 scripts/routerkit.py bootstrap --apply
python3 scripts/routerkit.py bootstrap --apply --yes
python3 scripts/routerkit.py bootstrap --apply --dry-run
```

Default execution and standalone `--dry-run` validate the selected manifest, map only Linux `aarch64`/`arm64`, and remain read-only: no package command, network, staging, or write. `--apply` requires fresh live inventory, literal `/opt`, a fixed `/opt`-scoped `opkg`, and confirmation unless `--yes` is supplied. `--apply --dry-run` is an abstract no-write transaction preview and does not prompt. The repository manifest is the default; standalone `--manifest` is an explicit operator-controlled trust input and any selected manifest must pass the same structural, repository/URL, checksum, and version gates.

Apply queries the fixed package set `ca-bundle`, `curl`, `unzip`, `coreutils-sha256sum`, and `python3`, then requests only missing top-level names in deterministic order. RouterKit fixes the top-level `opkg` verb and package arguments, but trusted package dependencies and maintainer scripts remain inside `opkg`'s authority. Package installation is additive: additions may remain after a partially failed `opkg install` or a later Xray stage because automatic dependency removal is unsafe. The exact manifest-pinned archive is acquired with bounded proxy-free HTTPS, verified by SHA-256, and safely reduced to one validated `xray` candidate. An existing binary gets a hash-addressed verified backup under `/opt/var/lib/routerkit/backups/`; replacement is same-filesystem atomic, post-validated, and automatically rolled back on failure. A restrictive provenance receipt enables a full no-op rerun only when release, archive hash, installed hash, and exact version all match.

Bootstrap apply does not activate Entware, restart or manage services, enable autostart, load configs, call `xkeen -start`, or change Web UI, firewall, proxy, or policy state. Ctrl-C at the confirmation prompt cancels before any package, network, staging, or write action. Inside the mutable transaction, `SIGINT` is coordinated with `SIGTERM`/`SIGHUP`: before replacement it stops forward progress and cleans staging; after replacement it verifies backup restoration or clean-install removal before conventional exit `130`. Repeated catchable signals are deferred through recovery and cleanup, and the first signal determines the eventual signal exit. Unproven recovery returns distinct exit `3` with retained-backup guidance instead of an ordinary signal result. `SIGKILL`, power loss, kernel failure, and host crash remain residual risks. Profile inputs and generated secrets are unrelated and are never consumed. Manual Entware activation is still required, and #16 must complete before this is described as hardware-tested. See the [execution-model ADR](docs/architecture/bootstrap-execution-model.md) and [artifact-pin evidence](docs/xray-artifact-pin.md).

`setup` now uses the completed profile-source stack by default. It accepts a source through hidden input, a named environment variable, or a protected owner-only file; safely resolves HTTPS when needed; parses compatible nodes; and selects one primary plus up to two fallbacks. Setup writes the selected profiles only inside a unique private workspace, suppresses generator output, removes the temporary profiles immediately after generation, and then runs a strict plan. Generated config fragments persist locally and are secret-bearing. Plain `setup` stops there with no bootstrap or router apply. `setup --apply` preserves the existing confirmed preflight → backup → install → healthcheck flow and performs no bootstrap. Only `setup --apply --bootstrap-apply` adds the reviewed standalone bootstrap transaction, after strict planning and the one visible setup confirmation but before preflight; the internal `--yes` prevents a second prompt. Bootstrap failure, cancellation, any catchable signal observed by the setup bootstrap supervisor, or any internal bootstrap-supervision failure starts no later router stage. `setup --apply --enable-autostart` adds the explicit autostart transaction after healthcheck and uses the same transactional child supervisor. Fixed package additions may remain, while Xray replacement and autostart have separate verified rollback boundaries. No reboot proof, service management outside the reviewed init script, Web UI, firewall, or policy action is performed.

While the private workspace exists, catchable `SIGTERM` and `SIGHUP` requests trigger coordinated source/generator process-group shutdown, child reaping, and workspace cleanup before setup exits. `SIGINT` keeps normal interactive cancellation behavior. `SIGKILL`, power loss, kernel failure, and host crashes cannot run in-process cleanup and may leave the owner-only workspace for manual removal.

For setup, `--source-env` accepts only a valid dedicated `ROUTERKIT_*` variable name. The raw value stays out of argv and output, is available only to the profile-source acquisition child, and is consumed there before URL classification, DNS resolver worker creation, parsing, or selection. Generator, strict-plan, integrated bootstrap, preflight, backup, install, and healthcheck subprocesses receive a copy of the normal environment with that one selected variable removed. Standalone `profile-source --source-env` keeps its existing general environment-name compatibility unless its internal consume option is explicitly used by setup.

Non-interactive source selection keeps raw source material out of argv:

```sh
ROUTERKIT_PROFILE_SOURCE='...' \
python3 scripts/routerkit.py setup --source-env ROUTERKIT_PROFILE_SOURCE --primary-index 1 --fallback-index 2
python3 scripts/routerkit.py setup --source-file /protected/path/source.txt --primary-index 1
```

Existing profiles reuse and the legacy wizard are explicit advanced modes:

```sh
python3 scripts/routerkit.py setup --reuse-profiles /protected/path/profiles.json
python3 scripts/routerkit.py setup --legacy-wizard
```

The old `--profiles` and `--force-wizard` spellings remain deprecated aliases for those explicit modes. Setup no longer notices or reuses `./profiles.json` accidentally. Reuse rejects symlinks, non-regular files, non-owner-only POSIX permissions, oversized content, invalid UTF-8, and path/descriptor identity changes; the validated input is copied into the setup workspace and the original is never modified or passed to the generator.

`setup --dry-run`, including `setup --apply --bootstrap-apply --dry-run`, is abstract and secret-free: it reads no source, reuse file, secret input, or environment value; performs no stdin prompt, DNS or HTTPS request, subprocess, private-workspace creation, file write, package/staging/Xray action, or router action. Python module loading and repository-path resolution are outside this secret-input contract. This is intentionally stricter than standalone `profile-source --dry-run`, which may still acquire an HTTPS source but never writes profiles.

This milestone completes the functional setup integration tracked by #29 and parent #13, but is not the final implementation of [epic #5](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/5). Bootstrap remains explicit; autostart is #14, device discovery is #21, Netcraze proxy/policy automation is #15, and hardware validation remains #16. The integrated path is not hardware-tested until #16 is completed.

### Safe profile-source selection

The profile-source command accepts a hidden pasted value, a named environment variable, or a protected local text file. It parses one raw VLESS link, newline subscriptions, Base64 subscription text, nested JSON strings, and Base64-encoded JSON. The same offline parser can now receive a direct HTTPS subscription or an HTTPS shortlink that uses standard HTTP redirects:

```sh
python3 scripts/routerkit.py profile-source
python3 scripts/routerkit.py profile-source --source-env ROUTERKIT_PROFILE_SOURCE
python3 scripts/routerkit.py profile-source --source-file /private/path/payload.txt --list
python3 scripts/routerkit.py profile-source --source-file /private/path/payload.txt --primary-index 1 --fallback-index 2
```

Only VLESS Reality nodes using TCP (including the normalized Xray `raw` alias), a plausible Reality public key, a valid optional hexadecimal short ID, and either no flow or `xtls-rprx-vision` are selectable. Summaries omit links, identifiers, hosts, SNI, Reality keys, short IDs, and spider paths. `profile-source --source-file` rejects symlinks and non-regular files; on POSIX its permissions must be owner-only, such as `0400` or `0600`, and the tool never changes them automatically. The legacy generator field `subscription_file` remains an advanced compatibility/debug path and does not apply the same permission or symlink policy; prefer `profile-source --source-file` for secret local payloads. Unsupported URI schemes are rejected without echoing the source. Selection produces exactly one primary and up to two fallbacks on deterministic ports `1082`, `1083`, and `1084`. The resulting `profiles.json` is atomically published with mode `0600` on POSIX and cannot clobber a file that appears during publication unless `--force` is explicit; even `--force` rejects symlink and non-regular destinations. The file contains secrets and must never be committed or published.

Network acquisition accepts only HTTPS on port 443, with no URL userinfo or fragments. Outer whitespace around one complete HTTPS source is removed consistently for hidden, environment, protected-file, and generator input, so LF/CRLF file endings work; internal whitespace, control characters, multiple lines, and empty values are rejected, while raw/offline payloads are unchanged. Every HTTP `Location` redirect is independently URL-validated and DNS-resolved; every returned address must pass fixed reviewed special-purpose CIDR tables plus standard-library `ipaddress` defense-in-depth checks, the TCP connection is pinned to a validated address, TLS still verifies the original hostname, and the connected peer is checked. IPv4-mapped IPv6, standardized NAT64, Teredo, 6to4, and ORCHID ranges are conservatively rejected. Ordinary cancellation stops retries and redirects while bounded best-effort resource cleanup is attempted. The limits are 5 redirects, 16 DNS addresses per hop, 5 seconds per DNS hop, 10 seconds per address connection, a 30-second operational deadline plus bounded cleanup grace, an 8192-byte URL/redirect value, and a 1 MiB response. The dedicated Python 3.8.18 and primary `3.x` compatibility job exercises the destination/address-policy test class; the full suite runs on the primary CI Python. Compressed responses are rejected. JavaScript is not executed and HTML meta refresh is not interpreted, so those browser-style navigation mechanisms are not followed or supported; a final HTTP 200 body is instead passed to the offline parser. `profile-source --dry-run` may perform this network read and parsing but never writes `profiles.json`. The generator's existing `subscription_url` and `subscription_url_env` fields use the same resolver. See the [network security ADR](docs/architecture/profile-source-network-security.md).

Default profile-source setup integration completed [#24](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/24) and parent [#20](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/20). The standalone bootstrap transaction now also has an explicit setup gate; autostart, device discovery, policies, and hardware validation remain separate work.

The individual commands remain available:

```sh
python3 scripts/routerkit.py wizard
python3 scripts/routerkit.py generate --profiles profiles.json --out generated
python3 scripts/routerkit.py plan --generated generated
```

Router-side checks:

```sh
python3 scripts/routerkit.py preflight
python3 scripts/routerkit.py healthcheck
```

Use `--dry-run` to preview the wrapper command.

1. Install Entware/OPKG on an EXT4 USB storage device.
2. Install the Xray binary on the router.
3. Copy `examples/profiles.example.json` to local ignored `profiles.json`.
4. Put subscription URLs in environment variables or local files, not in git.
5. Generate Xray config fragments:

```sh
python3 scripts/generate-xray-profiles.py \
  --profiles profiles.json \
  --out generated
```

6. Install generated configs and the direct init script on the router:

```sh
python3 scripts/routerkit.py install --generated generated --apply
```

7. Start Xray directly:

```sh
sh /opt/etc/init.d/S23xray-direct start
```

8. Run the read-only healthcheck again after manual start:

```sh
sh scripts/healthcheck.sh
```

9. Create Netcraze/Keenetic proxy connections and connection policies manually in the Web UI.

### Install plan / dry-run

Preview what the guided installer would do without changing `/opt`:

```sh
python3 scripts/routerkit-plan.py --generated generated
```

The plan suppresses secret-bearing outbound fields and does not call `xkeen -start`, touch firewall rules, enable autostart, or change Web UI policies.

### Install command

Plan-only mode:

```sh
python3 scripts/routerkit.py install --generated generated
```

Apply mode:

```sh
python3 scripts/routerkit.py install --generated generated --apply
```

### Hardened apply flow

`install --apply` runs safety steps around the installer:

```sh
python3 scripts/routerkit.py install --generated generated --apply
```

Default apply pipeline:

1. strict install plan;
2. router preflight;
3. backup;
4. install generated configs and S23xray-direct;
5. healthcheck.

Backups may contain secret-bearing router files. Do not publish backup archives.

`install --apply` does not automate Web UI policies, does not call `xkeen -start`, does not touch firewall rules, and does not enable autostart unless the explicit `--enable-autostart` flag is also provided.

Advanced/debug skip flags are available: `--skip-preflight`, `--skip-backup`, and `--skip-healthcheck`. They are not recommended; the default apply flow runs all safety steps. Skipping backup means rollback may be harder.

Preview the apply pipeline without running it:

```sh
python3 scripts/routerkit.py --dry-run install --generated generated --apply
```

To enable autostart after healthcheck through the reviewed transaction:

```sh
python3 scripts/routerkit.py install --generated generated --apply --enable-autostart
```

The autostart stage runs only after healthcheck and keeps the standalone confirmation prompt. It disables `S24xray`, restarts through the reviewed `S23xray-direct` init script when a restart is needed, verifies stable process epoch plus loopback listener ownership, and then enables only `S23xray-direct`. If autostart is already enabled and runtime-verified, it reports a no-op and does not claim restart verification. Disable is explicit and does not stop the running process:

```sh
python3 scripts/routerkit.py autostart --verify
python3 scripts/routerkit.py autostart --enable --apply
python3 scripts/routerkit.py autostart --disable --apply
```

No reboot is performed or proven. After a real router reboot, run read-only `autostart --verify`. Hardware/reboot validation remains #16; device discovery #21, Netcraze policy automation #15, and epic #5 remain open. See [autostart execution model](docs/architecture/autostart-execution-model.md).

### Testing

Local tests:

```sh
python3 -m unittest discover -s tests -v
```

## Example Topology

```text
Xray local listeners:
  127.0.0.1:1082 -> PROFILE-A
  127.0.0.1:1083 -> PROFILE-B
  127.0.0.1:1084 -> PROFILE-C

Web UI proxy connections:
  XRAY-PROFILE-A -> SOCKS5 127.0.0.1:1082
  XRAY-PROFILE-B -> SOCKS5 127.0.0.1:1083
  XRAY-PROFILE-C -> SOCKS5 127.0.0.1:1084

Connection policies:
  CLIENT-PROFILE-A -> only XRAY-PROFILE-A
  CLIENT-PROFILE-B -> only XRAY-PROFILE-B
  CLIENT-PROFILE-C -> only XRAY-PROFILE-C
```

## What This Is Not

- Not a Docker image.
- Not a ready-to-flash router image.
- Not a subscription service.
- Not a transparent proxy/firewall automation layer.
- Not a place to store real router configs, generated Xray configs, or backup archives.

## Secret Handling

Never commit:

- subscription URLs;
- VLESS links;
- UUIDs from real links;
- Reality public keys, short IDs, or spiderX values;
- real `/opt/etc/xray` configs;
- local `profiles.json` files containing URLs;
- router startup-config files;
- Entware/Xray backup directories or archives.

The repository intentionally keeps only a secret-free example profile file. Real values belong in local ignored files, environment variables, or private transfer channels.

## Supported/Tested Baseline

- Netcraze/Keenetic-style router with Entware/OPKG on USB storage.
- Xray installed at `/opt/sbin/xray`.
- Xray config directory `/opt/etc/xray/configs`.
- POSIX `sh` for router scripts.
- Python 3.8+ for local profile generation.
- Local SOCKS ports such as `1082`, `1083`, and `1084`.

## Repository Layout

```text
scripts/routerkit.py                Unified CLI wrapper for routerkit helpers
scripts/generate-xray-profiles.py  Generate 03/04/05 Xray config fragments
scripts/routerkit-wizard.py        Interactive local profiles.json wizard
scripts/routerkit-plan.py          Dry-run install plan without router changes
scripts/preflight.sh               Read-only Entware/router preflight checks
scripts/install-xray-direct.sh     Install generated configs and init script
scripts/healthcheck.sh             Read-only runtime, listener, firewall, IP checks
scripts/backup.sh                  Create local router backup archives; never publish them
templates/S23xray-direct           Direct-run init script for Entware
examples/profiles.example.json     Secret-free profile template
assets/social-preview.png          GitHub social preview image
README.ru.md                       Russian README
docs/install-from-zero.ru.md       Install from zero guide in Russian
docs/guided-installer.md           Guided installer workflow
docs/guided-installer.ru.md        Guided installer workflow in Russian
docs/installer-scope.md            Guided installer scope and prerequisites
docs/installer-scope.ru.md         Guided installer scope and prerequisites in Russian
docs/netcraze-ui.md                Web UI proxy and policy guide
docs/netcraze-ui.ru.md             Web UI proxy and policy guide in Russian
docs/restore.md                    Restore notes
docs/troubleshooting.md            Common problems and checks
docs/troubleshooting.ru.md         Common problems and checks in Russian
docs/friend-instruction.md         End-user switching guide
docs/friend-instruction.ru.md      End-user switching guide in Russian
docs/announcement.ru.md            Russian announcement draft
```

## Docs

- [Русская версия README](README.ru.md)
- [Changelog](CHANGELOG.md)
- [Guided installer](docs/guided-installer.md)
- [Guided installer — RU](docs/guided-installer.ru.md)
- [Install from zero — RU](docs/install-from-zero.ru.md)
- [Installer scope](docs/installer-scope.md)
- [Netcraze/Keenetic Web UI guide](docs/netcraze-ui.md)
- [Netcraze/Keenetic Web UI guide — RU](docs/netcraze-ui.ru.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Troubleshooting — RU](docs/troubleshooting.ru.md)
- [Friend instruction](docs/friend-instruction.md)
- [Friend instruction — RU](docs/friend-instruction.ru.md)
- [Restore notes](docs/restore.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

Use the GitHub issue templates for sanitized bug reports and feature requests.

## Roadmap

- Move toward a guided one-click installer after supported USB storage, official Entware/OPKG activation, and private SSH access are available: generate profiles, optionally prepare the pinned Xray runtime through explicit `--bootstrap-apply`, install configs and `S23xray-direct`, run healthchecks, and print exact Netcraze Web UI steps. A pre-existing Xray binary is not required for the explicit bootstrap path.
- Keep the from-zero path manual for USB preparation, official Entware activation, SSH access, and Netcraze Web UI/device-policy decisions; RouterKit does not provide a ready-to-flash USB/router image, and normal setup modes do not silently install Xray.
- Extend dry-run install planning with optional masked previews.
- Add optional config rendering previews with masked secrets.
- Add sample Web UI naming checklists.
- Add shellcheck once the target Entware shell compatibility matrix is documented.
- Keep CI secret rules strict as new examples are added.

## License

MIT.
