# Netcraze/Keenetic policy interface research

Status: `SOFTWARE_PLAN_CORE_READY_HARDWARE_WRITE_CONTRACT_PENDING`

This research used public sources only. No router, LAN, credentials, or local management interface was accessed. Candidate commands and resources below are evidence for a future hardware contract; they are intentionally absent from production planning code.

## Primary evidence

The official KeeneticOS 4.0 [Hero KN-1011 Command Reference Guide](https://docs.help.keenetic.com/cli/4.0/en/cli_manual_kn-1011.pdf) documents:

- a model-specific NDM command tree and an HTTP REST Core Interface whose base resource is `/rci`; a command path maps to the same resource path;
- GET for settings retrieval, POST for create/modify, and DELETE for reset/delete; nested JSON is processed top-to-bottom, but the guide does not claim that a multi-resource request is atomic;
- Proxy interfaces with SOCKS5 protocol and an upstream host/port (`interface proxy protocol`, `interface proxy upstream`), introduced in KeeneticOS 3.09; the separate SOCKS5 UDP option is documented from 4.1;
- IP Policy creation/removal (`ip policy`), up to 16 profiles, a description field, permitted global interfaces, and read-only status through `show ip policy`;
- host-to-policy association by MAC through `ip hotspot host … policy …`;
- asynchronous configuration save and a fail-safe timer/commit/rollback family from 3.08. Fail-safe rollback reboots, so it is not assumed suitable for RouterKit without a disposable canary.

The official Keenetic [startup-config guidance](https://destek.keenetic.com.tr/titan/kn-1811/tr/16479.html) says the startup configuration can be downloaded, retained as a backup, and uploaded to restore settings. This proves a backup mechanism exists, not its safe automation contract for the target Netcraze firmware.

The official guide is for Keenetic Hero, not the target Netcraze device. Command availability, JSON response schemas, authentication, firmware components, Web UI correspondence, save timing, and rollback behavior all require confirmation on the exact model and firmware.

## Contract matrix

| Capability | Candidate interface | Read/write | Evidence | Confidence | Hardware confirmation |
|---|---|---:|---|---|---|
| Connection inventory | NDM interface status/config; RCI mapping | read | Model guide documents interface status and RCI mapping, but not a complete safe Proxy inventory schema | `official_inferred` | Exact JSON fields, completeness, sensitive spillover |
| Policy inventory | `show ip policy`; structured RCI equivalent | read | Model guide documents output and RCI mapping | `official_documented` | Target names/IDs/default marker and schema |
| Device assignment inventory | hotspot/known-host configuration/status | read | Write relation is documented; complete inventory join is not | `official_inferred` | Join with #21 device records and stale entries |
| Default-policy identity | policy status and current Web UI | read | Policy0 examples do not prove a universal default identity | `hardware_confirmation_required` | Exact global/default identity and ambiguity behavior |
| Backup/export | `startup-config` download/export | read | Vendor support article | `official_documented` | Auth, safe bounded export, restore test |
| Create/reuse/update/delete connection | Proxy interface command tree; RCI setting methods | write | Model guide documents protocol/upstream and generic setting methods | `official_inferred` | Full create sequence, ID/name uniqueness, response, deletion |
| Create/reuse/update/delete policy | `ip policy`; RCI setting methods | write | Model guide documents create/remove and fields | `official_documented` | Target limits, exact Proxy reference semantics, default guard |
| Assign/unassign device | `ip hotspot host <MAC> policy`; RCI mapping | write | Model guide documents MAC relationship | `official_documented` | Exact target schema, registered-host prerequisites, rollback |
| Transaction/commit | RCI nested requests; configuration save; fail-safe | write | Ordering/save/fail-safe are documented separately | `official_inferred` | Atomicity, revision token, failure boundary, reboot risk |
| Verification | interface/policy/host status | read | Separate status commands exist | `official_inferred` | Exact post-write equivalence and propagation timing |
| Rollback | exact inverse operations, config restore, fail-safe | write | Mechanisms exist, but no RouterKit transaction contract is proven | `hardware_confirmation_required` | Disposable canary and failure injection |

## Unresolved contract

Primary evidence does not prove stable object IDs, globally unique names, a revision/version token, ownership markers, atomic multi-object behavior, a response safe from unrelated secrets, or a complete equivalent-object algorithm. It also does not prove which administrator roles authorize each read/write on Netcraze firmware. Therefore:

- fixture IDs and default labels are observations only;
- name equality never authorizes reuse or replacement;
- fixtures cannot grant ownership, revision trust, backup success, or write authority;
- no live adapter or apply command is implemented;
- #21 still needs the target read contract, #15 needs the target write contract, and #16 needs the disposable hardware proof.
