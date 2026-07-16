# Fixture-first Netcraze policy planning

The #15 software core is a pure function:

```text
private local-endpoint manifest
+ protected synthetic router snapshot
+ optional in-memory #21 DeviceSelection
→ deterministic diagnostic ChangePlan with embedded desired semantics,
  exact source-snapshot binding, and reverse RollbackPlan
```

It has no transport, live adapter, apply command, router command strings, network client, process primitive, or persistent selection state.

## Trust boundaries

Observed display state, semantic equivalence, and future write authorization are separate. Fixture input can describe objects but cannot assert ownership, update/delete authority, trusted revision, backup success, or live capability. The parser rejects those fields. The fixture-first planner has no caller-created authorization object: an exact equivalent object may be reused, an absent object may be proposed for creation, a same-name mismatch is a conflict, an existing assignment move is blocked, and nothing pre-existing is updated or deleted. Future hardware-confirmed adapter work must define ownership markers, revision binding, exact before-state, concurrency checks, and rollback semantics.

The observed default policy is immutable. A canonical snapshot validator proves that every policy, assignment, default reference, and observed default flag is internally consistent. The static plan field `default_policy_not_targeted` is derived from the validated default identity, action targets, generated names/IDs, and typed dependencies. Synthetic simulation separately compares a canonical before/after projection containing the default policy and its referenced connection semantics. Unknown or ambiguous default identity remains explicit diagnostic state and blocks planning readiness.

Every `SelectedDeviceRef` is normalized again at the public planner boundary, even when directly constructed in-process. The shared #21/#15 MAC helper accepts globally or locally administered unicast identities, canonicalizes case and supported separators, and rejects malformed, all-zero, broadcast, multicast/group, non-text, and control-character input. Display names are bounded, nonempty normalized text. Invalid references fail before any plan action is constructed.

## Contracts

`routerkit.local-endpoints.v1` contains only contiguous slots 1–3, code-owned labels (`primary`, `fallback-1`, `fallback-2`), exact loopback (`127.0.0.1` or `::1`), ports 1082–1084, enabled state, and SOCKS5 protocol. Raw profile names never enter the manifest. On POSIX, its publication directory must be owned by the current user with exact mode `0700`; existing `0755`, `0750`, traversal-only, group/world-accessible, or special-bit directories are rejected without being changed. Publication then uses a bounded exclusive temporary file, file `fsync`, validated no-clobber replacement, and parent-directory `fsync`. An existing destination is replaceable only when it is a recognized prior RouterKit manifest; unrelated or unsafe files are preserved and rejected. A valid stale manifest is retired before current generation so failed generation cannot leave old evidence at the current path.

`routerkit.netcraze.state.fixture.v1` is read through the shared bounded private-file reader with path/descriptor identity checks and rejection of symlinks, hard links, public POSIX permissions, invalid UTF-8, unknown fields, duplicate IDs/names, orphan policy/assignment/default references, duplicate or multi-policy device assignments, inconsistent default evidence, impossible state/capability combinations, incomplete proven-default semantics, and excessive cardinality.

Deterministic safe names are `RouterKit-SOCKS-<port>` and `RouterKit-Policy-<port>`. Equivalence compares the supported semantics, never name or ID alone. Unknown required semantics block reuse.

Action order is connection create/reuse, policy create/reuse, optional assignment of an unassigned synthetic device, verification, then the static default-policy non-targeting proof. A new policy carries a typed planned-connection dependency; simulation creates the connection first, assigns a deterministic simulation-only ID, resolves the dependency, and never presents that ID as target-hardware evidence. Any conflict blocks every action from being presented as partially ready.

`ChangePlan` embeds the complete immutable desired profile tuple, including slot, code-owned label, host, port, enabled state, protocol, and authentication mode. It also binds to a deterministic local-sensitive fingerprint of every planner-relevant source snapshot semantic: schema, state/staleness, complete default-policy projection, connections, policies, assignments, and readiness capabilities. Snapshot IDs and inventory ordering are not treated as router revision proof.

Before simulation, a pure integrity validator checks the plan schema, desired profiles, action IDs/order, profile slots/endpoints, operations/object types, proposed semantics, typed dependencies, readiness/preconditions, verification checks, static default-policy invariant, rollback correspondence, selected-device identity, and local integrity fingerprint. The simulator accepts only `(plan, snapshot)`; there is no separately supplied manifest. A source-snapshot mismatch or plan tamper returns a generic failure before any action. Every create/reuse/assignment and aggregate verify action inspects actual synthetic state against the plan-bound semantics. Successful simulation also proves a rebuild from the same desired profiles contains only reuse and verify operations for one to three slots, including mixed create/reuse cases. Simulator success is not hardware evidence.

When `setup --plan-netcraze` is combined with `--apply`, the output immediately before confirmation or the first `--yes` mutation explicitly states that the displayed Netcraze plan is offline preview only and excluded from RouterKit apply. Cancellation and final summaries retain the same boundary.

Local JSON is explicitly `local_sensitive` and labels `local_integrity_fingerprint` plus `source_snapshot_fingerprint`; neither is a router revision token or write authorization. Public evidence never emits either local fingerprint. It uses a separate `public_plan_fingerprint` whose minimized semantic identity includes a SHA-256 digest of the complete canonical default-policy projection, along with operation counts/types, profile slots, readiness, selected-device presence, and public-safe dependency categories. Public evidence removes raw local names, IDs, MACs, snapshot IDs, errors, and paths. Its stable fingerprint permits correlation, so redaction is not an anonymity guarantee, and the public fingerprint never authorizes simulation or writes.
