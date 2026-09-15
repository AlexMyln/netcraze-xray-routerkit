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

The public model has two independent axes:

- `--runtime-mode local-router|external-evidence` controls where RouterKit
  runtime files and processes are inspected or changed;
- `--transport local-ndmc|external` controls native component, reboot,
  Proxy/Policy, assignment, and DNS delivery.

`--transport external` safely infers `external-evidence`; it never implies a
remote shell and never authorizes local `/opt` mutation.

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

## Runtime execution boundary

`local-router` means the coordinator process is executing on the RouterKit
target and literal `/opt` is that router's external storage. Mutable apply must
select this mode explicitly. Only this mode may launch the existing
`preflight.sh`, bootstrap apply, setup/generation, backup, install, healthcheck,
and autostart subprocesses. Their implementation, ordering, and rollback
boundaries are unchanged.

`external-evidence` is the workstation/RMM mode. Its default owner-only receipt
is `.routerkit-live-install/receipt.json`; receipt and transaction paths under
the workstation's `/opt` are rejected. It never launches a runtime subprocess.

For a proven brownfield runtime, `--adopt-existing-runtime` requires an
explicit owner-only `--endpoint-manifest-file` and fresh strict runtime
evidence. RouterKit validates the existing `routerkit.local-endpoints.v1`
manifest, binds its fingerprint to the evidence/receipt, and requires the
selected slot to be enabled. The fixed NC-3812 contract must prove management,
network, external EXT4 `/opt`, Entware, exact pinned semantic Xray release,
running Xray, manifest-matched loopback listeners, autostart, and prior reboot
recovery. No profile source is accepted or read. Runtime write stages are
recorded `SKIPPED`, evidence-backed checks are `PASS`, and the receipt records
`runtime_disposition=ADOPTED`.

Without adoption, the first external-evidence apply stops before runtime work
with `ROUTERKIT_RUNTIME_EXECUTION_REQUIRED`. A shell-capable transport must run
the existing RouterKit runtime flow on the target; `router_exec`, raw MCP
commands, and browser/Web UI are not substitutes. Resume can accept fresh
runtime evidence plus the target-generated endpoint manifest and records
`EXTERNAL_EXECUTED`. Local execution records `EXECUTED`.

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
- runtime mode and adoption intent;
- bootstrap-validated pinned artifact identity;
- a one-way selected-device fingerprint;
- selected profile slot;
- explicit device-move and controlled-reboot authorization flags.

After generation, the existing local-endpoint manifest fingerprint is also
locked. Raw selection identity and all profile/config secrets stay outside the
receipt.

The receipt separately retains the runtime disposition and a strict evidence
fingerprint, so adopted stages cannot be reported as locally executed.

## Native transport boundary

`local-ndmc` delegates plan and apply to the existing reviewed live adapter.
Native component installation is deliberately not synthesized through ndmc;
missing support produces a typed-operation handoff.

`external` controls only native router configuration. It uses protected
snapshots and the existing external transaction protocol. RouterKit verifies
pre-state, then the external agent delivers only
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

The production brownfield NC-3812 rerun is blocked until this runtime-locus
fix is merged with green CI. That post-merge run must use external-evidence
adoption and complete the unchanged native/DNS/client gates. The clean
spare-hardware matrix remains #16.
