# ADR: bootstrap execution model

- Status: Accepted for the read-only planning slice; apply behavior remains gated
- Date: 2026-07-13
- Tracks: [#13](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/13), [#18](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/18), epic [#5](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/5)

## Decision question

Where does the final one-command bootstrap run before the full RouterKit Python environment and Xray are available?

## Decision

Use a documented hybrid:

1. A trusted host performs orchestration and connects to the router over SSH on a private/local interface.
2. Before Entware Python exists, any router-side bootstrap component must be a minimal, auditable POSIX `sh` entrypoint using only capabilities proven on the target hardware.
3. After Entware and Python 3 are confirmed, control hands off to the repository Python CLI for manifest validation, planning, and later explicitly approved apply stages.
4. In this slice only the Python read-only planner exists. It performs no host-to-router connection and no apply action.

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

After activation, the read-only planner records whether `opkg`, `python3`, `curl`, `unzip`, and checksum tooling are available. A later reviewed apply slice may install pinned prerequisites only after model validation. Until then, absence is reported as a warning, not repaired.

The plan records one deterministic command-to-Entware-package contract: `curl -> curl`, `unzip -> unzip`, `sha256sum -> coreutils-sha256sum`, and `python3 -> python3`, plus the base package `ca-bundle`. These package names are scoped to the documented initial Entware arm64/aarch64 environment and still require hardware validation. Package installation remains a later #13 slice.

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

## Rollback boundary

This slice stops before the first write. Its rollback is therefore “no state to restore.” A later apply design must create and verify a backup of the existing `/opt/sbin/xray`, validate the candidate independently, replace atomically, and retain a recovery path. Entware activation, USB formatting, router component changes, service state, firewall state, and Netcraze policies are outside this slice.

## Impact on `routerkit setup`

`routerkit bootstrap` is a separate read-only command in this release. `routerkit setup` does not invoke it. The next #13 slice may add an explicit planner gate before setup only after the manifest, inventory, execution model, and hardware results are reviewed. It must not silently turn `setup` into a package installer or Xray replacer.

## Non-goals

- formatting or selecting USB devices;
- automatic Entware activation;
- package installation or index updates;
- downloading or replacing Xray;
- service/autostart changes;
- `xkeen -start`, firewall, TPROXY, REDIRECT, Web UI/API, or policy automation;
- router/runtime, Docker, database, server, or production actions.
