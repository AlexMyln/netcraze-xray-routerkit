# Архитектура live-install orchestration

## Решение

Issue #41 добавляет один coordinator в unified CLI:

```text
routerkit live-install plan|apply|resume|status
```

Coordinator — state machine, а не второй installer. Он владеет только порядком
stage, одним подтверждением installation scope, state epochs, явными handoff,
stop routing и receipt `routerkit.live-install.v1`. Каждая domain operation
остаётся у существующего RouterKit owner.

Public model имеет две независимые оси:

- `--runtime-mode local-router|external-evidence` определяет locus RouterKit
  runtime files/processes;
- `--transport local-ndmc|external` определяет доставку native component,
  reboot, Proxy/Policy, assignment и DNS.

Для `--transport external` безопасно выводится `external-evidence`: этот флаг
не означает наличие remote shell и не разрешает local `/opt` mutation.

## Переиспользованные semantic owners

| Stage | Existing owner |
| --- | --- |
| USB/EXT4/Entware и system preflight | `preflight.sh --bootstrap-readiness` плюс typed evidence |
| Pinned Xray | `routerkit-bootstrap.py` и repository artifact manifest |
| Protected source | private workspace unified `setup` и `routerkit-profile-source.py` |
| Generation и endpoint manifest | `generate-xray-profiles.py` |
| Strict plan | `routerkit-plan.py --strict` через unified `setup` |
| Backup/install/healthcheck | `backup.sh`, `install-xray-direct.sh`, `healthcheck.sh` |
| Autostart | `routerkit-autostart.py` |
| Persistent local routing | reconcile из `routerkit_routing.py`, вызываемый существующим installer |
| Local native routing | `routerkit-netcraze-live.py` |
| External native routing | `routerkit.netcraze.external-transaction.v1` |

Orchestrator не формирует Xray configuration, не распределяет native object
IDs, не собирает заново commands external transaction, не редактирует routing
overrides и не создаёт новый rollback mechanism.

## Граница runtime execution

`local-router` означает, что coordinator process выполняется на RouterKit
target, а literal `/opt` принадлежит external storage этого router. Для mutable
apply этот mode должен быть выбран явно. Только он может запускать существующие
subprocesses `preflight.sh`, bootstrap apply, setup/generation, backup, install,
healthcheck и autostart. Их реализация, порядок и rollback boundaries не
изменены.

`external-evidence` — mode для workstation/RMM. Его default owner-only receipt
находится в `.routerkit-live-install/receipt.json`; receipt/transaction paths в
локальном `/opt` workstation отвергаются. Ни один runtime subprocess не
запускается.

Для доказанного brownfield runtime `--adopt-existing-runtime` требует явный
owner-only `--endpoint-manifest-file` и fresh strict runtime evidence. RouterKit
проверяет существующий `routerkit.local-endpoints.v1`, связывает его fingerprint
с evidence/receipt и требует enabled selected slot. Fixed contract NC-3812
должен доказать management/network, external EXT4 `/opt`, Entware, точную pinned
semantic Xray release, работающий Xray, manifest-matched loopback listeners,
autostart и прежний reboot recovery. Profile source не принимается и не
читается. Runtime write stages получают `SKIPPED`, evidence-backed checks —
`PASS`, receipt — `runtime_disposition=ADOPTED`.

Без adoption первый external-evidence apply останавливается до runtime work с
`ROUTERKIT_RUNTIME_EXECUTION_REQUIRED`. Существующий RouterKit runtime flow
должен выполнить shell-capable transport на target; `router_exec`, raw MCP
commands и browser/Web UI не являются заменой. Resume принимает fresh runtime
evidence плюс target-generated endpoint manifest и записывает
`EXTERNAL_EXECUTED`. Локальное выполнение записывается как `EXECUTED`.

## State epochs и discovery

Начальная state epoch — `0`. Для одной epoch принимается и переиспользуется один
validated evidence fingerprint. Другой evidence document для той же epoch
считается противоречием и отклоняется. Следующая integer epoch принимается
только с одной из bounded причин state change: `component`, `reboot`, `opt`,
`routerkit-xray`, `contradictory` или `native`. Пропуск epoch запрещён.

Поэтому resume детерминирован: завершённая stage не запускается снова только
из-за остановки на более позднем handoff. Reboot completion требует новой
`reboot` epoch; post-reboot proof включает management, WAN/PPPoE, LAN, Wi-Fi,
USB/EXT4 `/opt`, Entware, Xray и loopback listeners.

## Граница receipt

Checked-in schema receipt:
[`routerkit-live-install.v1.schema.json`](../../hardware/routerkit-live-install.v1.schema.json).
Parent directory должна быть exact owner-private `0700`; файл — `0600`,
bounded, структурно validated и atomically заменяется только поверх валидного
предыдущего receipt. Resume повторно вычисляет intent fingerprint из:

- hardware contract;
- transport mode;
- runtime mode и adoption intent;
- bootstrap-validated pinned artifact identity;
- one-way selected-device fingerprint;
- selected profile slot;
- explicit authorization flags для device move и controlled reboot.

После generation также фиксируется fingerprint существующего local-endpoint
manifest. Raw selection identity и все profile/config secrets остаются за
пределами receipt.

Receipt отдельно хранит runtime disposition и strict evidence fingerprint,
поэтому adopted stages нельзя выдать за локально выполненные.

## Граница native transport

`local-ndmc` делегирует plan и apply существующему reviewed live adapter.
Установка native component намеренно не синтезируется через ndmc; отсутствие
support возвращает handoff для typed operation.

`external` управляет только native router configuration. Он использует
protected snapshots и существующий external transaction protocol. RouterKit
проверяет pre-state, затем external agent доставляет только
exact packet commands. Отдельный fresh running snapshot обязателен, в том числе
для NOOP. Mutating packet может перейти к save только после разрешения
RouterKit running verifier, после чего требуется отдельная saved-state
verification. Transport success никогда не считается acceptance. У native
external stage нет зависимости от Entware SSH.

## DNS и client acceptance

DNS evidence описывает availability, а не preferred public provider.
Существующий verified protected path переиспользуется, если он `any`/unbound
или если его bound-interface set пересекается с interfaces selected policy.
Protected upstream, привязанный только к обычному WAN interface, отсутствующему
в Proxy-only policy, отклоняется. `ip global` не моделируется как DNS repair.

Если precise DNS writer недоступен, orchestration останавливается с
`PROTECTED_DNS_DELTA_REQUIRED`, а не импровизирует write. Final PASS также
требует assignment выбранного устройства от native verifier и bounded
selected-client evidence для real domain resolution и HTTPS. Без client-side
probe transport возвращается `CLIENT_FUNCTIONAL_VERIFICATION_REQUIRED`.

## Уровень завершённости

Production brownfield rerun NC-3812 заблокирован до merge этого runtime-locus
fix с зелёным CI. Post-merge run должен использовать external-evidence adoption
и пройти неизменённые native/DNS/client gates. Clean spare-hardware matrix
остаётся #16.
