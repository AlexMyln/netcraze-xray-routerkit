# Fixture-first Netcraze policy planning

The #15 software core is a pure function:

```text
private local-endpoint manifest
+ protected synthetic router snapshot
+ optional in-memory #21 DeviceSelection
→ deterministic diagnostic ChangePlan + reverse RollbackPlan
```

It has no transport, live adapter, apply command, router command strings, network client, process primitive, or persistent selection state.

## Trust boundaries

Observed display state, semantic equivalence, and future write authorization are separate. Fixture input can describe objects but cannot assert ownership, update/delete authority, trusted revision, backup success, or live capability. The parser rejects those fields. The fixture-first planner has no caller-created authorization object: an exact equivalent object may be reused, an absent object may be proposed for creation, a same-name mismatch is a conflict, an existing assignment move is blocked, and nothing pre-existing is updated or deleted. Future hardware-confirmed adapter work must define ownership markers, revision binding, exact before-state, concurrency checks, and rollback semantics.

The observed default policy is immutable. A canonical snapshot validator proves that every policy, assignment, default reference, and observed default flag is internally consistent. The static plan field `default_policy_not_targeted` is derived from the validated default identity, action targets, generated names/IDs, and typed dependencies. Synthetic simulation separately compares a canonical before/after projection containing the default policy and its referenced connection semantics. Unknown or ambiguous default identity remains explicit diagnostic state and blocks planning readiness.

## Contracts

`routerkit.local-endpoints.v1` contains only contiguous slots 1–3, code-owned labels (`primary`, `fallback-1`, `fallback-2`), exact loopback (`127.0.0.1` or `::1`), ports 1082–1084, enabled state, and SOCKS5 protocol. Raw profile names never enter the manifest. Publication uses a private parent directory, bounded exclusive temporary file, file `fsync`, validated no-clobber replacement, and parent-directory `fsync`. An existing destination is replaceable only when it is a recognized prior RouterKit manifest; unrelated or unsafe files are preserved and rejected. A valid stale manifest is retired before current generation so failed generation cannot leave old evidence at the current path.

`routerkit.netcraze.state.fixture.v1` is read through the shared bounded private-file reader with path/descriptor identity checks and rejection of symlinks, hard links, public POSIX permissions, invalid UTF-8, unknown fields, duplicate IDs/names, orphan policy/assignment/default references, duplicate or multi-policy device assignments, inconsistent default evidence, impossible state/capability combinations, incomplete proven-default semantics, and excessive cardinality.

Deterministic safe names are `RouterKit-SOCKS-<port>` and `RouterKit-Policy-<port>`. Equivalence compares the supported semantics, never name or ID alone. Unknown required semantics block reuse.

Action order is connection create/reuse, policy create/reuse, optional assignment of an unassigned synthetic device, verification, then the static default-policy non-targeting proof. A new policy carries a typed planned-connection dependency; simulation creates the connection first, assigns a deterministic simulation-only ID, resolves the dependency, and never presents that ID as target-hardware evidence. Any conflict blocks every action from being presented as partially ready. The plan fingerprint includes dependency categories and the derived default-policy invariant while excluding raw profile names, snapshot ID, device name, MAC, raw inventory, and secrets.

The simulator mutates only immutable synthetic state, validates consistency before simulation, after every mutation, after each rollback, before success, and before idempotent rerun planning. It supports failure after any action, rolls back only objects or assignments created by that simulation, and checks initial-state restoration, unrelated-object preservation, canonical default-policy projection equality, and rerun idempotency. Simulator success is not hardware evidence.

When `setup --plan-netcraze` is combined with `--apply`, the output immediately before confirmation or the first `--yes` mutation explicitly states that the displayed Netcraze plan is offline preview only and excluded from RouterKit apply. Cancellation and final summaries retain the same boundary.

Local JSON is explicitly `local_sensitive`. Public evidence retains counts, action categories, slot/port context, readiness, and the secret-free fingerprint, while removing local names, IDs, device data, snapshot IDs, raw errors, and paths. Redaction is not an anonymity guarantee.
