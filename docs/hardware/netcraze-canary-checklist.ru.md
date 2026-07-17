# Операторский checklist аппаратного canary Netcraze

Packet: `routerkit.netcraze.hardware-canary.v1`
Release: `v0.2.0-alpha.16`
Commit: `c8f697635c93584e85e76a1d734f8fa797a76b51`
Hard ceiling: 120 min
Cleanup reserve: 15 min

Private session ID: ____________________
Дата/timezone: ____________________
Authorization: [ ]
Start: ______  Cleanup deadline: ______  Hard stop: ______

## P0 — preflight, 0–5

- [ ] Exact release/tag/commit и clean checkout
- [ ] Offline verdict `READY_FOR_HARDWARE_CANARY`
- [ ] `hardware_validated=false`
- [ ] `live_contract_confirmed=false`
- [ ] Spare/disposable/recoverable device
- [ ] Нет production-critical dependency
- [ ] Backup/export и recovery path
- [ ] Stable power/local access
- [ ] Private directory вне repo, exact `0700`
- [ ] Files `0600`, no symlink/hardlink/cloud sync
- [ ] Нет public terminal recording
- [ ] Cleanup reserve защищён

Decision: [ ] GO  [ ] STOP  Time: ______

## P1 — platform, 5–10

- [ ] Observed model/firmware private
- [ ] Architecture/kernel category
- [ ] USB/mount/`/opt`
- [ ] Entware
- [ ] Xray/runtime/init
- [ ] Listeners/RouterKit artifacts
- [ ] Backup/export
- [ ] Management interface category
- [ ] Authentication-mode category
- [ ] Planned target match; иначе STOP

Decision: [ ] GO  [ ] STOP_UNSUPPORTED  Time: ______

## P2 — read contract #21, 10–20

- [ ] DHCP schema
- [ ] Association schema
- [ ] Client/hotspot schema
- [ ] Corroborating source
- [ ] Stable identity
- [ ] Source precedence/joins/duplicates
- [ ] Online/offline/stale
- [ ] Policy visibility
- [ ] UI correspondence
- [ ] Interface equivalence
- [ ] Auth/error categories
- [ ] Нет sensitive spill

Result: [ ] PASS  [ ] PARTIAL  [ ] FAIL/STOP  Time: ______

## P3 — read contract #15, 20–30

- [ ] Connections/SOCKS representation
- [ ] Policies/references
- [ ] Device assignments
- [ ] Default policy unambiguous
- [ ] IDs/names/uniqueness
- [ ] Ownership marker category
- [ ] Revision/state/preconditions
- [ ] Save/commit
- [ ] Backup/export complete
- [ ] Readback
- [ ] Exact rollback
- [ ] UI correspondence

Все пункты до write: [ ]  Time: ______

## P4 — decision, 30–35

Ровно один:

- [ ] `GO_WITH_EXISTING_ALPHA16_CONTRACT`
- [ ] `OFF_DEVICE_NARROW_PATCH_REQUIRED`
- [ ] `STOP_UNSUPPORTED_OR_AMBIGUOUS`

Если patch:

- [ ] Router writes stopped
- [ ] Synthetic fixture
- [ ] Focused/full tests
- [ ] Static guard
- [ ] Independent delta review
- [ ] New authorization
- [ ] Осталось минимум 30 min

Time: ______

## P5 — disposable connection, 35–45

Authorization [ ]  Fresh state [ ]  Backup [ ]

- [ ] Synthetic name collision-check
- [ ] One disposable loopback SOCKS connection
- [ ] Exact readback
- [ ] Default unchanged
- [ ] Removed
- [ ] Removal verified
- [ ] Unrelated unchanged

Result: [ ] PASS  [ ] ROLLBACK_COMPLETE  [ ] MANUAL_RECOVERY  Time: ______

## P6 — disposable policy, 45–55

- [ ] P5 passed и authorization
- [ ] One non-default synthetic policy
- [ ] Только disposable connection
- [ ] Exact readback
- [ ] Default unchanged
- [ ] Policy/connection removed
- [ ] Unrelated unchanged

