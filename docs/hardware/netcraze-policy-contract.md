# Netcraze policy hardware-contract packet

Current verdict: `SOFTWARE_PLAN_CORE_READY_HARDWARE_WRITE_CONTRACT_PENDING`.

`scripts/probe-netcraze-policy-contract.sh` is inert. Its allowlist is empty; it accepts only help and the pending-status print. Do not add unverified commands to that script.

## Stop conditions

Stop on an unexpected model/firmware, incomplete backup, ambiguous default policy, sensitive unrelated output, missing revision/precondition evidence, inconsistent inventory, authentication uncertainty, name/ID ambiguity, any default-policy delta, verification mismatch, or rollback uncertainty. Do not improvise. Preserve the raw output only in an owner-only local directory and never publish it.

## Phase A — read-only contract confirmation

Timebox the session and record only sanitized metadata. Confirm model and firmware; administrator role; available CLI/HTTP API; structured schemas for Proxy interfaces, policies, known-host assignments, and the default policy; ID/name stability across repeated reads; revision/version evidence; config export/backup; and Web UI correspondence. Determine whether a scoped inventory response contains credentials or unrelated configuration.

Candidate evidence to test manually only after authorization comes from the official model guide: read-only NDM status, the documented RCI mapping, policy status, interface status, and configuration export. Exact commands/resources must be selected for the observed target firmware during the session, not copied blindly from another model.

## Phase B — explicitly authorized disposable canary

Use a disposable connection name and loopback endpoint, then verify and remove it. Repeat for one disposable policy. Only if Phase A proved the device relationship and an expendable test device is selected, assign it, verify it, and restore the exact prior assignment. Capture the default-policy fingerprint before every mutation and prove it unchanged after every verification and rollback.

For each mutation require: fresh state/revision, verified backup/export, exact preconditions, one operation, read-back semantic verification, reverse operation, rollback verification, and unrelated-object comparison. Stop on first failure. Never proceed from connection failure to policy, or policy failure to assignment.

## Sanitized evidence schema

Retain model family, firmware, interface availability, generic capability states, result category, elapsed time, before/after counts, redacted semantic hashes, default-policy-unchanged boolean, rollback result, and discovered limitations. Do not retain credentials, cookies, raw API output, backups, real names, addresses, MACs, object IDs, or local paths in public evidence.

## Cleanup and rollback checklist

- verify disposable assignment is absent or exactly restored;
- verify disposable policy is absent;
- verify disposable connection is absent;
- verify default policy and unrelated objects match the preflight snapshot;
- verify running and saved configuration are consistent under the confirmed contract;
- retain the private backup until a separate read-back confirms cleanup;
- record any incomplete cleanup as a hard #16 blocker.
