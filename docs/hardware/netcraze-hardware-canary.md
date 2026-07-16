# Netcraze hardware-canary runbook

Repository/offline verdict: `READY_FOR_HARDWARE_CANARY`.

This document is the primary operator sequence for the limited Netcraze/Keenetic hardware window. The supporting #21 and #15 packets remain useful research:

- [read-only device discovery probe](device-discovery-probe.md);
- [policy write-contract packet](netcraze-policy-contract.md);
- [machine-readable canary packet](../../hardware/netcraze-canary-packet.v1.json);
- [printable checklist](netcraze-canary-checklist.md).

## 1. Purpose and non-claims

Release `v0.2.0-alpha.16` contains the fixture-first #21 discovery core and the fixture-first #15 connection/policy/optional-assignment planning core. The live read and write contracts are still unknown.

`READY_FOR_HARDWARE_CANARY` means the repository, phase graph, time budget, evidence model, stop conditions, rollback, cleanup, and #16 matrix are prepared for the device window. It does not mean:

- hardware validated;
- live management interface confirmed;
- disposable write proven;
- reboot/recovery proven;
- a live adapter exists;
- the one-command path is hardware-tested, beta-ready, or production-ready.

No normal `routerkit setup` mode is added by this packet. The validator and consolidated probe are offline development/operator tools only.

## 2. Planned target and mismatch rule

The planned first target is:

- model: Netcraze Hopper 4G+ NC-2312;
- firmware: `5.00.C.12.0-0`;
- architecture: `aarch64`;
- storage: manually formatted EXT4 USB;
- Entware: mounted under `/opt`.

Every value is `expected_unverified`. Record the observed values separately in the private manifest. On any model, firmware, architecture, or storage mismatch:

1. stop forward progress;
2. record only a sanitized mismatch category publicly;
3. preserve the observed values privately;
4. do not broaden supported scope automatically;
5. proceed directly to cleanup/device return.

## 3. Project invariants

- Never call `xkeen -start`.
- Never add TPROXY, REDIRECT, or firewall rules.
- Never change the default/global policy.
- Never overwrite unrelated connections, policies, assignments, or configuration.
- Never select a device implicitly or assign by IP only.
- Never publish credentials, backups, startup configuration, device inventory, MAC addresses, subscription material, UUIDs, Reality values, or private hostnames.
- Xray listeners remain loopback-only at `127.0.0.1:1082`, `:1083`, and `:1084`.
- Expected runtime is `/opt/sbin/xray` with `/opt/etc/init.d/S23xray-direct`.
- `S24xray` remains disabled.
- Every write needs exact observed state, explicit authorization, backup/export, preconditions, readback, rollback, and rollback verification.
- Stop on the first failure.
- No hardware-tested claim is allowed before #16 passes.

## 4. Operator prerequisites

All items are mandatory unless a phase explicitly marks an operation optional:

- physical access or owner-authorized local administration;
- spare, disposable, or fully recoverable device with no production-critical dependency;
- stable power and local LAN access;
- working backup/export and a known manual recovery path;
- manual USB formatting already complete;
- Entware prerequisite state known;
- clean checkout of `v0.2.0-alpha.16` at `c8f697635c93584e85e76a1d734f8fa797a76b51`;
- offline copies of this runbook, packet JSON, schema, and checklist;
- private evidence directory outside the repository;
- a bounded two-hour window;
- authority to rollback, reboot, clean up, and return the device.

Do not request or begin the window if any prerequisite is uncertain.

## 5. Time budget

The default hard ceiling is 120 minutes. Phase timeout or stop conditions override the schedule.

| Window | Phase | Budget |
| --- | --- | ---: |
| 0–5 | P0 operator preflight | 5 min |
| 5–10 | P1 platform inventory | 5 min |
| 10–20 | P2 #21 read contract | 10 min |
| 20–30 | P3 #15 read contract | 10 min |
| 30–35 | P4 compatibility decision | 5 min |
| 35–45 | P5 disposable connection | 10 min |
| 45–55 | P6 disposable policy | 10 min |
| 55–60 | P7 optional assignment | 5 min |
| 60–75 | P8 full alpha.16 software path | 15 min |
| 75–80 | P9 rerun/profile update | 5 min |
| 80–90 | P10 failures and rollback | 10 min |
| 90–100 | P11 reboot/recovery | 10 min |
| 100–105 | P12 invariant audit | 5 min |
| 105–120 | P13 cleanup and device return | 15 min |

