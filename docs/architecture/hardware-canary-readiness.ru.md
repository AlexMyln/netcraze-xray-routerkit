# Readiness contract аппаратного canary

## Scope

Документ определяет repository/offline gate перед ограниченным окном Netcraze. Он не разрешает hardware access, management access, write, reboot, deploy или live adapter.

Source of truth:

```text
hardware/netcraze-canary-packet.v1.json
```

Offline validator:

```text
scripts/routerkit_hardware_canary.py
```

В нём нет process/thread, network/socket, hardware transport, environment execution switch или default write. `status` не читает files; остальные modes читают только packet и repository references.

## Offline readiness

`READY_FOR_HARDWARE_CANARY` требует:

- exact alpha.16 release/commit;
- strict packet и evidence schemas;
- exact P0–P13;
- acyclic dependencies;
- estimates/timeouts;
- ceiling 120 min и cleanup reserve 15 min;
- stop routes to cleanup;
- read contracts #21/#15;
- disposable connection/policy/optional assignment verification и rollback;
- full #16 matrix: install/rerun/update/failure/reboot/recovery/invariants;
- private evidence schema и bilingual public templates;
- narrow off-device patch branch;
- cleanup/device return;
- static guards/tests;
- independent offline review с zero unresolved Critical/High/Medium/Low findings.

Validator показывает completeness packet/repository contract. Итоговый repository verdict дополнительно требует реального test run и external review текущего change.

Каждый ready output содержит:

```text
hardware_validated=false
live_contract_confirmed=false
```

Evidence-directory initializer намеренно отсутствует. Private manifest проверяется pure functions, а directory создаёт оператор по exact `0700`/`0600`, no-link, no-cloud-sync и cleanup rules. Это не добавляет новый write-capable helper до подтверждения hardware contract.

## Не входит

- device availability;
- credentials/owner access;
- live interface confirmation;
- successful write;
- reboot/USB proof;
- live adapter;
- normal setup integration;
- beta/production readiness.

## Transitions

```text
READY_FOR_HARDWARE_CANARY
  -> READ_CONTRACT_CONFIRMED
  -> WRITE_CONTRACT_CONFIRMED
  -> HARDWARE_CANARY_PASS
```

### `READ_CONTRACT_CONFIRMED`

Требует reviewed private evidence: observed target, #21 schema/join/UI, #15 read inventory, unambiguous default policy, backup/verification/rollback categories и отсутствие write. Docs/fixtures недостаточно.

### `WRITE_CONTRACT_CONFIRMED`

Требует previous status, explicit authorization, disposable connection create/readback/remove, disposable non-default policy create/readback/remove, optional assignment skip или exact restore, unchanged default/unrelated state и verified cleanup. Planner/simulator недостаточно.

### `HARDWARE_CANARY_PASS`

Требует previous status, full released software path, idempotent rerun/profile update, applicable failure/rollback matrix, reboot/recovery, USB result/accepted limitation, final invariant audit, cleanup/device return и reviewed public evidence.

## Failure states

- `CHANGES_REQUIRED` — packet/schema/test/review incomplete.
- `BLOCKED_BY_OFFLINE_EVIDENCE` — required repo reference missing.
- `PARTIAL_NEEDS_OFF_DEVICE_PATCH` — нужен narrow off-device patch, writes stopped.
- `FAILED_ROLLBACK_COMPLETE` — failure с proven rollback.
- `FAILED_MANUAL_RECOVERY_REQUIRED` — final state/cleanup не доказан.
- `STOP_UNSUPPORTED` — target/contract unsupported или ambiguous.

## Cleanup reachability

P13 зависит только от successful P0. Все stop conditions ведут в P13. Forward progress прекращается при достижении 15-minute reserve.

## Normal setup boundary

Packet не входит в `routerkit setup`. Fixture-first `devices` и `netcraze-plan` остаются explicit и не применяют Netcraze changes. Future live adapter — отдельный reviewed change с повторным #16.
