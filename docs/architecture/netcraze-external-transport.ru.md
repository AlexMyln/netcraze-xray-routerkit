# Внешний transport для Netcraze transaction

Этот документ фиксирует архитектурную границу #40/#41 после production evidence
NC-3812: официальный Netcraze MCP/RMM умеет читать и исполнять native router
commands, но не предоставляет arbitrary Entware shell.

## Граница ответственности

RouterKit владеет всей deterministic semantics:

```text
protected fresh running-config snapshot
-> существующий RouterKit live parser
-> существующий semantic-compatibility planner
-> immutable external transaction packet
-> external transport исполняет exact native commands
-> protected fresh running-config snapshot
-> существующий semantic-compatibility verifier
```

Transport отвечает только за доставку. Поэтому официальный Netcraze MCP/RMM —
поддерживаемый transport для native Proxy/Policy даже без SSH и arbitrary Linux
shell. Локальный Entware `ndmc` остаётся поддерживаемым direct path в
`scripts/routerkit-netcraze-live.py`.

Browser/Web UI по-прежнему запрещён как автоматический fallback.

## Protected snapshot contract

`scripts/routerkit-netcraze-external.py` принимает running-config snapshot
только из bounded UTF-8 regular file. На POSIX файл должен принадлежать current
user, не иметь group/world permissions, symlink и hard-link aliases. RouterKit
проверяет identity path/file descriptor до и после чтения. Raw snapshot никогда
не печатается и не включается в packet.

Endpoint manifest сохраняет существующий private-file contract.

## External transaction v1

Schema packet: `routerkit.netcraze.external-transaction.v1`; checked-in shape —
[`routerkit-netcraze-external-transaction.v1.schema.json`](../../hardware/routerkit-netcraze-external-transaction.v1.schema.json).
Packet содержит:

- поддерживаемый hardware contract `nc3812-netcrazeos-5.1.5`;
- fingerprints manifest, pre-state, expected post-state и Default guard;
- deterministic bindings slot -> native Proxy -> native Policy;
- action `create`/`reuse` для каждого Proxy и Policy;
- optional assignment action `none`/`reuse`/`assign`/`move`;
- exact ordered running-state commands и exact reverse rollback commands;
- явные `backup_required`, `write_required`, `save_required` и two-phase verification gates;
- transaction fingerprint, покрывающий все остальные поля;
- никаких raw running config, profile source, authentication material или
  transport success assertions.

Packet file создаётся один раз с owner-only permissions и не перезаписывается.
Для assignment он неизбежно содержит selected MAC внутри exact native command;
такой packet является private operational data.

Integrity packet не заменяет fresh state. До write `verify --phase pre` должен
подтвердить полный parser-visible pre-state fingerprint. После commands
`verify --phase running` должен подтвердить expected post-state, уникальные
semantic Proxy/Policy matches, assignment и неизменность Default guard. Строки
успеха MCP/HTTP/CLI не считаются доказательством.

## Two-phase mutation и save

Для mutating packet:

1. получить fresh running-config snapshot и построить packet;
2. получить и проверить native saved/startup rollback checkpoint, требуемый
   `backup_required`;
3. выполнить `verify --phase pre` для состояния непосредственно перед write;
4. исполнить только `commands`, точно в указанном порядке;
5. получить fresh running-config snapshot;
6. выполнить `verify --phase running`;
7. исполнить exact `save_command` только при `save_authorized=true`;
8. если transport отдаёт saved/startup configuration в том же native syntax,
   получить fresh snapshot и выполнить `verify --phase saved`;
9. отдельно выполнить functional service checks из runbook.

External agent не имеет права добавлять команды, менять их порядок,
восстанавливать Proxy/Policy semantics из model memory или считать успешный
transport response доказательством.

## NOOP contract и текущий NC-3812

Если каждый Proxy и Policy является exact semantic reuse, а assignment равен
`none` или `reuse`, packet содержит:

```text
write_required=false
save_required=false
backup_required=false
commands=[]
```

Fresh post-state всё равно проходит verifier. Только ради NOOP не создаётся
running-config backup, не выполняется native write и не вызывается
`system configuration save`. Existing human-readable production descriptions
вроде `XRAY-NL` / `VPN-NL` продолжают exact semantic reuse через
`routerkit_netcraze_live_compat.py`; переименование не планируется и не нужно.

## Статус #40 и #41

Live adapter из #40 теперь разделён на RouterKit-owned semantics и заменяемую
доставку через local `ndmc` или external MCP/RMM. Remaining hardware proof может
выполняться без SSH и обязан завершаться RouterKit verification.

Orchestration из #41 теперь компонует этот packet protocol через
`routerkit live-install`. Она не предполагает, что RouterKit можно запустить в
Entware через любой management transport. State machine сохраняет exact packet
commands, требует fresh running readback даже для NOOP и gates save/saved-state
verification для mutation. Software implementation всё ещё требует
документированный post-merge brownfield rerun на NC-3812 до закрытия #41.
