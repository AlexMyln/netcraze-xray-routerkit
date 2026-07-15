# Device Discovery Architecture

RouterKit #21 is intentionally read-only. The implemented path accepts only synthetic protected fixture inventories and produces deterministic text/JSON output plus an optional in-memory selection token. It never runs router commands, scans the LAN, changes policies, or assigns devices.

## Data Flow

```text
protected fixture file
-> fixture adapter
-> RawDeviceRecord
-> NormalizedDevice
-> sorted DiscoveryResult
-> text/JSON/redacted output
-> optional DeviceSelection token
```

`routerkit devices status` reports `contract_unverified` until the hardware probe confirms the production adapter contract. `routerkit devices discover --inventory-file PATH` and `routerkit devices select --inventory-file PATH` are for offline validation and demos only.

## Identity Rules

Stable identity preference:

1. documented stable router identifier;
2. normalized MAC/device identifier;
3. explicit vendor record ID;
4. IP address only as a weak display/correlation hint.

Records merge only when they share stable identity. Same IP with different stable IDs remains separate. Same name alone never merges. IP-only devices are shown but cannot be selected for future assignment.

## Selection

Selection is explicit:

- option `0` is always no device assignment;
- blank input and EOF also produce no assignment;
- invalid indexes fail safely;
- weak or conflicting identities cannot be selected;
- the selection token is an opaque `routerkit-device-selection-v1` hash, not a router command.

The setup integration is explicit:

```sh
python3 scripts/routerkit.py setup --discover-devices --device-inventory-file /private/inventory.json
```

Plain `routerkit setup`, `setup --dry-run`, `setup --apply`, `setup --apply --bootstrap-apply`, and `setup --apply --enable-autostart` keep their previous behavior unless `--discover-devices` is present. The discovery stage runs after strict planning and before write confirmation. It does not alter the existing confirmation prompt.

## Privacy

Normal local interactive output may show local-sensitive device names, addresses, and stable IDs needed by the administrator. JSON labels those fields with `local_sensitive`. Public-evidence mode masks addresses, replaces local names and record IDs with counters, hashes local identifiers with an ephemeral or caller-provided salt, and states that this is not anonymity.

Committed fixtures use only RFC 5737 IPv4 documentation networks, `2001:db8::/32`, locally administered unicast MAC addresses, and fictional names. CI checks the fixture set for this property.

## Adapter Boundary

The future vendor adapter must implement:

- `probe_capabilities()`;
- `collect()`;
- `parse()`.

External execution must use injected runners, exact argv allowlists, no shell interpolation, bounded stdout/stderr, timeouts, output-size limits, and a sanitized environment. Adapter states are `supported`, `unsupported`, `contract_unverified`, `malformed_output`, `permission_denied`, `timeout`, and `output_too_large`.
