# Архитектура обнаружения устройств

RouterKit #21 реализован как read-only этап. Текущий путь принимает только synthetic protected fixture inventories и выдаёт deterministic text/JSON output плюс optional in-memory selection object с ephemeral internal handle. Он не запускает router commands, не сканирует LAN, не меняет policies, не сохраняет selections и не назначает устройства.

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

`routerkit devices status` показывает `contract_unverified`, пока hardware probe не подтвердит production adapter contract. `routerkit devices discover --inventory-file PATH` и `routerkit devices select --inventory-file PATH` предназначены для offline validation и demos.

## Identity Rules

Приоритет identity:

1. documented stable router identifier;
2. normalized MAC/device identifier;
3. explicitly reviewed assignment-stable vendor identifier;
4. unproven vendor или unknown identifiers только как display/dedup hints;
5. IP address только как weak display/correlation hint.

Records объединяются только по display/dedup identity. Один IP с разными stable IDs остаётся разными devices. Одно имя само по себе никогда не объединяет devices. IP-only devices, unknown stable IDs и standalone unreviewed vendor record IDs показываются, но не могут быть выбраны для future assignment.

## Selection

Выбор всегда explicit:

- option `0` всегда означает no device assignment;
- blank input и EOF тоже означают no assignment;
- invalid indexes fail safely;
- nonzero selection требует adapter state `supported`, отсутствие sanitized errors, все sources в `supported` и selectable device;
- weak, untrusted, degraded, malformed или conflicting identities нельзя выбрать;
- selection handles ephemeral, identity-independent, internal only и никогда не сохраняются или печатаются.

Setup integration explicit:

```sh
python3 scripts/routerkit.py setup --discover-devices --device-inventory-file /private/inventory.json
```

Обычные `routerkit setup`, `setup --dry-run`, `setup --apply`, `setup --apply --bootstrap-apply` и `setup --apply --enable-autostart` сохраняют прежнее поведение, если нет `--discover-devices`. Discovery stage идёт после strict planning и до write confirmation. Existing confirmation prompt не меняется.

## Privacy

Обычный локальный interactive output может показывать local-sensitive names, addresses, source names, raw sanitized errors и stable IDs, нужные администратору. JSON помечает эти поля как `local_sensitive`. Public-evidence mode доступен только для discover JSON: он маскирует addresses, заменяет local names и record IDs counters, хэширует local identifiers с ephemeral или caller-provided salt, отдаёт только schema-controlled source categories и сообщает generic error codes/counts вместо raw source names или error text.

Committed fixtures используют только RFC 5737 IPv4 documentation networks, `2001:db8::/32`, locally administered unicast MAC addresses и fictional names. CI проверяет это свойство.

## Adapter Boundary

Future vendor adapter обязан реализовать:

- `probe_capabilities()`;
- `collect()`;
- `parse()`.

External execution должен использовать injected runners, exact argv allowlists, no shell interpolation, clean environments, process groups, concurrent bounded stdout/stderr draining, monotonic deadlines, TERM/KILL cleanup для direct children и descendants, а также sanitized user-facing errors. Adapter states: `supported`, `unsupported`, `contract_unverified`, `malformed_output`, `permission_denied`, `timeout`, `output_too_large`, `source_missing`; fixture confidence values перечислены allowlist.
