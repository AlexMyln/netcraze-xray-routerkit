# Live-install orchestration architecture

## Decision

Issue #41 adds one coordinator to the unified CLI:

```text
routerkit live-install plan|apply|resume|status
```

The coordinator is a state machine, not a second installer. It owns only
stage ordering, the one installation-scope confirmation, state epochs,
explicit handoffs, stop routing, and the `routerkit.live-install.v1` receipt.
Every domain operation remains delegated to its existing RouterKit owner.

## Reused semantic owners

| Stage | Existing owner |
| --- | --- |
| USB/EXT4/Entware and system preflight | `preflight.sh --bootstrap-readiness` plus typed evidence |
| Pinned Xray | `routerkit-bootstrap.py` and the repository artifact manifest |
| Protected source | unified `setup` private workspace and `routerkit-profile-source.py` |
| Generation and endpoint manifest | `generate-xray-profiles.py` |
| Strict plan | `routerkit-plan.py --strict` through unified `setup` |
| Backup/install/healthcheck | `backup.sh`, `install-xray-direct.sh`, `healthcheck.sh` |
| Autostart | `routerkit-autostart.py` |
| Persistent local routing | `routerkit_routing.py` reconcile called by the existing installer |
| Local native routing | `routerkit-netcraze-live.py` |
| External native routing | `routerkit.netcraze.external-transaction.v1` |

The orchestrator never renders Xray configuration, allocates native object
IDs, rebuilds external transaction commands, edits routing overrides, or
creates a new rollback mechanism.

## State epochs and discovery

The initial state epoch is `0`. One validated evidence fingerprint is accepted
for an epoch and reused. A second different evidence document for the same
epoch is contradictory and rejected. The next integer epoch is accepted only
with one of the bounded state-change reasons `component`, `reboot`, `opt`,
`routerkit-xray`, `contradictory`, or `native`. An epoch cannot be skipped.

This makes resume deterministic: a completed stage is not rerun merely because
the process stopped at a later handoff. Reboot completion must use a new
`reboot` epoch, and post-reboot proof covers management, WAN/PPPoE, LAN,
Wi-Fi, USB/EXT4 `/opt`, Entware, Xray, and loopback listeners.

## Receipt boundary

The checked-in receipt schema is
[`routerkit-live-install.v1.schema.json`](../../hardware/routerkit-live-install.v1.schema.json).
The parent directory must be exactly owner-private `0700`; the file is `0600`,
bounded, structurally validated, and atomically replaced only over a valid
prior receipt. Resume recomputes the intent fingerprint from:

- hardware contract;
- transport mode;
- bootstrap-validated pinned artifact identity;
- a one-way selected-device fingerprint;
- selected profile slot;
- explicit device-move and controlled-reboot authorization flags.

After generation, the existing local-endpoint manifest fingerprint is also
locked. Raw selection identity and all profile/config secrets stay outside the
receipt.

## Transport boundary

`local-ndmc` delegates plan and apply to the existing reviewed live adapter.
Native component installation is deliberately not synthesized through ndmc;
missing support produces a typed-operation handoff.

`external` uses protected snapshots and the existing external transaction
protocol. RouterKit verifies pre-state, then the external agent delivers only
the exact packet commands. A distinct fresh running snapshot is mandatory,
including for NOOP. A mutating packet can reach save only after the RouterKit
running verifier authorizes it, followed by distinct saved-state verification.
Transport success is never an acceptance signal. The native external stage has
no Entware SSH dependency.

## DNS and client acceptance

DNS evidence describes availability, not a preferred public provider. An
already verified protected path is reused when it is `any`/unbound or when its
bound-interface set intersects the selected policy's interfaces. A protected
upstream bound only to an ordinary WAN interface absent from a Proxy-only
policy is rejected. `ip global` is not represented as a DNS repair.

When a precise DNS writer is unavailable, orchestration stops with
`PROTECTED_DNS_DELTA_REQUIRED`; it does not improvise a write. Final PASS also
requires the native verifier's selected-device assignment plus bounded
selected-client evidence for real domain resolution and HTTPS. Without a
client-side probe transport it returns
`CLIENT_FUNCTIONAL_VERIFICATION_REQUIRED`.

## Completion level

The implementation is software-complete when focused and full CI pass. That
does not close the hardware gate: after merge, #41 requires one brownfield
NC-3812 rerun of the complete command/resume flow. The clean spare-hardware
matrix remains #16.
