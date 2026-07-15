# Autostart execution model

This ADR documents the explicit `S23xray-direct` autostart transaction. It is part of #14 and does not complete #16 hardware/reboot validation or epic #5.

## Scope

The transaction manages only `/opt/etc/init.d/S23xray-direct`, `/opt/etc/init.d/S24xray`, and the direct Xray runtime started by the reviewed init script. It does not configure the Netcraze Web UI, proxy connections, policies, default policy, devices, firewall, TPROXY/REDIRECT, reboot, Entware activation, bootstrap package installation, Docker, databases, or servers.

Production CLI status, verify, enable, and disable always inspect real `/proc`. Synthetic proc data is available only to importable Python functions used by tests.

## Verification

Runtime verification is fail-closed. RouterKit reads the PID file without following symlinks, then verifies:

- `/proc/<pid>/stat` start time before and after identity checks;
- executable device/inode identity for `/opt/sbin/xray`;
- exact command line: `/opt/sbin/xray run -confdir /opt/etc/xray/configs`;
- bounded `/proc/<pid>/fd` socket ownership;
- expected listeners on `127.0.0.1:1082`, `127.0.0.1:1083`, and `127.0.0.1:1084`;
- no matching expected port is exposed on a non-loopback address.

If process identity changes during verification, listener tables cannot be read, fd ownership cannot be proven, init directory enumeration is unreadable or oversized, or an executable conflicting Xray init script is found, verification fails.

## Enable Contract

Enable apply supports only literal `/opt` on Linux. It rejects symlinks, non-regular files, hardlinked init scripts, unsafe `S23xray-direct`, unsafe `S24xray`, missing executable Xray, missing/symlinked/non-directory Xray config directories, config directory identity changes during inspection, and executable Xray init conflicts.

If `S23xray-direct` is already enabled, `S24xray` is disabled, the installed template matches, and runtime verification succeeds, enable is a verified no-op:

- `runtime_verified=true`;
- `restart_performed=false`;
- `restart_verified=false`.

Otherwise enable disables `S24xray`, temporarily disables `S23xray-direct`, invokes the reviewed init script through `sh ... restart`, and verifies runtime before enabling `S23xray-direct`. If a verified process was running before restart, the post-restart identity must be a different process epoch. PID reuse is accepted only with a different `/proc/<pid>/stat` start time. An unchanged epoch is failure.

The successful fresh/recovery message is:

```text
Autostart enabled and restart-verified.
```

The verified no-op message is:

```text
Autostart already enabled and runtime-verified; no restart was performed.
```

## Rollback

Before mutation the transaction captures mode state and verified runtime state. On failure it restores the original `S23xray-direct` mode, keeps `S24xray` non-executable, removes stale autostart receipt state, and then proves the runtime outcome:

- if Xray was verified running before, RouterKit makes one bounded attempt to start through reviewed `S23xray-direct` and requires runtime verification;
- if Xray was verified not running before but the transaction started it, RouterKit stops through the reviewed init script and requires that matching runtime verification no longer succeeds.

Rollback start/stop runs in a recovery-critical section. The original catchable signal remains recorded, but it cannot cancel the recovery child; newly delivered catchable signals are recorded and deferred instead of forwarded to the recovery child. Only the first signal determines an ordinary signal exit after recovery and teardown complete. If rollback or recovery cleanup cannot be proven, enable exits `3` and reports safe manual disable guidance. Rollback failures are not downgraded to ordinary signal or generic failure.

## Disable Contract

Disable supports only literal `/opt`. It uses `lstat`/lexists semantics so dangling symlinks and special files are rejected. Once disable apply begins, catchable signals are recorded and deferred until RouterKit disables `S24xray` first, disables `S23xray-direct`, verifies both final modes, removes stale receipt state, and restores signal lifecycle state. It then returns the deferred signal code when one was recorded. Disable does not stop runtime.

## Init Script

`S23xray-direct` fails closed on process evidence. It requires `/proc/<pid>`, readable `exe`, `cmdline`, and `stat`, stable start time, exact executable/cmdline evidence, and `kill -0`. It revalidates PID plus start time plus executable/cmdline before TERM and KILL, waits boundedly after each signal, and fails if the original process epoch survives.

The script publishes the PID through an owner-only temp file inside the private lock directory. It tracks the active direct child PID plus start time for the current invocation. PID publication failure, start verification failure, and catchable signal traps clean that active child through bounded exact-epoch TERM/KILL, remove only the matching active PID file, and never claim success if cleanup cannot be proven. Signal traps return `3` instead of `129`, `130`, or `143` when active-child cleanup cannot be proven.

The lock path must be a real directory, records owner PID and start time, installs catchable signal traps, and releases only locks owned by the current invocation. Current-invocation lock cleanup remains automatic but identity-checked before and after owner-file removal. Stale lock owners are detected, revalidated, and reported, but stale or unclear locks fail closed and require operator inspection. The init script intentionally avoids automatic stale-lock deletion because POSIX shell path operations cannot atomically bind `rm` or `rmdir` to a previously proven inode identity. A stale lock prevents entry into mutable start/stop/restart actions and leaves lock evidence intact. Owned-lock cleanup rejects symlinked or otherwise unsafe lock paths, including dangling symlinks; unproven lock cleanup returns `3` from signal and EXIT cleanup and is not reported as a clean stop.

## Signals And JSON

Python apply owns the direct init child with `start_new_session=True` on POSIX, records the first catchable signal, forwards `SIGINT`, `SIGTERM`, and `SIGHUP` only outside recovery-critical sections, and keeps ownership until the child is terminal and reaped. The parent blocks handled signals while installing handlers and spawning the child; the child restores the prior mask before exec. Cleanup, rollback, and signal handler/mask teardown complete before a final signal exit is returned; rollback failure exit `3` takes precedence over ordinary signal codes. Setup and install supervisors preserve meaningful nonzero autostart child codes and do not print success summaries after autostart failure.

`--json` apply captures init stdout/stderr with a bounded drain and emits exactly one JSON document on stdout. The JSON does not include raw logs, config content, endpoints, command lines, or PIDs. If child output exceeds the bound, the transaction fails safely after the child is reaped.

## Receipt Decision

The previous autostart receipt is not used for idempotency or trust decisions. This milestone removes it from the trust boundary and deletes stale receipt state during enable/disable cleanup.

## Residual Risk

No reboot is performed or proven. After a real reboot, run:

```sh
python3 scripts/routerkit.py autostart --verify
```

Hardware canary, idempotency, reboot persistence, stale-lock operator handling, and rollback matrix validation remain tracked by #16.
