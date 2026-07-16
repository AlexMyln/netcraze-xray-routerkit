# Fixture-first Netcraze policy planning

The #15 software core is a pure function:

```text
private local-endpoint manifest
+ protected synthetic router snapshot
+ optional in-memory #21 DeviceSelection
+ optional code-owned adapter proofs
→ deterministic diagnostic ChangePlan + reverse RollbackPlan
```

It has no transport, live adapter, apply command, router command strings, network client, process primitive, or persistent selection state.

## Trust boundaries

Observed display state, semantic equivalence, adapter-proven ownership, and future write authorization are separate. Fixture input can describe objects but cannot assert ownership, update/delete authority, trusted revision, backup success, or live capability. The parser rejects those fields. `AdapterOwnershipProof` requires programmatic construction and exact rollback data. Without it, an exact equivalent object may be reused, an absent object may be proposed for creation, a same-name mismatch is a conflict, and nothing is deleted.

The observed default policy is immutable. A known fixture label permits diagnostics only. Every plan ends with an explicit `default_policy_unchanged` verification, no action can target the default, and unknown/ambiguous evidence blocks future-write readiness.

## Contracts

`routerkit.local-endpoints.v1` contains only contiguous slots 1–3, a safe label, exact loopback (`127.0.0.1` or `::1`), ports 1082–1084, enabled state, and SOCKS5 protocol. The generator publishes it atomically with owner-only permissions. It contains no upstream host, UUID, key, SNI, subscription source, provider, or credential.

`routerkit.netcraze.state.fixture.v1` is read through the shared bounded private-file reader with path/descriptor identity checks and rejection of symlinks, hard links, public POSIX permissions, invalid UTF-8, unknown fields, duplicate IDs/names, malformed references, and excessive cardinality.

Deterministic safe names are `RouterKit-SOCKS-<port>` and `RouterKit-Policy-<port>`. Equivalence compares the supported semantics, never name or ID alone. Unknown required semantics block reuse.

Action order is connection create/reuse/update, policy create/reuse/update, optional explicit assignment last, verification, then proof that the default policy is unchanged. Any conflict blocks every action from being presented as partially ready. The plan fingerprint excludes the snapshot ID, device name, MAC, raw inventory, and secrets.

The simulator mutates only immutable synthetic state, supports failure after any action, rolls back in reverse, and checks initial-state restoration, unrelated-object preservation, default-policy preservation, and rerun idempotency. Simulator success is not hardware evidence.

Local JSON is explicitly `local_sensitive`. Public evidence retains counts, action categories, slot/port context, readiness, and the secret-free fingerprint, while removing local names, IDs, device data, snapshot IDs, raw errors, and paths. Redaction is not an anonymity guarantee.
