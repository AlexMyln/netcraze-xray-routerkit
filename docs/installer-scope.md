# Installer scope

The planned guided installer is a one-command core setup helper, not a bare-router installer.

## Prerequisites

Before running the guided installer, the router must already have:

- Entware/OPKG installed on USB storage;
- official Entware activation completed with `/opt` available;
- working SSH access to the Entware shell;
- writable `/opt/etc/xray/configs`;
- Python 3 available to run RouterKit.

## In scope for the guided installer

The guided installer may:

- run preflight checks;
- verify that it runs on Entware/Linux, not macOS/Windows;
- verify `/opt`, `/opt/sbin/xray`, and config directories;
- accept subscription URLs through hidden input or environment variables;
- extract and mask VLESS Reality/TCP nodes;
- generate Xray config fragments;
- with explicit `setup --apply --bootstrap-apply`, install a fixed prerequisite set and install or transactionally replace the manifest-pinned Xray binary;
- install `S23xray-direct`;
- keep `S24xray` disabled;
- run healthcheck;
- with explicit `setup --apply --enable-autostart` or `install --apply --enable-autostart`, enable `S23xray-direct` only after healthcheck and strict runtime verification;
- with explicit `setup --discover-devices`, run read-only fixture-first device discovery and optional no-write selection after strict planning;
- with explicit `setup --plan-netcraze --netcraze-state-file PATH`, run a consistency-validated fixture-first connection/policy/optional-assignment preview whose desired inputs and exact source snapshot are bound into the plan; reject invalid selected-device identities and require exact POSIX `0700` private generation directories; if combined with apply, print that every Netcraze action is excluded;
- provide a separate offline hardware-canary validator, machine-readable phase/evidence contracts, runbook, and checklist for the limited #16 device window; this development/operator workflow is not part of setup;
- print exact Web UI steps for proxy connections and policies.

## Out of scope for the first guided installer

The first version will not:

- format USB drives;
- install Netcraze/Keenetic firmware components;
- install Entware/OPKG from scratch;
- blindly download and install Xray from unverified sources;
- restart services outside the explicit autostart transaction, perform reboot proof, or call `xkeen -start`;
- automate Web UI clicks;
- change default router policies;
- apply the #15 diagnostic plan to a router before the target write contract and #16 canary are complete;
- invoke the hardware-canary packet from normal `routerkit setup` or treat offline readiness as hardware proof;
- actively scan the LAN by default;
- create TPROXY/REDIRECT/firewall rules;
- publish or store real secrets.

## Why

USB preparation, Entware installation, firmware components, and Web UI policy assignment are device-specific and can be destructive if automated blindly.

The installer should fail closed: if prerequisites are missing, it must stop instead of guessing. Package additions made by explicit bootstrap may remain, while Xray replacement has a separate verified backup/rollback boundary. Autostart enable is not a firewall, Web UI, policy, default-policy, device-discovery, or reboot-validation step. Fixture-first device discovery and the #15 offline plan are not policy or assignment writes. Hardware validation remains tracked in #16.