Rules:

- protect at least 15 minutes for P13;
- stop new work when the cleanup reserve begins;
- a narrow patch may reenter hardware only with at least 30 minutes remaining;
- no phase may exceed its packet timeout;
- optional P7 is skipped before consuming cleanup reserve.

Canonical phase IDs:

```text
P0_OPERATOR_PREFLIGHT
P1_READ_ONLY_PLATFORM_INVENTORY
P2_READ_ONLY_DEVICE_DISCOVERY_CONTRACT
P3_READ_ONLY_POLICY_CONTRACT
P4_OFF_DEVICE_COMPATIBILITY_DECISION
P5_DISPOSABLE_CONNECTION_CANARY
P6_DISPOSABLE_POLICY_CANARY
P7_OPTIONAL_DISPOSABLE_ASSIGNMENT_CANARY
P8_FULL_ROUTERKIT_INSTALL_CANARY
P9_IDEMPOTENT_RERUN
P10_FAILURE_AND_ROLLBACK
P11_REBOOT_AND_RECOVERY
P12_FINAL_INVARIANT_AUDIT
P13_CLEANUP_AND_DEVICE_RETURN
```

## 6. Offline packet gate

Run on the workstation checkout, not the router:

```sh
python3 scripts/routerkit-hardware-canary.py status
python3 scripts/routerkit-hardware-canary.py validate
python3 scripts/routerkit-hardware-canary.py matrix
```

The expected offline verdict is `READY_FOR_HARDWARE_CANARY`, with:

```text
hardware_validated=false
live_contract_confirmed=false
```

If validation reports anything else, do not start the hardware session.

## 7. P0 — operator preflight

Record timestamps and timezone privately. Confirm:

- [ ] owner authorization and rollback authority;
- [ ] the device is safe to test;
- [ ] backup and recovery are available;
- [ ] no active production dependency;
- [ ] exact release, tag, and commit;
- [ ] private evidence location is outside the repository;
- [ ] directory mode is exactly `0700`, files will be exactly `0600`;
- [ ] no symlink, hardlink, cloud-sync default, or public terminal recording;
- [ ] cleanup reserve is protected.

Stop if any item fails.

## 8. P1 — read-only platform inventory

Capture only the minimum private categories:

- model and firmware;
- architecture and kernel category;
- shell/tool availability;
- USB filesystem and mount state;
- `/opt` and Entware state;
- Xray presence/version category;
- init-script state;
- current RouterKit artifact category;
- existing listener category;
- current default-policy identity category;
- backup/export availability;
- management interface availability;
- authentication-mode category.

Generic host inspection is allowed only when clearly read-only. Any vendor-specific command or management resource remains an official documented candidate and must be reviewed one at a time for the observed firmware. Nothing from the candidate research is placed in an automatic executable branch.

Pass only if the observed target matches the planned scope and the evidence boundary does not expose unrelated sensitive configuration.

## 9. P2 — #21 device-discovery read contract

The minimum private capture must establish:

- DHCP binding schema;
- association schema;
- hotspot/client summary schema;
- ARP or equivalent only as corroboration;
- stable identity and its assignment suitability;
- source precedence and join rules;
- online/offline and stale state;
- policy visibility;
- duplicate-record behavior;
- local management UI correspondence;
- equivalence or difference between available local interfaces;
- authentication and generic error categories.

Keep the smallest raw artifacts needed to understand field names, cardinality, joins, and error shape. Never place them in the repository.

Outcome:

- `pass`: complete, deterministic read contract matches the UI;
- `partial`: safe useful schema captured, but one non-write question remains;
- `fail`: inconsistent, sensitive, ambiguous, or unverifiable;
- `stop`: target mismatch, authorization problem, or unsafe evidence spill.

P2 never authorizes a write.

## 10. P3 — #15 policy read/write contract, read-only portion

Before any write, capture:

- connection inventory and SOCKS5 representation;
- policy inventory and policy-to-connection references;
- device-to-policy references;
- unambiguous default-policy identity;
- object names/IDs and uniqueness behavior;
- ownership/description markers if present;
- revision/state/precondition evidence;
- save/commit behavior category;
- backup/export category;
- verification readback category;
- rollback category;
- UI correspondence.

No write is allowed until:

- the default policy is unambiguous;
- the full scoped inventory is internally consistent;
- stable identifiers and preconditions are sufficient for one disposable object;
- backup/export is complete;
- the exact reverse operation is understood;
- the read surface does not spill unrelated secret material.

## 11. P4 — compatibility decision

Choose exactly one:

```text
GO_WITH_EXISTING_ALPHA16_CONTRACT
OFF_DEVICE_NARROW_PATCH_REQUIRED
STOP_UNSUPPORTED_OR_AMBIGUOUS
```

### GO_WITH_EXISTING_ALPHA16_CONTRACT

The observed contract is narrow, internally consistent, and sufficient for the planned disposable sequence. Continue only with explicit write authorization.

### OFF_DEVICE_NARROW_PATCH_REQUIRED

Stop router writes. Retain private evidence and leave the router unchanged. The only acceptable patch class is defined in [the compatibility-patch template](netcraze-canary-compatibility-patch.md).

Required before hardware reentry:

- one synthetic fixture derived from sanitized semantics;
- focused and full tests;
- static no-live guard;
- independent delta review;
- explicit authorization to resume;
- at least 30 minutes remaining, including the 15-minute cleanup reserve.

Never patch directly on the router and never improvise a production adapter during the session.

### STOP_UNSUPPORTED_OR_AMBIGUOUS

Do not write. Record the limitation and proceed to P13.

## 12. P5 — disposable connection canary

Only after P3 passes, P4 is GO, backup is verified, state is fresh, and the operator explicitly authorizes one disposable write:

1. collision-check a clearly synthetic name;
2. create one disposable SOCKS5 connection referencing one loopback listener;
3. use no authentication material unless the confirmed contract absolutely requires a safe synthetic local-only value;
4. verify exact semantic readback;
5. prove the default policy is unchanged;
6. remove the connection;
7. verify removal;
8. prove unrelated objects are unchanged.

Stop on the first mismatch. Do not proceed to P6 after an uncertain rollback.

## 13. P6 — disposable policy canary

Only after P5 fully passes:

1. create one synthetic, non-default policy;
2. reference only the disposable connection;
3. verify exact policy and reference readback;
4. prove default policy and unrelated state are unchanged;
5. remove the policy and connection;
6. verify removal and saved/running-state consistency under the confirmed contract.

Do not proceed to assignment after any ambiguity.

## 14. P7 — optional disposable assignment

P7 may be skipped without invalidating connection/policy contract proof.

If used:

- choose a disposable/test client explicitly;
- require a trusted MAC or confirmed stable identifier;
- record the exact previous assignment;
- never use IP-only identity;
- never move a production-critical device;
- verify the new relationship;
- restore the exact prior relationship;
- verify restoration, default policy, and unrelated assignments.

## 15. P8 — full RouterKit canary

P8 validates the released alpha.16 software path after the disposable connection/policy contract passes:

- documented prerequisite state;
- clean plan;
- explicit bootstrap/install;
- pinned Xray binary/checksum;
- config generation;
- loopback-only listener verification;
- health checks;
- device discovery;
- offline Netcraze plan;
- generic selected-profile egress result without secret output;
- default-policy and unrelated-state audit.

Keep two claims separate:

```text
alpha.16 full software path validation
hardware-confirmed interface prototype validation
```

Alpha.16 has no live Netcraze apply adapter. A future adapter requires a separate narrow reviewed change and a rerun of #16. Do not call the path one-command hardware-tested merely because the software path passes.

## 16. P9 — idempotent rerun and profile update

Verify:

- no duplicate generated configuration;
- no duplicate connection or policy;
- exact reuse of equivalent objects;
- no implicit assignment;
- unchanged default policy;
- stable loopback listeners;
- no unrelated state change;
- one bounded profile update follows the documented backup/replacement boundary.

## 17. P10 — failure and rollback matrix

