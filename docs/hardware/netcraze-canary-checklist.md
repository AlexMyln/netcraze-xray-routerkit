# Netcraze hardware-canary operator checklist

Packet: `routerkit.netcraze.hardware-canary.v1`
Release: `v0.2.0-alpha.16`
Expected commit: `c8f697635c93584e85e76a1d734f8fa797a76b51`
Hard ceiling: 120 min
Protected cleanup reserve: 15 min

Private session ID: ____________________
Date/timezone: ____________________
Operator/owner authorization recorded: [ ]
Start time: ______  Cleanup deadline: ______  Hard stop: ______

## P0 — preflight, 0–5 min

- [ ] Exact release/tag/commit and clean checkout
- [ ] Offline validator: `READY_FOR_HARDWARE_CANARY`
- [ ] `hardware_validated=false`
- [ ] `live_contract_confirmed=false`
- [ ] Device is spare/disposable/recoverable
- [ ] No production-critical dependency
- [ ] Backup/export and recovery path available
- [ ] Stable power and local access
- [ ] Private directory outside repository, exact `0700`
- [ ] Evidence files exact `0600`; no symlink/hardlink/cloud sync
- [ ] No public terminal recording
- [ ] Fifteen-minute cleanup reserve protected

Decision: [ ] GO  [ ] STOP
Timestamp: ______  Notes category: ____________________

## P1 — platform inventory, 5–10 min

- [ ] Observed model recorded privately
- [ ] Observed firmware recorded privately
- [ ] Architecture and kernel category
- [ ] USB filesystem/mount and `/opt`
- [ ] Entware state
- [ ] Xray/runtime/init category
- [ ] Existing listeners and RouterKit artifacts
- [ ] Backup/export availability
- [ ] Management-interface category
- [ ] Authentication-mode category
- [ ] Planned target match; otherwise STOP

Decision: [ ] GO  [ ] STOP_UNSUPPORTED
Timestamp: ______

## P2 — #21 read contract, 10–20 min

- [ ] DHCP binding schema
- [ ] Association schema
- [ ] Client/hotspot summary schema
- [ ] Corroborating table category
- [ ] Stable identity and source precedence
- [ ] Joins and duplicate behavior
- [ ] Online/offline/stale behavior
- [ ] Policy visibility
- [ ] UI correspondence
- [ ] Interface equivalence/difference
- [ ] Auth/error categories
- [ ] No sensitive spill outside private evidence

Result: [ ] PASS  [ ] PARTIAL  [ ] FAIL/STOP
Timestamp: ______

## P3 — #15 read contract, 20–30 min

- [ ] Connection inventory/SOCKS representation
- [ ] Policy inventory and references
- [ ] Device-to-policy references
- [ ] Default policy unambiguous
- [ ] IDs/names/uniqueness
- [ ] Ownership/description marker category
- [ ] Revision/state/preconditions
- [ ] Save/commit category
- [ ] Backup/export complete
- [ ] Verification readback understood
- [ ] Exact rollback category understood
- [ ] UI correspondence

No write until every item passes: [ ]
Timestamp: ______

## P4 — compatibility decision, 30–35 min

Select exactly one:

- [ ] `GO_WITH_EXISTING_ALPHA16_CONTRACT`
- [ ] `OFF_DEVICE_NARROW_PATCH_REQUIRED`
- [ ] `STOP_UNSUPPORTED_OR_AMBIGUOUS`

If patch:

- [ ] Router writes stopped
- [ ] Synthetic fixture added off-device
- [ ] Focused/full tests passed
- [ ] Static guard passed
- [ ] Delta independently reviewed
- [ ] New authorization recorded
- [ ] At least 30 min remain

Decision timestamp: ______

## P5 — disposable connection, 35–45 min

Explicit authorization: [ ]  Fresh state: [ ]  Backup: [ ]

- [ ] Synthetic name collision-checked
- [ ] One disposable loopback SOCKS connection created
- [ ] Exact readback
- [ ] Default policy unchanged
- [ ] Connection removed
- [ ] Removal verified
- [ ] Unrelated state unchanged

Result: [ ] PASS  [ ] FAILED_ROLLBACK_COMPLETE  [ ] MANUAL_RECOVERY
Timestamp: ______

## P6 — disposable policy, 45–55 min

Explicit authorization: [ ]  P5 passed: [ ]

- [ ] One non-default synthetic policy created
- [ ] References only disposable connection
- [ ] Exact readback
- [ ] Default policy unchanged
- [ ] Policy removed
- [ ] Connection removed
- [ ] Removal and unrelated state verified

