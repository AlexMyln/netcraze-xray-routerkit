# RouterKit live installation runbook

This runbook is the **short production path** for an operator or AI/automation agent installing RouterKit on a supported Netcraze/Keenetic-style router.

Read and obey the repository-root [`AGENTS.md`](../AGENTS.md) first. That file is normative for agent behavior. This runbook turns those rules into an installation sequence.

The objective is to complete the requested installation safely, not to turn one maintenance window into an open-ended audit.

## 1. Operating model

Use one bounded discovery pass for the current state epoch, then act.

A new state epoch begins only after a material change such as:

- router reboot;
- firmware/component change;
- USB or `/opt` change;
- RouterKit/Xray replacement;
- operator-reported drift;
- contradictory live evidence.

Within one epoch, reuse facts that already passed. Do not repeat the same preflight merely to increase confidence.

## 2. Full happy path

For an installation whose goal includes per-device VPN routing, use this order:

1. ordinary router network stable;
2. one bounded preflight;
3. external USB identified;
4. EXT4 + Entware mounted at literal `/opt`;
5. RouterKit bootstrap;
6. protected profile-source acquisition;
7. setup/generation/strict plan/install/healthcheck;
8. autostart enablement;
9. native Proxy client component present;
10. one controlled reboot if authorized/required;
11. after-reboot proof;
12. native Proxy interfaces;
13. native Internet-access policies;
14. only operator-selected clients assigned;
15. final production verification;
16. SSH hardening and backup follow-up.

Do not postpone checking the native **Proxy client** component until after RouterKit is otherwise declared complete when per-device routing is part of the requested goal.

## 3. One bounded preflight

Before destructive or mutable work, establish only what is needed to execute safely:

- exact router model and firmware/build;
- architecture (`aarch64`/`arm64` for the current RouterKit path);
- management path/RMM availability;
- ordinary WAN/LAN/Wi-Fi health;
- external USB identity and size;
- whether the native Proxy client component is already installed when per-device routing is in scope;
- whether a reboot is required for pending component changes.

Do not perform a second inventory, audit, or configuration review unless the state epoch changes.

## 4. USB and Entware

Formatting is destructive. Proceed only when the external target is unambiguous and the operator authorized formatting.

Expected end state:

```text
USB=external intended device
FILESYSTEM=EXT4
MOUNT=/dev/... -> /opt
/opt/etc=present
/opt/sbin=present
opkg=working and /opt-scoped
ARCH=aarch64|arm64
```

If Entware activation or a required native router component requires a reboot and the operator already authorized completing the installation, the reboot is part of the job rather than a reason to stop.

## 5. Bootstrap

Use the repository implementation and manifest. Preserve all real integrity gates:

```sh
python3 scripts/routerkit.py bootstrap
python3 scripts/routerkit.py bootstrap --apply --yes
```

Do not bypass:

- architecture validation;
- literal `/opt` validation;
- trusted `/opt`-scoped `opkg`;
- HTTPS/TLS/destination checks;
- repository-pinned Xray artifact;
- archive SHA-256 verification;
- semantic Xray release verification;
- safe extraction;
- transactional replacement/rollback.

A narrow parser compatibility difference is not permission to disable a gate. If the underlying invariant is proven, make the smallest strict fix, add regression coverage, run targeted tests, and continue the already-authorized installation.

Known live compatibility forms include:

- Entware `Status: install ok installed`;
- Entware `Status: install user installed`;
- the exact pinned Xray semantic release followed by the official Xray banner/build metadata.

## 6. Profile source

Use only protected input paths:

- hidden interactive input;
- owner-only protected file;
- dedicated `ROUTERKIT_*` environment variable.

Never put a raw subscription/VLESS secret in argv, reports, Git, or chat logs.

HTTPS shortlinks are supported inputs. If a shortlink resolves to an HTML landing page rather than raw/Base64 VLESS, do not force the operator to reconstruct a direct VLESS URI. Use the normal protected subscription action/browser flow to obtain the actual subscription source, then feed it back into RouterKit's protected resolver.

Preserve HTTPS/TLS/SSRF controls throughout.

## 7. Setup, install, healthcheck, autostart

Normal integrated path:

```sh
python3 scripts/routerkit.py setup --apply --enable-autostart
```

Use the exact current CLI options required by the checked-out version and source mode. The expected logical stages are:

```text
source
-> selection
-> generation
-> strict plan
-> preflight
-> backup
-> install
-> healthcheck
-> autostart enable
```

Do not insert extra audits between successful stages.

The resulting listeners must remain loopback-only:

```text
127.0.0.1:1082  primary
127.0.0.1:1083  fallback-1, if configured
127.0.0.1:1084  fallback-2, if configured
```

Never expose them on `0.0.0.0`, LAN, or WAN.

## 8. Controlled reboot and after-reboot proof

When the operator authorizes a reboot, use **one** controlled reboot to prove the actual production chain rather than performing repeated restart experiments.

Before reboot retain only the minimal recovery facts needed.

After reboot prove:

```text
RMM/management=ONLINE
PPPoE/default route=UP
DNS=OK
LAN=OK
Wi-Fi=OK
USB=present
/opt=mounted from intended external EXT4 device
Entware=available
Xray=RUNNING
expected 127.0.0.1 listeners=present and owned by Xray
```

If a diagnostic helper fails but independent bounded checks prove these exact invariants, record a tooling defect separately. Do not reinstall a healthy service solely because one verifier has a known implementation limit.

## 9. Per-device routing

RouterKit listeners are not client routing by themselves.

Use the native chain:

```text
Xray loopback SOCKS
-> native Proxy interface
-> native Internet-access policy
-> selected registered client
```

For a three-profile layout, the usual mapping is:

```text
Proxy/profile A -> 127.0.0.1:1082
Proxy/profile B -> 127.0.0.1:1083
Proxy/profile C -> 127.0.0.1:1084
```

Create one native policy for each desired profile. Assign only explicitly selected clients.

Do not use TPROXY, REDIRECT, xkeen, iptables marking, or hand-edited config files when the native Proxy/policy mechanism is available.

Unselected clients should keep direct PPPoE as their normal path unless the operator explicitly asks for another behavior.

If the operator explicitly authorizes adding proxy paths as fallbacks to Default, report that actual state precisely; do not claim that Default remained unchanged.

## 10. Stop conditions

Stop and involve the operator only when:

- a destructive target is ambiguous;
- a required secret or device/profile selection is needed;
- pinned artifact/checksum/architecture/semantic version is genuinely wrong;
- `/opt`, Entware, PPPoE, LAN, or RMM becomes unavailable and another write could worsen recovery;
- hardware behavior contradicts the known live contract and no safe next command is known;
- the next action exceeds the operator's authorization.

Do **not** stop merely because:

- a valid state uses a known equivalent status/banner form;
- CI is still pending after a narrow tested compatibility fix and the operator did not require CI first;
- the operator laptop left the local LAN while RMM still provides the needed transport;
- a secondary verifier false-fails while independent evidence proves service health;
- the next stage is already inside the operator's declared installation goal.

## 11. Final result classification

Separate service outcome from tooling defects.

Example:

```text
INSTALLATION_RESULT=PASS
VPN_SERVICE=PASS
CLIENT_ROUTING=PASS
AFTER_REBOOT=PASS
TOOLING_ISSUES=#...
SSH_HARDENING=FOLLOW_UP
```

Do not label a functioning production VPN installation `PARTIAL` solely because an auxiliary verifier has a known false-negative.

## 12. Proven NC-3812 baseline

Live evidence from 2026-09-09 established the following working path on **Netcraze Hopper SE NC-3812**, NetcrazeOS **5.1.5 / 5.01.C.5.0-0**:

- `aarch64`;
- external EXT4 USB mounted at `/opt`;
- Entware/OPKG;
- pinned Xray 26.3.27 with SHA-256 verification;
- RouterKit setup and healthcheck;
- autostart recovery after a controlled router reboot;
- three loopback SOCKS listeners;
- native Proxy client interfaces and native policies;
- selected-client routing through a RouterKit profile;
- ordinary PPPoE/LAN/Wi-Fi/RMM remaining healthy.

Treat that contract as established evidence for the same model/firmware path unless new live evidence contradicts it.

## 13. After the maintenance window

Do not refactor the project in the middle of a successful production run unless a concrete defect blocks progress.

After the service goal is complete:

1. preserve a sanitized factual installation record;
2. turn repeatable friction into code + regression tests, agent rules, concise docs, or a tracked issue;
3. keep production secrets, MAC inventories, startup configs, and subscription material out of the repository;
4. shorten the next run instead of adding another layer of approval or audit ceremony.
