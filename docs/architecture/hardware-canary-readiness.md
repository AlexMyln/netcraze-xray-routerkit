# Hardware-canary readiness contract

## Scope

This architecture defines the repository/offline gate for the limited Netcraze hardware window. It does not authorize hardware access, management-interface access, a router write, reboot, deployment, or a live adapter.

The versioned source of truth is:

```text
hardware/netcraze-canary-packet.v1.json
```

The pure offline validator is:

```text
scripts/routerkit_hardware_canary.py
```

It has no process/thread primitive, network client, socket, hardware transport, environment-controlled execution path, or default file write. `status` reads no files. `validate`, `render`, and `matrix` read the packet and repository references only.

## Offline readiness

`READY_FOR_HARDWARE_CANARY` requires:

- exact alpha.16 release and commit;
- strict packet and private-manifest schemas;
- exact P0–P13 phase set;
- acyclic dependencies;
- complete estimates and hard timeouts;
- 120-minute ceiling and protected 15-minute cleanup reserve;
- stop conditions routing to cleanup;
- read-only #21 and #15 contract checklists;
- disposable connection, policy, and optional assignment verification/rollback;
- full #16 install/rerun/update/failure/reboot/recovery/invariant matrix;
- private evidence schema and bilingual public templates;
- narrow off-device compatibility-patch branch;
- cleanup and device-return checklist;
- static guards and tests;
- independent offline review with zero unresolved Critical, High, Medium, or Low findings.

The validator's readiness result describes packet/repository contract completeness. The final repository verdict additionally requires the actual test run and external review report for the current change.

Every ready output must include:

```text
hardware_validated=false
live_contract_confirmed=false
```

The package intentionally omits an evidence-directory initializer. The private manifest schema is validated in pure functions, while directory creation remains an explicit operator action governed by exact `0700`/`0600`, no-link, no-cloud-sync, and cleanup rules. This avoids adding a new write-capable helper before the hardware contract exists.

## Not included

Offline readiness does not include:

- device availability;
- credentials or owner access;
- live interface confirmation;
- successful disposable write;
- reboot proof;
- USB recovery proof;
- a live adapter;
- normal setup integration;
- beta or production readiness.

## State transitions

```text
READY_FOR_HARDWARE_CANARY
  -> READ_CONTRACT_CONFIRMED
  -> WRITE_CONTRACT_CONFIRMED
  -> HARDWARE_CANARY_PASS
```

### `READ_CONTRACT_CONFIRMED`

Requires reviewed private evidence proving:

- observed target and scope decision;
- complete #21 discovery schema/join/UI contract;
- complete #15 connection/policy/assignment read schema;
- unambiguous default-policy identity;
- backup/verification/rollback categories;
- no router write during the read contract.

Documentation or synthetic fixtures alone cannot produce this status.

### `WRITE_CONTRACT_CONFIRMED`

Requires `READ_CONTRACT_CONFIRMED` plus:

- explicit disposable-write authorization;
- one disposable connection created, read back, removed, and verified;
- one disposable non-default policy created, read back, removed, and verified;
- optional assignment either skipped or exactly restored;
- default and unrelated state proven unchanged;
- rollback complete and cleanup verified.

A plan or simulator result cannot produce this status.

### `HARDWARE_CANARY_PASS`

Requires `WRITE_CONTRACT_CONFIRMED` plus:

- full released software-path canary;
- idempotent rerun and bounded profile update;
- applicable safe failure/rollback matrix;
- reboot and recovery;
- optional USB recovery explicitly passed or recorded as a scoped limitation accepted by review;
- final invariant audit;
- complete cleanup/device return;
- public evidence reviewed against the private manifest.

## Failure states

- `CHANGES_REQUIRED`: packet/schema/test/review contract is incomplete or contradictory.
- `BLOCKED_BY_OFFLINE_EVIDENCE`: a required repository reference is missing or unreadable.
- `PARTIAL_NEEDS_OFF_DEVICE_PATCH`: hardware evidence supports one narrow patch, but writes remain stopped.
- `FAILED_ROLLBACK_COMPLETE`: a hardware phase failed and verified rollback completed.
- `FAILED_MANUAL_RECOVERY_REQUIRED`: final state or cleanup cannot be proven.
- `STOP_UNSUPPORTED`: observed target or contract is unsupported or ambiguous.

## Cleanup reachability

P13 depends only on successful P0, not on the success of P1–P12. Every global stop condition routes to P13. Forward progress stops when the remaining session equals the 15-minute reserve.

## Normal setup boundary

The packet is not part of `routerkit setup`. Existing fixture-first `devices` and `netcraze-plan` stages remain explicit and cannot apply Netcraze changes. Any future live adapter is a separate reviewed change after contract confirmation and must rerun #16.