Result: [ ] PASS  [ ] FAILED_ROLLBACK_COMPLETE  [ ] MANUAL_RECOVERY
Timestamp: ______

## P7 — optional assignment, 55–60 min

- [ ] SKIPPED
- [ ] Explicit expendable client selected
- [ ] Trusted stable identity; not IP-only
- [ ] Prior assignment recorded
- [ ] Assignment verified
- [ ] Exact prior assignment restored
- [ ] Default/unrelated assignments unchanged

Result: [ ] PASS  [ ] SKIP  [ ] FAIL
Timestamp: ______

## P8 — full alpha.16 software path, 60–75 min

- [ ] Prerequisite state verified
- [ ] Clean plan
- [ ] Explicit bootstrap/install boundary
- [ ] Pinned Xray/checksum
- [ ] Config generation
- [ ] Listeners loopback-only
- [ ] Health checks
- [ ] Device discovery remains explicit
- [ ] Netcraze plan remains offline-only
- [ ] Generic egress result recorded safely
- [ ] Default/unrelated state unchanged
- [ ] No live-adapter or one-command hardware-tested claim

Result: [ ] PASS  [ ] PARTIAL  [ ] FAIL
Timestamp: ______

## P9 — rerun/update, 75–80 min

- [ ] No duplicate config
- [ ] No duplicate connection/policy
- [ ] Exact reuse
- [ ] No implicit assignment
- [ ] Stable loopback listeners
- [ ] Default/unrelated state unchanged
- [ ] Bounded profile update follows backup/replacement contract

Result: [ ] PASS  [ ] FAIL
Timestamp: ______

## P10 — safe failure/rollback, 80–90 min

- [ ] Planning failure stops before mutation
- [ ] Bootstrap precondition failure stops before replacement
- [ ] Router preflight failure stops before backup and later stages
- [ ] Backup-gate failure blocks later stages
- [ ] Install staging failure restores/removes candidate
- [ ] Autostart failure preserves reviewed boundary
- [ ] Healthcheck failure has verified recovery decision
- [ ] Disposable connection failure restored
- [ ] Disposable policy failure restored
- [ ] Optional assignment failure restored or skipped
- [ ] Default/unrelated state unchanged after each

Result: [ ] PASS  [ ] PARTIAL  [ ] FAIL
Timestamp: ______

## P11 — reboot/recovery, 90–100 min

Authorization: [ ]  Backup: [ ]  Cleanup reserve intact: [ ]

- [ ] Reboot result
- [ ] `/opt` and storage healthy
- [ ] Xray process/binary expected
- [ ] Loopback listeners
- [ ] `S23xray-direct` expected state
- [ ] `S24xray` disabled
- [ ] No prohibited routing markers
- [ ] Rerun remains idempotent
- [ ] USB detach/reattach: [ ] PASS  [ ] SKIP  [ ] FAIL
- [ ] Default/unrelated state unchanged

Result: [ ] PASS  [ ] PARTIAL  [ ] FAIL
Timestamp: ______

## P12 — final invariant audit, 100–105 min

- [ ] Default policy canonical projection unchanged
- [ ] Unrelated connections/policies unchanged
- [ ] Unrelated assignments unchanged
- [ ] Firewall/routing state unchanged
- [ ] Listeners loopback-only
- [ ] No secret leakage
- [ ] Unsupported target routes to stop
- [ ] All disposable objects absent
- [ ] Temporary files absent

Result: [ ] PASS  [ ] FAIL
Timestamp: ______

## P13 — cleanup and return, 105–120 min

- [ ] Disposable connection absent
- [ ] Disposable policy absent
- [ ] Prior assignment restored or no assignment occurred
- [ ] No test users
- [ ] Default and unrelated state verified
- [ ] No stale lock/PID/temp evidence
- [ ] USB and expected services healthy
- [ ] Private final state hash recorded
- [ ] Backup/evidence retention decision applied
- [ ] Owner informed of limitations
- [ ] Device physically returned

Cleanup complete: [ ]
Manual recovery required: [ ]
Return timestamp: ______

## Final outcome

Select exactly one:

- [ ] `PASS_CONTRACT_CAPTURE_ONLY`
- [ ] `PASS_DISPOSABLE_WRITE_CONTRACT`
- [ ] `PASS_FULL_CANARY`
- [ ] `PARTIAL_NEEDS_OFF_DEVICE_PATCH`
- [ ] `FAILED_ROLLBACK_COMPLETE`
- [ ] `FAILED_MANUAL_RECOVERY_REQUIRED`
- [ ] `STOP_UNSUPPORTED`

Public evidence reviewed and sanitized: [ ]
Redaction is not anonymity acknowledged: [ ]
