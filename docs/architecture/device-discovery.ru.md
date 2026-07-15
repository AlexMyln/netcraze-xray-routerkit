# Архитектура обнаружения устройств

RouterKit #21 реализован как read-only этап. Текущий путь принимает только synthetic protected fixture inventories и выдаёт deterministic text/JSON output плюс optional in-memory selection token. Он не запускает router commands, не сканирует LAN, не меняет policies и не назначает устройства.

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

`routerkit devices status` показывает `contract_unverified`, пока hardware probe не подтвердит production adapter contract. `routerkit devices discover --inventory-file PATH` и `routerkit devices select --inventory-file PATH` предназначены для offline validation и demos.

## Identity Rules

Приоритет identity:

1. documented stable router identifier;
2. normalized MAC/device identifier;
3. explicit vendor record ID;
4. IP address только как weak display/correlation hint.

Records объединяются только по stable identity. Один IP с разными stable IDs остаётся разными devices. Одно имя само по себе никогда не объединяет devices. IP-only devices показываются, но не могут быть выбраны для future assignment.

## Selection

Выбор всегда explicit:

- option `0` всегда означает no device assignment;
- blank input и EOF тоже означают no assignment;
- invalid indexes fail safely;
- weak или conflicting identities нельзя выбрать;
- selection token - opaque `routerkit-device-selection-v1` hash, а не router command.

Setup integration explicit:

```sh
python3 scripts/routerkit.py setup --discover-devices --device-inventory-file /private/inventory.json
```

Обычные `routerkit setup`, `setup --dry-run`, `setup --apply`, `setup --apply --bootstrap-apply` и `setup --apply --enable-autostart` сохраняют прежнее поведение, если нет `--discover-devices`. Discovery stage идёт после strict planning и до write confirmation. Existing confirmation prompt не меняется.

## Privacy

Обычный локальный interactive output может показывать local-sensitive names, addresses и stable IDs, нужные администратору. JSON помечает эти поля как `local_sensitive`. Public-evidence mode маскирует addresses, заменяет local names и record IDs counters, хэширует local identifiers с ephemeral или caller-provided salt и прямо говорит, что это не anonymity.

Committed fixtures используют только RFC 5737 IPv4 documentation networks, `2001:db8::/32`, locally administered unicast MAC addresses и fictional names. CI проверяет это свойство.

## Adapter Boundary

Future vendor adapter обязан реализовать:

- `probe_capabilities()`;
- `collect()`;
- `parse()`.

External execution должен использовать injected runners, exact argv allowlists, no shell interpolation, bounded stdout/stderr, timeouts, output-size limits и sanitized environment. Adapter states: `supported`, `unsupported`, `contract_unverified`, `malformed_output`, `permission_denied`, `timeout`, `output_too_large`.
