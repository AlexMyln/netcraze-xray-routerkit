# Device Discovery Architecture

RouterKit #21 is intentionally read-only. The implemented path accepts only synthetic protected fixture inventories and produces deterministic text/JSON output plus an optional in-memory selection object with an ephemeral internal handle. It never runs router commands, scans the LAN, changes policies, persists selections, or assigns devices.

## Data Flow

```text
protected fixture file
-> fixture adapter
-> RawDeviceRecord
-> NormalizedDevice
-> sorted DiscoveryResult
-> text/JSON/redacted output
-> optional DeviceSelection object
-> no-op read-only selection handoff boundary
```

`routerkit devices status` reports `contract_unverified` until the hardware probe confirms the production adapter contract. `routerkit devices discover --inventory-file PATH` and `routerkit devices select --inventory-file PATH` are for offline validation and demos only.

## Identity Rules

Stable identity preference:

1. documented stable router identifier;
2. normalized MAC/device identifier;
3. explicitly reviewed assignment-stable vendor identifier;
4. unproven vendor or unknown identifiers only as display/dedup hints;
5. IP address only as a weak display/correlation hint.

Records merge only when they share display/dedup identity. Same IP with different stable IDs remains separate. Same name alone never merges. IP-only devices, unknown stable IDs, and standalone unreviewed vendor record IDs are shown but cannot be selected for future assignment.

## Selection

Selection is explicit:

- option `0` is always no device assignment;
- blank input and EOF also produce no assignment;
- invalid indexes fail safely;
- nonzero selection requires adapter state `supported`, no sanitized errors, all sources `supported`, and a selectable device;
- weak, untrusted, degraded, malformed, or conflicting identities cannot be selected;
- selection handles are ephemeral, identity-independent, internal only, and never persisted or printed.

The setup integration is explicit:

```sh
python3 scripts/routerkit.py setup --discover-devices --device-inventory-file /private/inventory.json
```

Plain `routerkit setup`, `setup --dry-run`, `setup --apply`, `setup --apply --bootstrap-apply`, and `setup --apply --enable-autostart` keep their previous behavior unless `--discover-devices` is present. The discovery stage runs after strict planning and before write confirmation. It does not alter the existing confirmation prompt.

## Privacy

Normal local interactive output may show local-sensitive device names, addresses, source names, raw sanitized errors, and stable IDs needed by the administrator. JSON labels those fields with `local_sensitive`. Public-evidence mode is discover-only JSON: it masks addresses, replaces local names and record IDs with counters, hashes local identifiers with an ephemeral or caller-provided salt, emits only schema-controlled source categories, and reports generic error codes/counts rather than raw source names or error text.

Committed fixtures use only RFC 5737 IPv4 documentation networks, `2001:db8::/32`, locally administered unicast MAC addresses, and fictional names. CI checks the fixture set for this property.

## Adapter Boundary

The future vendor adapter must implement:

- `probe_capabilities()`;
- `collect()`;
- `parse()`.

External execution must use injected runners, exact argv allowlists, no shell interpolation, clean environments, process groups, concurrent bounded stdout/stderr draining, monotonic deadlines, TERM/KILL cleanup for direct children and descendants, and sanitized user-facing errors. Adapter states are `supported`, `unsupported`, `contract_unverified`, `malformed_output`, `permission_denied`, `timeout`, `output_too_large`, and `source_missing`; fixture confidence values are enumerated.
