# Installer scope

The planned guided installer is a one-command core setup helper, not a bare-router installer.

## Prerequisites

Before running the guided installer, the router must already have:

- Entware/OPKG installed on USB storage;
- working SSH access to the Entware shell;
- Xray binary available at `/opt/sbin/xray`;
- writable `/opt/etc/xray/configs`;
- basic tools such as `curl`, `jq`, and `tar`.

## In scope for the guided installer

The guided installer may:

- run preflight checks;
- verify that it runs on Entware/Linux, not macOS/Windows;
- verify `/opt`, `/opt/sbin/xray`, and config directories;
- accept subscription URLs through hidden input or environment variables;
- extract and mask VLESS Reality/TCP nodes;
- generate Xray config fragments;
- install `S23xray-direct`;
- keep `S24xray` disabled;
- start Xray directly without `xkeen -start`;
- run healthcheck;
- optionally enable autostart after explicit confirmation;
- print exact Web UI steps for proxy connections and policies.

## Out of scope for the first guided installer

The first version will not:

- format USB drives;
- install Netcraze/Keenetic firmware components;
- install Entware/OPKG from scratch;
- blindly download and install Xray from unverified sources;
- automate Web UI clicks;
- change default router policies;
- create TPROXY/REDIRECT/firewall rules;
- publish or store real secrets.

## Why

USB preparation, Entware installation, firmware components, and Web UI policy assignment are device-specific and can be destructive if automated blindly.

The installer should fail closed: if prerequisites are missing, it must stop and print a checklist instead of guessing.
