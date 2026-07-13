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

### Read-only bootstrap planner

The first [bootstrap #13](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/13) slice ([#18](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/18)) validates prerequisites and the repository-owned pinned Xray manifest:

```sh
python3 scripts/routerkit.py bootstrap
python3 scripts/routerkit.py bootstrap --json
python3 scripts/routerkit.py bootstrap --dry-run
```

It initially supports only Linux `aarch64`/`arm64`. Default mode and `--dry-run` are both read-only. The planner does not install Entware or packages, download/replace Xray, write under `/opt`, control services/autostart, or touch firewall and Netcraze policies. Offline inventory files are supported for tests and development.

The plan exposes an explicit command-to-Entware-package mapping, including `sha256sum -> coreutils-sha256sum`, with `ca-bundle` retained as a base requirement. The names are scoped to the documented initial Entware arm64/aarch64 environment and need hardware validation. This remains read-only; package installation belongs to a later #13 slice.

The bootstrap design is recorded in the [execution-model ADR](architecture/bootstrap-execution-model.md); the official release/checksum and independent hash evidence are in [pinned Xray verification](xray-artifact-pin.md). Hardware validation remains blocked by [#16](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/16), and the true one-command [epic #5](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/5) is incomplete.

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

## Safe profile-source acquisition and offline parsing

Use `profile-source` to inspect a local payload or safely acquire an HTTPS subscription and create a generator-compatible private `profiles.json`:

```sh
python3 scripts/routerkit.py profile-source
python3 scripts/routerkit.py profile-source --source-env ROUTERKIT_PROFILE_SOURCE
python3 scripts/routerkit.py profile-source --source-file /private/path/payload.txt --list
python3 scripts/routerkit.py profile-source --source-file /private/path/payload.txt --list --json
python3 scripts/routerkit.py profile-source --source-file /private/path/payload.txt --primary-index 1 --fallback-index 2 --yes
```

Input is accepted through hidden interactive input, an environment variable name, or a regular local UTF-8 file. `--source-file` rejects symlinks and non-regular files. On POSIX, the file must have owner-only permissions such as `0400` or `0600`, with no group or other bits; the tool never changes source-file permissions automatically. Permission-bit enforcement is POSIX-only. There is intentionally no command-line raw-value argument. The parser supports a raw VLESS link, newline-separated links, Base64 subscription text, nested JSON string values, and Base64-decoded JSON. Payload size, decoded size, JSON depth, and candidate count are bounded.

Compatibility is deliberately narrow: VLESS with a syntactically valid UUID, endpoint and port; Reality security; TCP transport (`raw` is normalized to `tcp`); a plausible Base64URL-style Reality public key; an empty or even-length hexadecimal short ID up to 16 characters; and either empty flow or `xtls-rprx-vision`. SNI defaults internally to the endpoint host, and spider paths are normalized to start with `/`. Compatible nodes are deterministically deduplicated.

The numbered list is secret-safe and sanitizes untrusted fragments. It does not print the raw payload, link, UUID, host, SNI, public key, short ID, or spider path. Unsupported URI schemes are rejected without echoing the source. Select exactly one primary and zero, one, or two fallbacks. Output profiles are named `primary`, `fallback-1`, and `fallback-2`, using local SOCKS ports `1082`, `1083`, and `1084`. New output is atomically published without clobbering a destination that appears during the write and has mode `0600` on POSIX. Replacement requires explicit `--force`; even then, symlink and non-regular destinations are refused. `--list` and `--dry-run` never write output; `--yes` does not imply `--force`.

An HTTPS source may be a direct subscription or a standard redirect-based shortlink. Only HTTPS port 443 is accepted, without userinfo or fragments. Every hop revalidates the URL and all DNS addresses, requires an entirely global address set, pins TCP to one validated address, verifies TLS for the original hostname, and verifies the peer address. There are at most 5 redirects and 16 DNS addresses per hop; DNS, connection, overall, URL/Location, and body limits are 5 seconds, 10 seconds, 30 seconds, 8192 bytes, and 1 MiB respectively. Compressed responses, JavaScript redirects, and meta refresh are rejected. See the [security ADR](architecture/profile-source-network-security.md).

For this command, `--dry-run` is no-write rather than no-network: an HTTPS source is acquired and parsed before selection, but no output is written. The generator shares the resolver for `subscription_url` and `subscription_url_env`. Automatic default setup integration remains #24, #20 remains open, and the existing `routerkit setup` path is unchanged.

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

## Setup command

`scripts/routerkit.py setup` is the first implementation slice of the one-command setup roadmap. It orchestrates the existing safe stages instead of replacing them:

1. create `profiles.json` with the wizard, or safely reuse an existing file without printing its contents;
2. generate local config fragments;
3. run a strict install plan;
4. after explicit apply permission, run preflight, backup, install, and healthcheck.

Plan-only setup is the default:

```sh
python3 scripts/routerkit.py setup
```

This performs only local profile collection or reuse, generation, and strict planning. No router apply stage is started.

Within unified setup, generator stdout and stderr are suppressed and replaced with generic status messages because they may contain subscription-derived or credential-derived details. Running the generator directly retains its existing diagnostics.

To continue through the hardened apply pipeline, use:

```sh
python3 scripts/routerkit.py setup --apply
```

After the strict plan passes, setup asks `Proceed with router apply stages? [y/N]:`. Supplying `--yes` skips only this confirmation prompt; preflight, backup, install, and healthcheck still run:

```sh
python3 scripts/routerkit.py setup --apply --yes
```

Use dry-run to render the intended flow without running the wizard, generator, plan, apply stages, or confirmation prompt, and without creating local profile/generated files:

```sh
python3 scripts/routerkit.py --dry-run setup
python3 scripts/routerkit.py setup --apply --dry-run
```

This is a milestone toward epic #5, not its final implementation. Bootstrap apply remains tracked in #13, autostart in #14, Netcraze proxy/policy automation in #15, and hardware validation in #16. `setup` does not invoke `bootstrap` in this release; that integration is deferred until the planner/manifest contract and hardware evidence are reviewed. Setup does not download or install Xray, enable autostart, change Netcraze policies or the default policy, automate the Web UI, create firewall/TPROXY/REDIRECT rules, or call `xkeen -start`.

## Install command

`scripts/routerkit.py install` is safe by default. Without `--apply`, it runs the same strict plan mode and does not change files:

```sh
python3 scripts/routerkit.py install --generated generated
```

With an alternate plan target:

```sh
python3 scripts/routerkit.py install --generated generated --target-root /opt
```

`--apply` runs the hardened apply pipeline:

```sh
python3 scripts/routerkit.py install --generated generated --apply
```

Default apply pipeline:

1. strict install plan;
2. router preflight;
3. backup;
4. install generated configs and S23xray-direct;
5. healthcheck.

If a step before install fails, the pipeline stops and does not run later steps. If install fails after backup, the CLI prints a rollback hint that points back to the backup output/path printed by `scripts/backup.sh`. If healthcheck fails after install, the CLI warns that install may have completed and points to logs and the pre-apply backup.

Backups may contain secret-bearing router files. Do not publish backup archives.

The command does not automate Netcraze Web UI policies, does not create firewall rules, does not call `xkeen -start`, and does not enable autostart. The `--enable-autostart` flag is reserved and currently exits before running any install step. Autostart remains a manual action after healthcheck.

Preview the apply pipeline without running it:

```sh
python3 scripts/routerkit.py --dry-run install --generated generated --apply
python3 scripts/routerkit.py install --generated generated --apply --dry-run
```

Advanced/debug skip flags are available, but they are not recommended:

- `--skip-preflight`;
- `--skip-backup`;
- `--skip-healthcheck`.

The default apply flow runs all safety steps. Skipping backup means rollback may be harder.

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
6. Review the apply summary and healthcheck output.
7. Create Netcraze Web UI proxy connections and policies manually.

## Security notes

- Do not store secrets in git.
- `profiles.json` is ignored.
- `generated/` is ignored.
- Do not paste real generated configs into public issues.
- Do not paste real subscription URLs, VLESS links, UUIDs, Reality public keys, short IDs, spiderX values, IP addresses, MAC addresses, or hostnames into public issues.
