# ADR: bootstrap execution model

- Status: Accepted for the read-only planner, explicitly gated standalone apply, and explicit setup integration
- Date: 2026-07-13
- Tracks: [#13](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/13), planner [#18](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/18), standalone apply [#28](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/28), setup integration [#29](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/29), epic [#5](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/5)

## Decision question

Where does the final one-command bootstrap run before the full RouterKit Python environment and Xray are available?

## Decision

Use a documented hybrid:

1. A trusted host performs orchestration and connects to the router over SSH on a private/local interface.
2. Before Entware Python exists, any router-side bootstrap component must be a minimal, auditable POSIX `sh` entrypoint using only capabilities proven on the target hardware.
3. After manual Entware activation and Python 3 availability are confirmed, control hands off to the repository Python CLI for manifest validation and planning.
4. `routerkit bootstrap` and standalone `--dry-run` remain read-only. `routerkit bootstrap --apply` is a separate write-capable transaction that requires fresh live inventory, literal `/opt`, a trusted fixed-path Entware `opkg`, supported Linux arm64, and explicit confirmation. `--yes` skips only that prompt; `--apply --dry-run` is a no-write preview.
5. The standalone transaction installs only missing fixed prerequisites, acquires only the reviewed manifest artifact, validates a private candidate, preserves a verified rollback binary, replaces atomically, post-validates, rolls back on failure, and publishes a non-secret provenance receipt.
6. Normal `routerkit setup` and `routerkit setup --apply` do not invoke bootstrap. Only `routerkit setup --apply --bootstrap-apply` delegates to the repository-default standalone transaction after strict planning and one visible setup confirmation, then continues through preflight, backup, install, and healthcheck.

This keeps trust decisions, pin review, and operator confirmation on a general-purpose host while avoiding an assumption that Python already exists on a factory or partially prepared router. The future shell stage must remain small and must verify a repository-owned immutable manifest before any replacement is considered.

## Official evidence

- Netcraze documents SSHv2 as the secure way to reach its CLI and requires the SSH server component; access can be limited to local/private interfaces: [SSH remote access to the Netcraze command line](https://support.netcraze.ru/viva/nc-1913/en/22340-ssh-remote-access-to-the-router-command-line.html).
- Netcraze states that Web CLI is incomplete and recommends Telnet/SSH for professional configuration: [Command-line interface](https://support.netcraze.ru/buddy-4/nc-3211/en/18480-command-line-interface--cli-.html). This project chooses SSH, not Telnet.
- Keenetic's official Entware procedure requires an EXT-family USB filesystem, recommends EXT4, installation of the Open Packages component, placement of the architecture-specific installer, and selection/activation through the device UI: [Installing the Entware repository on a USB drive](https://support.keenetic.ru/eaeu/orbiter-pro/kn-2810/ru/20980-installing-the-entware-repository-on-a-usb-drive.html).
- The official KeeneticOS command reference documents `opkg disk`, `opkg chroot`, and `opkg initrc`, but documentation of a command is not proof that the complete Entware activation flow is safe and equivalent on the target Netcraze hardware: [KeeneticOS 4.0 CLI reference](https://docs.help.keenetic.com/cli/4.0/en/cli_manual_kn-1011.pdf).
- Entware describes itself as a package repository for embedded devices and maintains architecture-specific feeds and installers: [official Entware repository](https://github.com/Entware/Entware).
- Xray-core publishes immutable versioned release assets and sidecar digests from the official repository: [XTLS/Xray-core releases](https://github.com/XTLS/Xray-core/releases).

## Alternatives considered

### Router-side Python orchestrator

This is the best environment after Entware Python is installed: the existing RouterKit code is testable, uses structured data, and can provide strict validation. It cannot be the first-stage answer because Python availability is one of the prerequisites bootstrap must discover. Treating it as preinstalled would make the one-command claim circular.

### Host-side orchestrator over SSH/NDM

A host has Python, storage, TLS trust, and a reviewable checkout. SSH is officially supported. A host-only solution still cannot safely infer that USB preparation or Entware activation is complete, and the documented NDM/Web CLI interfaces are not proven to expose every required step consistently on the target model.

### Minimal router-side POSIX shell, then Python handoff

This solves the missing-Python bootstrap paradox and keeps the first stage small. On its own it puts download, trust-store, package-manager, and recovery logic onto the constrained device too early. The shell stage must therefore be limited to validated, hardware-proven actions and must stop on any unsupported condition.

### Documented hybrid

The hybrid combines the host's safer review/orchestration environment with a minimal router-side compatibility stage and the existing Python implementation after Python is available. It creates explicit gates rather than guessing about device state. This is the selected direction.

## Minimum manual prerequisite

Until spare-hardware validation in [#16](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/16) proves an official, model-correct automated route, the operator must manually:

1. format and attach a suitable USB device as EXT4;
2. install the router's official Open Packages/OPKG component;
3. complete the official Entware activation flow and confirm that `/opt` and an Entware shell are available;
4. enable local/private SSH access without exposing router management to the public Internet.

USB formatting remains deliberately outside RouterKit. It is destructive, depends on the host OS and physical disk identity, and cannot be made safe from a repository command without a separate device-selection and confirmation design.

## Entware activation and Python

The current official materials show supported UI and CLI building blocks, but they do not prove a single non-interactive Entware activation sequence for the target Netcraze hardware, firmware, disk layout, and architecture. RouterKit therefore preserves Entware activation as a manual gate. It does not invent an NDM command sequence.

After activation, the read-only planner reports command and Xray state without invoking the package manager. Standalone apply resolves `opkg` only from `/opt/bin/opkg` or `/opt/sbin/opkg`; a symlink is accepted only when its resolved regular executable remains below `/opt`. Arbitrary `PATH` resolution is never authoritative for writes.

The fixed transaction package set is `ca-bundle`, `curl`, `unzip`, `coreutils-sha256sum`, and `python3`. Apply queries each name, requests only missing top-level names in deterministic order with one bounded `opkg install`, and re-queries every requirement. RouterKit never runs `opkg upgrade`, accepts package input, or requests update/removal of unrelated packages; trusted dependencies and maintainer scripts remain within `opkg`'s authority. Package installation is additive: additions may remain after a partially failed `opkg install` or a later Xray stage because automated dependency removal is unsafe.

## Artifact and candidate transaction

Only the selected `linux-arm64` manifest `download_url` and `sha256` are runtime artifact inputs. The repository manifest is the default, while standalone `--manifest` is an explicit operator-controlled trust input; every selected manifest must pass the same repository, release, architecture, URL, and checksum validation. The initial URL must match that validated manifest exactly. Acquisition uses raw HTTPS connections rather than ambient proxy handlers: port 443 only, TLS hostname verification, connected-peer verification, the existing reviewed global-destination policy, fail-closed mixed DNS answers, and independent redirect validation. Redirects are limited to five and only `github.com` or dot-boundary subdomains of `githubusercontent.com`; query-bearing signed redirects are permitted but never printed. Operational bounds are 16 DNS addresses per hop, 5 seconds per DNS hop, 10 seconds per address connection, 180 seconds overall, 8192-byte URL/Location values, and a 128 MiB archive. The response is streamed to an exclusive `0600` file while hashing and is never buffered in full.

The downloaded SHA-256 must equal the manifest's lowercase digest before extraction or execution. Python ZIP handling rejects malformed, encrypted, traversal, absolute, backslash-confused, duplicate-normalized, directory, symlink/special, unsupported-compression, oversized, excessive-ratio, or excessive-entry archives. It writes only the single member that normalizes to root `xray`; it does not extract GeoIP, GeoSite, README, or any other entry. Limits are 128 entries, a 96 MiB candidate, and a 200:1 compression ratio. The private candidate becomes executable only after complete CRC-checked extraction; the first non-empty version-output line must equal the manifest-derived value `Xray 26.3.27` under a sanitized environment and bounded child lifecycle. Later output lines do not participate in version matching.

Private staging is a unique `0700` directory below the RouterKit-owned `/opt/var/tmp/routerkit`, must share the destination filesystem, and is removed with directory-identity and flat-entry checks on success, failure, `SIGINT`, `SIGTERM`, and `SIGHUP`. Ctrl-C at the confirmation prompt remains ordinary cancellation and enters none of this mutable scope. After confirmation, scoped `SIGINT`/`SIGTERM`/`SIGHUP` handlers record the first signal and coordinate bounded process-group shutdown and reaping. Before replacement, a catchable signal proceeds to staging cleanup without binary rollback. From the point replacement may start through post-install hash/version validation and receipt publication, any catchable termination enters a recovery critical section: pending and repeated catchable signals are deferred while the prior binary is restored and validated, or while a clean-install candidate is removed and absence is verified. Only verified recovery plus staging cleanup permits conventional exit `130` for SIGINT, `143` for SIGTERM, or `129` for SIGHUP. An unproven rollback remains the highest-priority error and returns exit `3` with retained-backup guidance; a cleanup failure outranks an otherwise verified signal exit. `SIGKILL`, power loss, kernel failure, and host crash cannot run in-process cleanup and remain explicit residual risks.

## Hardware-validation unknowns

Spare-hardware work in #16 must establish:

- exact Netcraze model/firmware architecture reporting;
- whether `aarch64` and `arm64` cover the intended hardware;
- behavior and rollback of `opkg disk`/`initrc` and UI activation;
- the minimal shell and certificate/trust-store baseline before Entware;
- available storage and atomic replacement semantics on the mounted USB device;
- init-script paths, reboot persistence, and failure recovery;
- whether host SSH sessions survive each transition safely.

No apply implementation should claim support until those points are observed on disposable hardware.

## Backup, replacement, rollback, and provenance

Before replacement, `/opt/sbin/xray` is opened without following symlinks, identity-checked, bounded-hashed, and safely version-probed. A present target must be a regular executable. After checksum and candidate validation, the current binary is copied exclusively to `/opt/var/lib/routerkit/backups/xray-<full-sha256>`; an existing deterministic backup is reused only after metadata and hash verification. Backups remain after success.

The validated candidate is copied to an exclusive `0755` file in the destination directory, fsynced, hash-verified, and installed with same-filesystem `os.replace()`, followed by directory fsync. No service is stopped or restarted. The installed path must hash-identically and return the exact pinned version. Any post-replacement or receipt-publication failure atomically restores and hash/version-validates the prior backup, or removes and verifies absence for a clean install. Provenance is removed only after binary recovery is verified. A rollback that cannot be proven returns a distinct failure and identifies the retained backup path; it is never downgraded to ordinary signal termination. Package additions are outside this binary rollback boundary.

Successful installation atomically publishes `/opt/var/lib/routerkit/bootstrap-state.json` with restrictive permissions, deterministic JSON, release/archive/installed hashes, exact version, backup identity, and fixed packages installed by RouterKit. It contains no source/profile secret, transient URL, response body, environment dump, timestamp, or staging path. A rerun skips package installation, network, backup, and replacement only when all packages are installed and receipt, release, archive hash, current executable hash, and exact version agree. Matching version without matching provenance is insufficient; stale or corrupt state causes the normal verified transaction.

## Impact on `routerkit setup`

`routerkit bootstrap --apply` remains independently usable. Setup adds no manifest, inventory, package, artifact, or target override: the explicit combined mode delegates exactly to the repository-default standalone apply with internal `--yes`, because setup has already passed strict planning and its one visible confirmation. The stage order is source/reuse/wizard → private profiles → generator → private-profile cleanup → strict plan → confirmation → bootstrap → preflight → backup → install → healthcheck. Without both `--apply` and `--bootstrap-apply`, bootstrap is neither built nor run.

The selected setup source environment variable is consumed after profile acquisition and removed from bootstrap and every later child environment; unrelated variables and `PATH` remain available. The setup parent starts bootstrap in its own session, forwards catchable `SIGINT`, `SIGTERM`, and `SIGHUP` to the direct bootstrap process, preserves the first parent signal, waits without a recovery timeout, and always reaps the child. It does not signal the bootstrap process group or reuse setup's private-workspace force-kill lifecycle. Standalone bootstrap remains responsible for child coordination, binary recovery, cleanup, and any rollback claim. Its nonzero result, including exit `3`, `129`, `130`, or `143`, stops all later router stages and is preserved; a child zero after a parent signal becomes `128 + signal`, and a spawn failure returns `127`.

Integrated setup dry-run renders only an abstract bootstrap stage. It reads no source/environment value, prompts for nothing, creates no workspace, launches no subprocess or network operation, and performs no package, staging, candidate, backup, receipt, Xray, or router write. Manual Entware activation remains required, package additions may remain outside the Xray rollback boundary, and no service restart or autostart is performed. Hardware validation remains #16.

## Non-goals

- formatting or selecting USB devices;
- automatic Entware activation;
- package index updates, upgrades, or removals;
- any artifact other than the exact reviewed manifest pin;
- service/autostart changes;
- config installation or profile-source consumption;
- `xkeen -start`, firewall, TPROXY, REDIRECT, Web UI/API, or policy automation;
- router/runtime, Docker, database, server, or production actions.
