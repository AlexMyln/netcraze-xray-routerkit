# netcraze-xray-routerkit

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![CI](https://github.com/AlexMyln/netcraze-xray-routerkit/actions/workflows/ci.yml/badge.svg)](https://github.com/AlexMyln/netcraze-xray-routerkit/actions/workflows/ci.yml)
[![Shell](https://img.shields.io/badge/Shell-POSIX%20sh-4EAA25.svg)](scripts)
[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB.svg)](scripts/generate-xray-profiles.py)

Safe public starter kit for running Xray VLESS/Reality client profiles on Netcraze/Keenetic-style routers with USB storage, Entware/OPKG, local SOCKS listeners, and Web UI connection policies.

Русская версия: [README.ru.md](README.ru.md)

Changelog: [CHANGELOG.md](CHANGELOG.md)

Guided installer docs: [docs/guided-installer.md](docs/guided-installer.md)

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

The v0.2-alpha guided installer foundation is in progress. For the new read-only preflight and local profiles wizard, see [Guided installer foundation](docs/guided-installer.md).

### Unified CLI

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
sh scripts/install-xray-direct.sh generated
```

7. Start Xray directly:

```sh
sh /opt/etc/init.d/S23xray-direct start
```

8. Run the read-only healthcheck:

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

`install` does not automate Web UI policies, does not call `xkeen -start`, does not touch firewall rules, and does not enable autostart by default.

The `--enable-autostart` flag is reserved for a later explicit flow. Autostart remains manual after healthcheck.

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
docs/guided-installer.md           Guided installer foundation
docs/guided-installer.ru.md        Guided installer foundation in Russian
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
- [Guided installer foundation](docs/guided-installer.md)
- [Guided installer foundation — RU](docs/guided-installer.ru.md)
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

- Move toward a guided one-click installer after Entware/OPKG and Xray prerequisites are already in place: generate profiles, install configs, install `S23xray-direct`, run healthchecks, and print exact Netcraze Web UI steps. The guided installer assumes Entware, SSH, and Xray are already available.
- Keep the from-zero path manual for USB storage, Entware/Xray setup, and Netcraze Web UI/device policy decisions; no ready-to-flash USB/router image is promised.
- Extend dry-run install planning with optional masked previews.
- Add optional config rendering previews with masked secrets.
- Add sample Web UI naming checklists.
- Add shellcheck once the target Entware shell compatibility matrix is documented.
- Keep CI secret rules strict as new examples are added.

## License

MIT.
