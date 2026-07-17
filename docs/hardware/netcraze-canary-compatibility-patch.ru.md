# Template narrow compatibility patch

Использовать только после P4:

```text
OFF_DEVICE_NARROW_PATCH_REQUIRED
```

Router writes прекращены. Patch выполняется off-device.

Private evidence schema v1 не принимает execution через compatibility patch. Manifest valid только при `execution_source=released_baseline`, оба commits равны `c8f697635c93584e85e76a1d734f8fa797a76b51`, `compatibility_patch=null`. Reviewed patch требует будущей schema version со strict receipt; arbitrary 40-hex patch commit не является evidence.

## Contract gap

- Private evidence reference: `<opaque reference>`
- Phase/check: `<ID>`
- Sanitized schema difference: `<summary>`
- Почему alpha.16 не может безопасно продолжить: `<summary>`
- Router/default state changed before stop: `false`

## Допустим ровно один класс

- [ ] parser mapping одного read-only schema;
- [ ] interface-specific read adapter за explicit contract;
- [ ] exact field normalization;
- [ ] capability detection;
- [ ] один disposable write serialization за disabled-by-default test gate.

Запрещены broad refactor, unrelated cleanup, production writes, generic command runner, browser automation, firewall/default-policy changes, secrets/real identifiers в fixtures, patch на router и normal `routerkit setup` integration.

## Synthetic fixture

- Path: `<path>`
- [ ] Только synthetic reserved identifiers
- [ ] Только минимальная observed semantics
- [ ] Нет raw private evidence
- [ ] Unknown/ambiguous fail closed

## Tests

- [ ] focused parser/adapter/serialization;
- [ ] malformed/unknown fields;
- [ ] default/unrelated guards;
- [ ] no-live static guard;
- [ ] mutation proof;
- [ ] full suite;
- [ ] EN/RU docs sync.

## Independent review

- Reviewer/reference: `<value>`
- Findings: `<none/list>`
- Unresolved: `0`
- Receipt base commit: `c8f697635c93584e85e76a1d734f8fa797a76b51`
- Receipt patch commit: `<reviewed execution commit>`
- Review verdict: `READY_FOR_HARDWARE_REENTRY`
- Focused tests passed: `<true>`
- Full tests passed: `<true>`
- Static guard passed: `<true>`
- Explicit user authorization: `<true>`
- [ ] Scope соответствует выбранному классу
- [ ] Нет invented vendor command/endpoint
- [ ] Нет secret material

## Hardware reentry

- [ ] Focused/full checks pass
- [ ] Zero unresolved findings
- [ ] New explicit authorization
- [ ] Backup/state still valid
- [ ] Осталось минимум 30 minutes
- [ ] Cleanup reserve 15 minutes
- [ ] Возврат к read phase, не напрямую к write

Иначе outcome `PARTIAL_NEEDS_OFF_DEVICE_PATCH` и cleanup.