Use low-risk injection methods only. Examples are invalid fixture/state for planning, a deliberately unmet precondition before mutation, a test-only staging failure, a bounded healthcheck failure, or a disposable-object verification mismatch. Do not cause destructive failures merely to fill the table.

| Layer | Required proof |
| --- | --- |
| Planning | invalid plan stops before mutation |
| Bootstrap precondition | failure stops before replacement |
| Router preflight | failure stops before backup and every later mutation |
| Backup gate | later mutation is blocked |
| Install staging | backup restoration or clean-install removal is verified |
| Autostart | failed precondition/verification stops and preserves reviewed state |
| Healthcheck | recovery decision is explicit and state is verified |
| Disposable connection | failure removes or proves absence of the object |
| Disposable policy | failure restores connection/policy state |
| Optional assignment | failure restores exact prior relationship |

After every injected failure, audit default policy and unrelated state.

## 18. P11 — reboot and recovery

Reboot only after explicit authorization, successful prior phases, verified backup, stable power, and enough cleanup time.

After reboot verify:

- `/opt` mount and storage health;
- Xray process identity and expected binary;
- loopback-only listeners;
- `S23xray-direct` reviewed autostart behavior;
- `S24xray` disabled;
- no prohibited firewall/routing markers;
- setup state and idempotent rerun.

USB detach/reattach is optional and only allowed when explicitly safe. Otherwise record it as a limitation. Any recovery uncertainty routes to P13 and may require manual recovery.

## 19. P12 — final invariant audit

Compare preflight and final canonical projections:

- default policy unchanged;
- unrelated connections/policies unchanged;
- unrelated assignments unchanged;
- firewall state unchanged;
- no TPROXY/REDIRECT or xkeen markers;
- listeners loopback-only;
- no secret leakage;
- all disposable objects removed;
- temporary files removed;
- unsupported target paths correctly stop;
- private backup retention/disposal decision recorded.

A failed invariant forbids `PASS_FULL_CANARY`.

## 20. P13 — cleanup and device return

P13 is reachable after P0 regardless of the last successful phase. Use the reserved 15 minutes.

- remove every disposable connection and policy;
- restore prior assignment or prove none occurred;
- remove test users and temporary RouterKit artifacts;
- verify default and unrelated state;
- verify no stale lock, PID, or temporary evidence path;
- verify USB and expected services;
- record the final private state hash;
- retain or securely dispose of private backup/evidence according to the explicit decision;
- inform the owner of limitations;
- physically return the device.

If cleanup cannot be proven, use `FAILED_MANUAL_RECOVERY_REQUIRED`, not a generic failure.

## 21. Evidence model

The private manifest follows `routerkit.netcraze.hardware-evidence.v1` and records metadata only. It may refer to a local filename or opaque artifact reference, size, SHA-256, sensitivity, retention, redaction, and cleanup status. It never embeds raw artifact contents.

Raw evidence rules:

- private directory exactly `0700`;
- files exactly `0600`;
- no symlink or hardlink;
- no cloud sync by default;
- no repository placement;
- no secret-bearing terminal recording;
- no public issue attachment;
- checksum before sanitization;
- explicit retention or secure-deletion decision.

No evidence-directory initializer is included. A new write-capable helper would expand the attack and cleanup surface during a limited session; the strict schema, explicit permission checklist, and operator-created private directory are sufficient for this packet.

Public evidence must use the [public template](netcraze-canary-public-evidence-template.md). Redaction is not anonymity.

## 22. Hardware-session verdicts

Use exactly one:

```text
PASS_CONTRACT_CAPTURE_ONLY
PASS_DISPOSABLE_WRITE_CONTRACT
PASS_FULL_CANARY
PARTIAL_NEEDS_OFF_DEVICE_PATCH
FAILED_ROLLBACK_COMPLETE
FAILED_MANUAL_RECOVERY_REQUIRED
STOP_UNSUPPORTED
```

No documentation-only result transitions repository status to `READ_CONTRACT_CONFIRMED`, `WRITE_CONTRACT_CONFIRMED`, or `HARDWARE_CANARY_PASS`. Those transitions require the corresponding private evidence, review, and public-safe summary defined in the readiness architecture.