Result: [ ] PASS  [ ] ROLLBACK_COMPLETE  [ ] MANUAL_RECOVERY  Time: ______

## P7 — optional assignment, 55–60

- [ ] SKIP
- [ ] Explicit expendable client
- [ ] Trusted stable identity, not IP-only
- [ ] Prior assignment recorded
- [ ] Assignment verified
- [ ] Exact prior assignment restored
- [ ] Default/unrelated unchanged

Result: [ ] PASS  [ ] SKIP  [ ] FAIL  Time: ______

## P8 — full alpha.16 path, 60–75

- [ ] Prerequisites
- [ ] Clean plan
- [ ] Explicit bootstrap/install
- [ ] Pinned Xray/checksum
- [ ] Config generation
- [ ] Loopback listeners
- [ ] Health checks
- [ ] Explicit device discovery
- [ ] Offline-only Netcraze plan
- [ ] Safe generic egress result
- [ ] Default/unrelated unchanged
- [ ] Нет hardware-tested claim

Result: [ ] PASS  [ ] PARTIAL  [ ] FAIL  Time: ______

## P9 — rerun/update, 75–80

- [ ] No duplicate config/connection/policy
- [ ] Exact reuse
- [ ] No implicit assignment
- [ ] Stable listeners
- [ ] Default/unrelated unchanged
- [ ] Bounded profile update contract

Result: [ ] PASS  [ ] FAIL  Time: ______

## P10 — failures/rollback, 80–90

- [ ] Planning stops before mutation
- [ ] Bootstrap precondition stops before replacement
- [ ] Router preflight stops before backup/later stages
- [ ] Backup gate blocks writes
- [ ] Install staging restore/remove
- [ ] Autostart failure preserves state
- [ ] Healthcheck recovery verified
- [ ] Connection failure restored
- [ ] Policy failure restored
- [ ] Assignment failure restored/skipped
- [ ] Default/unrelated unchanged

Result: [ ] PASS  [ ] PARTIAL  [ ] FAIL  Time: ______

## P11 — reboot/recovery, 90–100

Authorization [ ]  Backup [ ]  Cleanup reserve [ ]

- [ ] Reboot result
- [ ] `/opt`/storage healthy
- [ ] Xray process/binary
- [ ] Loopback listeners
- [ ] `S23xray-direct`
- [ ] `S24xray` disabled
- [ ] No forbidden markers
- [ ] Idempotent rerun
- [ ] USB recovery: [ ] PASS [ ] SKIP [ ] FAIL
- [ ] Default/unrelated unchanged

Result: [ ] PASS  [ ] PARTIAL  [ ] FAIL  Time: ______

## P12 — invariant audit, 100–105

- [ ] Default projection unchanged
- [ ] Unrelated connections/policies unchanged
- [ ] Unrelated assignments unchanged
- [ ] Firewall/routing unchanged
- [ ] Loopback-only
- [ ] No secret leakage
- [ ] Unsupported routes to stop
- [ ] Disposable objects absent
- [ ] Temporary files absent

Result: [ ] PASS  [ ] FAIL  Time: ______

## P13 — cleanup/return, 105–120

- [ ] Disposable connection absent
- [ ] Disposable policy absent
- [ ] Assignment restored/no assignment
- [ ] No test users
- [ ] Default/unrelated verified
- [ ] No stale lock/PID/temp
- [ ] USB/services healthy
- [ ] Final private hash
- [ ] Retention/disposal applied
- [ ] Owner informed
- [ ] Device returned

Cleanup complete: [ ]  Manual recovery: [ ]  Time: ______

## Final outcome

- [ ] `PASS_CONTRACT_CAPTURE_ONLY`
- [ ] `PASS_DISPOSABLE_WRITE_CONTRACT`
- [ ] `PASS_FULL_CANARY`
- [ ] `PARTIAL_NEEDS_OFF_DEVICE_PATCH`
- [ ] `FAILED_ROLLBACK_COMPLETE`
- [ ] `FAILED_MANUAL_RECOVERY_REQUIRED`
- [ ] `STOP_UNSUPPORTED`

Public evidence sanitized: [ ]
Redaction is not anonymity: [ ]
