# Guided installer foundation

This is the v0.2-alpha foundation for a guided one-click installer. It adds safe local guidance and read-only router preflight checks without automating the Netcraze Web UI or changing router runtime state.

## Prerequisites

Before using this flow, prepare the router manually:

- Entware is installed on USB storage;
- SSH access to the Entware shell works;
- the Xray binary is available at `/opt/sbin/xray`;
- `/opt` and `/opt/etc` are available on the router.

## Unified CLI

`scripts/routerkit.py` is the unified Python entrypoint for the guided installer foundation. It only delegates to existing scripts and returns the delegated process exit code.

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

Use `--dry-run` to preview the command that the wrapper would run:

```sh
python3 scripts/routerkit.py --dry-run plan --generated generated --strict
```

The wrapper does not automate the Netcraze Web UI, create firewall rules, call `xkeen -start`, or make hidden `/opt` changes. The `backup` command delegates to `scripts/backup.sh`; backup archives may contain secrets and must not be published.

## What the wizard does

`scripts/routerkit-wizard.py` helps create a local `profiles.json` file without hand-editing JSON.

It can:

- ask for profile names and local SOCKS ports;
- accept a subscription source as a hidden URL, an environment variable name, or a local file path;
- configure node selection by first matching node, name contains, host contains, or index;
- write the local ignored `profiles.json`;
- optionally run `python3 scripts/generate-xray-profiles.py --profiles profiles.json --out generated`.

The wizard uses only the Python standard library and suppresses generator output when it runs the optional generation step, so subscription details are not printed back to the terminal.

## What the wizard does not do

The wizard does not:

- connect to the router;
- copy files to the router;
- modify `/opt`;
- install or start Xray;
- run Docker, database, server, or production actions;
- automate the Netcraze Web UI;
- create TPROXY, REDIRECT, or firewall automation.

## Read-only preflight

`scripts/preflight.sh` is intended to run on the Entware/Linux router before installation. It checks prerequisites and prints a human-readable report.

It checks:

- Linux OS;
- `/opt`, `/opt/etc`, `/opt/sbin/xray`, and `/opt/etc/xray/configs`;
- basic commands such as `sh`, `curl`, and `tar`;
- optional `jq`;
- known Xray init scripts;
- whether target local SOCKS ports are exposed on `0.0.0.0`;
- firewall markers related to xkeen, TPROXY, and the routerkit ports.

It is read-only: it does not create files, change permissions, start or stop Xray, or call any xkeen start command.

## Install plan / dry-run

`scripts/routerkit-plan.py` previews the install operations for local generated config fragments without changing `/opt`.

```sh
python3 scripts/routerkit-plan.py --generated generated
```

It checks that `03_inbounds.json`, `04_outbounds.json`, and `05_routing.json` are valid JSON, verifies loopback-only inbound listeners, summarizes profiles without printing outbound secrets, and shows the planned copy targets under `/opt/etc/xray/configs`.

The plan keeps `S24xray` disabled and explicitly does not call `xkeen -start`, touch firewall rules, enable autostart automatically, publish/store secrets, or change Netcraze Web UI policies.

For machine-readable output:

```sh
python3 scripts/routerkit-plan.py --generated generated --json
```

## Install command

`scripts/routerkit.py install` is safe by default. Without `--apply`, it runs the same strict plan mode and does not change files:

```sh
python3 scripts/routerkit.py install --generated generated
```

With an alternate plan target:

```sh
python3 scripts/routerkit.py install --generated generated --target-root /opt
```

Only `--apply` delegates to the install shell script:

```sh
python3 scripts/routerkit.py install --generated generated --apply
```

The command does not automate Netcraze Web UI policies, does not create firewall rules, does not call `xkeen -start`, and does not enable autostart by default. The `--enable-autostart` flag is reserved and currently exits before running any install step. Autostart remains a manual action after healthcheck.

## Example flow

1. Run the wizard locally:

```sh
python3 scripts/routerkit.py wizard
```

2. Generate local config fragments:

```sh
python3 scripts/routerkit.py generate --profiles profiles.json --out generated
```

3. Preview the local install plan:

```sh
python3 scripts/routerkit.py install --generated generated
```

4. Copy the generated config fragments to the router using your private transfer method.
5. Run `python3 scripts/routerkit.py install --generated generated --apply` on the router after reviewing the generated files.
6. Run the healthcheck.
7. Create Netcraze Web UI proxy connections and policies manually.

## Security notes

- Do not store secrets in git.
- `profiles.json` is ignored.
- `generated/` is ignored.
- Do not paste real generated configs into public issues.
- Do not paste real subscription URLs, VLESS links, UUIDs, Reality public keys, short IDs, spiderX values, IP addresses, MAC addresses, or hostnames into public issues.
