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
- bootstrap-validated pinned artifact identity;
- one-way selected-device fingerprint;
- selected profile slot;
- explicit authorization flags для device move и controlled reboot.

После generation также фиксируется fingerprint существующего local-endpoint
manifest. Raw selection identity и все profile/config secrets остаются за
пределами receipt.

## Граница transport

`local-ndmc` делегирует plan и apply существующему reviewed live adapter.
Установка native component намеренно не синтезируется через ndmc; отсутствие
support возвращает handoff для typed operation.

`external` использует protected snapshots и существующий external transaction
protocol. RouterKit проверяет pre-state, затем external agent доставляет только
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

Implementation software-complete после PASS focused и full CI. Это не закрывает
hardware gate: после merge #41 требует один brownfield rerun полного
command/resume flow на NC-3812. Clean spare-hardware matrix остаётся #16.
