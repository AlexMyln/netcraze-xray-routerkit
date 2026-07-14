# Guided installer

The v0.2-alpha guided setup integrates profile-source acquisition, private local generation, strict planning, and explicitly confirmed router apply stages. It does not automate the Netcraze Web UI, firewall, autostart, or device policies.

## Prerequisites

Before using this flow, prepare the router manually:

- Entware is installed on USB storage;
- SSH access to the Entware shell works;
- `/opt/sbin` is available; an existing `/opt/sbin/xray` can be replaced, but standalone bootstrap apply also supports a clean install;
- `/opt` and `/opt/etc` are available on the router.

## Unified CLI

`scripts/routerkit.py` is the unified Python entrypoint. It delegates individual tools and also owns setup workspace creation, secure profile reuse, source-environment sanitation, source/generator child lifecycle, cleanup, confirmation, and apply orchestration. Ordinary delegated commands preserve their child exit codes.

```sh
python3 scripts/routerkit.py wizard
python3 scripts/routerkit.py generate --profiles profiles.json --out generated
python3 scripts/routerkit.py plan --generated generated
```

### Standalone bootstrap transaction

The read-only planner (#18) and separately gated standalone apply (#28) live under [bootstrap #13](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/13):

```sh
python3 scripts/routerkit.py bootstrap
python3 scripts/routerkit.py bootstrap --dry-run
python3 scripts/routerkit.py bootstrap --apply
python3 scripts/routerkit.py bootstrap --apply --yes
python3 scripts/routerkit.py bootstrap --apply --dry-run
```

Only Linux `aarch64`/`arm64` is supported. Default mode and standalone `--dry-run` perform no package command, network, staging, or write. Apply requires literal `/opt`, fresh live inventory, trusted fixed-path Entware `opkg`, and explicit confirmation; `--yes` skips only that prompt. Apply dry-run is a no-write preview and never prompts. Synthetic inventory is accepted only for read-only tests and development and conflicts with apply. The repository manifest is the default; standalone `--manifest` is an explicit operator-controlled trust input subject to the same validation gates.

Apply requests only missing top-level names from the fixed set `ca-bundle`, `curl`, `unzip`, `coreutils-sha256sum`, and `python3`; RouterKit never requests upgrade or removal. Trusted dependencies and maintainer scripts remain controlled by `opkg`, and additions may remain after a partially failed package install or a later failure because automatic dependency removal is unsafe. It then streams the exact manifest URL through proxy-free HTTPS with per-hop destination/TLS validation and hard bounds, verifies SHA-256, safely extracts only one root `xray`, and requires the first non-empty version-output line to equal the exact pin. An existing executable is hashed and copied to a verified deterministic backup before same-filesystem atomic replacement. Post-install hash/version failure restores that backup; failed clean installs remove the new candidate. A restrictive receipt records non-secret provenance and enables an idempotent no-network rerun only when every identity field still matches.

Manual Entware activation remains required. Bootstrap does not restart services, enable autostart, install configs, read profile secrets, call `xkeen -start`, or change firewall/Web UI/policies. Ctrl-C at the apply confirmation prompt performs no transaction action. During mutable apply, `SIGINT`, `SIGTERM`, and `SIGHUP` stop forward progress; after replacement they defer repeated catchable signals until verified rollback or clean-install removal and staging cleanup complete. Verified SIGINT recovery exits `130`; unproven recovery returns distinct exit `3` with retained-backup guidance. `SIGKILL`, power loss, kernel failure, and host crash remain residual risks. Setup invokes bootstrap only with the explicit `--apply --bootstrap-apply` pair. The [execution-model ADR](architecture/bootstrap-execution-model.md) and [pinned Xray verification](xray-artifact-pin.md) contain the full contract. Hardware validation remains [#16](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/16), so the true one-command [epic #5](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/5) is incomplete.

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

Input is accepted through hidden interactive input, an environment variable name, or a regular local UTF-8 file. `profile-source --source-file` uses the hardened reader: it rejects symlinks and non-regular files, and on POSIX the file must have owner-only permissions such as `0400` or `0600`, with no group or other bits. The tool never changes source-file permissions automatically, and permission-bit enforcement is POSIX-only. The legacy generator field `subscription_file` remains an advanced compatibility/debug path and does not provide the same permission and symlink policy; prefer `profile-source --source-file` for a secret local payload. There is intentionally no command-line raw-value argument. The parser supports a raw VLESS link, newline-separated links, Base64 subscription text, nested JSON string values, and Base64-decoded JSON. Payload size, decoded size, JSON depth, and candidate count are bounded.

Compatibility is deliberately narrow: VLESS with a syntactically valid UUID, endpoint and port; Reality security; TCP transport (`raw` is normalized to `tcp`); a plausible Base64URL-style Reality public key; an empty or even-length hexadecimal short ID up to 16 characters; and either empty flow or `xtls-rprx-vision`. SNI defaults internally to the endpoint host, and spider paths are normalized to start with `/`. Compatible nodes are deterministically deduplicated.

The numbered list is secret-safe and sanitizes untrusted fragments. It does not print the raw payload, link, UUID, host, SNI, public key, short ID, or spider path. Unsupported URI schemes are rejected without echoing the source. Select exactly one primary and zero, one, or two fallbacks. Output profiles are named `primary`, `fallback-1`, and `fallback-2`, using local SOCKS ports `1082`, `1083`, and `1084`. New output is atomically published without clobbering a destination that appears during the write and has mode `0600` on POSIX. Replacement requires explicit `--force`; even then, symlink and non-regular destinations are refused. `--list` and `--dry-run` never write output; `--yes` does not imply `--force`.

An HTTPS source may be a direct subscription or a standard redirect-based shortlink. Outer whitespace around the complete HTTPS value is removed, including a protected file's LF/CRLF ending, without modifying path/query content or raw/offline payloads; internal whitespace, control characters, multiple lines, and empty values are rejected. Only HTTPS port 443 is accepted, without userinfo or fragments. Every hop revalidates the URL and all DNS addresses, requires an entirely global set under fixed reviewed special-purpose CIDR tables plus standard-library `ipaddress` defense-in-depth checks, pins TCP to one validated address, verifies TLS for the original hostname, and verifies the peer address. IPv4-mapped IPv6, standardized NAT64, Teredo, 6to4, and ORCHID ranges are rejected. Ordinary cancellation stops retries and redirects while bounded best-effort cleanup is attempted. There are at most 5 redirects and 16 DNS addresses per hop; DNS, connection, operational, URL/Location, and body limits are 5 seconds, 10 seconds, 30 seconds plus bounded cleanup grace, 8192 bytes, and 1 MiB respectively. The dedicated compatibility job runs the destination/address-policy test class on Python 3.8.18 and primary `3.x`; the full multiprocessing, TLS, HTTP, deadline, and cancellation suite runs only on the primary CI Python. Compressed responses are rejected. RouterKit follows only supported HTTP 3xx `Location` redirects; it does not execute JavaScript or interpret HTML meta refresh, so those browser-style navigation mechanisms are not followed or supported. A final HTTP 200 body is passed to the offline parser, which fails generically if it contains no compatible profile payload. See the [security ADR](architecture/profile-source-network-security.md).

For this standalone command, `--dry-run` is no-write rather than no-network: an HTTPS source is acquired and parsed before selection, but no output is written. The generator shares the resolver for `subscription_url` and `subscription_url_env`. Default `routerkit setup` integration now completes #20/#24 and has the stricter no-source/reuse/secret/environment-value read, no-stdin-prompt, no-network, no-subprocess, no-private-workspace, and no-write dry-run contract described below.

After cancellation, profile-source makes no unconditional no-write claim: a narrowly timed interrupt can arrive after atomic publication. No secret is printed; check whether the requested output file exists before retrying. Setup normally removes any setup-owned output during private-workspace cleanup.

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

`scripts/routerkit.py setup` uses the existing profile-source, generator, strict-plan, and apply tools as one stop-on-first-failure pipeline:

1. accept a source through hidden input, a named environment variable, or a protected owner-only file;
2. use the reviewed profile-source resolver for HTTPS when needed;
3. parse compatible nodes and select one primary plus up to two fallbacks;
4. publish selected profiles only inside a unique private setup workspace;
5. generate local config fragments with generator output suppressed;
6. remove the private setup profiles and workspace immediately after generator termination;
7. run a strict install plan;
8. after explicit apply permission, optionally run the standalone bootstrap transaction, then run preflight, backup, install, and healthcheck.

Plan-only setup is the default:

```sh
python3 scripts/routerkit.py setup
```

With no source option, setup reads the source through hidden input and prompts for node selection. No accidental current-directory `profiles.json` reuse occurs. This performs source acquisition/selection, local generation, cleanup, and strict planning; no router apply stage is started.

For non-interactive input, pass only an environment-variable name or protected file path, never the raw source as an ordinary argument:

Setup requires a valid dedicated `ROUTERKIT_*` name for `--source-env`. The profile-source child copies and removes that value before URL classification or DNS resolver worker creation; generator, strict plan, integrated bootstrap, and every later apply subprocess receive the normal environment with only that selected variable removed. The raw source is never copied to argv or output. Standalone `profile-source --source-env` retains compatibility with other valid environment names because it does not use setup's internal consume option.

```sh
ROUTERKIT_PROFILE_SOURCE='...' \
python3 scripts/routerkit.py setup \
  --source-env ROUTERKIT_PROFILE_SOURCE \
  --primary-index 1 \
  --fallback-index 2

python3 scripts/routerkit.py setup \
  --source-file /protected/path/source.txt \
  --primary-index 1
```

Existing private profiles reuse and the old wizard are explicit advanced/compatibility modes:

```sh
python3 scripts/routerkit.py setup \
  --reuse-profiles /protected/path/profiles.json

python3 scripts/routerkit.py setup --legacy-wizard
```

`--profiles PATH` is a deprecated explicit alias for `--reuse-profiles PATH`; it has no default. `--force-wizard` is a deprecated alias for `--legacy-wizard`. Secure reuse rejects symlinks and non-regular files, requires owner-only permissions on POSIX, enforces a bounded UTF-8 read, checks path/descriptor identity and metadata, and copies validated content to mode `0600` inside setup's private `0700` workspace. The original file is never modified, deleted, backed up, printed, or passed to the generator.

Within unified setup, generator stdout and stderr are suppressed and replaced with generic status messages because they may contain subscription-derived or credential-derived details. Running the generator directly retains its existing diagnostics.

While setup owns the private workspace, catchable `SIGTERM` and `SIGHUP` requests cause controlled source/generator process-group shutdown, bounded escalation when needed, child reaping, and workspace cleanup before exit. `SIGINT` remains ordinary interactive cancellation. `SIGKILL`, power loss, kernel failure, and host crashes cannot execute user-space cleanup and may leave the owner-only workspace for manual removal. Generated fragments are intentionally persistent, secret-bearing local artifacts; temporary-workspace cleanup does not remove them.

To continue through the hardened apply pipeline, use:

```sh
python3 scripts/routerkit.py setup --apply
```

After the strict plan passes, setup asks `Proceed with router apply stages? [y/N]:`. Supplying `--yes` skips only this confirmation prompt; preflight, backup, install, and healthcheck still run:

```sh
python3 scripts/routerkit.py setup --apply --yes
```

Explicit runtime preparation is a distinct third mode:

```sh
python3 scripts/routerkit.py setup --apply --bootstrap-apply
```

After generation cleanup and a successful strict plan, setup prints the package/Xray warning and asks one visible `Proceed with bootstrap and router apply stages? [y/N]:` question. Once confirmed, it delegates exactly to the repository-default standalone `routerkit-bootstrap.py --apply --yes`, then continues through preflight, backup, install, and healthcheck. The internal `--yes` prevents a second bootstrap prompt; setup `--yes` suppresses only the one setup prompt. Bootstrap failure, cancellation, or an unproven rollback stops every later router stage and preserves the standalone exit code. Any catchable signal observed by the setup bootstrap supervisor likewise stops every later router stage. Fixed package additions may remain; Xray replacement uses the standalone transaction's separate verified backup/rollback. No service restart or autostart occurs.

Use dry-run to render an abstract secret-free flow with no source, reuse-file, secret-input, or environment-value read; no stdin prompt; and no DNS/HTTPS request, subprocess, private workspace, profile, generated file, write, or router action:

```sh
python3 scripts/routerkit.py --dry-run setup
python3 scripts/routerkit.py setup --apply --dry-run
python3 scripts/routerkit.py setup --apply --bootstrap-apply --dry-run
```

This setup dry-run contract differs from standalone `profile-source --dry-run`: standalone profile-source may still perform an HTTPS network read to validate selection, while setup dry-run performs no secret input or network access. Python module loading and repository-path resolution are outside the secret-input contract.

This integration functionally completes #29 and parent #13 but is not the end of epic #5. Autostart is #14, Netcraze proxy/policy automation #15, device discovery #21, and hardware validation #16. The path is not hardware-tested until #16 is complete. Plain `setup` and `setup --apply` do not bootstrap; only the explicit combined mode downloads or installs pinned Xray. No setup mode activates Entware, enables autostart, discovers devices, changes Netcraze policies or the default policy, automates the Web UI, creates firewall/TPROXY/REDIRECT rules, or calls `xkeen -start`. Generated fragments remain secret-bearing local operational artifacts and must not be published.

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
