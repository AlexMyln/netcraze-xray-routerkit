# Исследование интерфейса обнаружения устройств

Дата доступа: 2026-07-15.

## Итог

Текущий статус RouterKit:

`SOFTWARE_CORE_READY_HARDWARE_CONTRACT_PENDING`

Официальный справочник KeeneticOS CLI описывает несколько read-only источников, которые могут участвовать в обнаружении локальных устройств, но сам по себе не доказывает точную форму вывода для Netcraze Hopper 4G+ NC-2312 с firmware `5.00.C.12.0-0`. Поэтому RouterKit реализует fixture-first core, нормализацию, вывод, redaction и выбор устройства, а vendor adapter остаётся выключенным в состоянии `contract_unverified`.

## Evidence

| Evidence | Publisher | URL | Claim | Confidence |
| --- | --- | --- | --- | --- |
| Command Reference Guide, Hero KN-1011, OS 4.0 | Keenetic Limited | https://docs.help.keenetic.com/cli/4.0/en/cli_manual_kn-1011.pdf | CLI reference указывает metadata `Change settings` и содержит раздел HTTP API `/rci`. | official_documented |
| `show ip dhcp bindings` | Keenetic Limited | https://docs.help.keenetic.com/cli/4.0/en/cli_manual_kn-1011.pdf | Section 3.146.52, guide p. 537, marked `Change settings No`; example содержит DHCP lease fields: IP, MAC, expiry, hostname. | official_documented |
| `show associations` | Keenetic Limited | https://docs.help.keenetic.com/cli/4.0/en/cli_manual_kn-1011.pdf | Section 3.146.4, guide pp. 475-476, marked `Change settings No`; example содержит Wi-Fi station fields: MAC, AP/interface, authentication, uptime, radio metrics. | official_documented |
| `show ip hotspot summary` | Keenetic Limited | https://docs.help.keenetic.com/cli/4.0/en/cli_manual_kn-1011.pdf | Section 3.146.57, guide pp. 542-543, marked `Change settings No`; summary registered hosts включает active state и names для traffic counters. | official_documented |
| REST Core Interface | Keenetic Limited | https://docs.help.keenetic.com/cli/4.0/en/cli_manual_kn-1011.pdf | `/rci` описан как HTTP API base для доступа к settings через HTTP methods. | official_documented |
| Keenetic User Manual | Keenetic GmbH | https://support.keenetic.com/eu/titan/kn-1811/en/31111-keenetic-mobile-application.html | Manual index содержит Web Interface, Status, Traffic monitor и Wi-Fi monitor, но не фиксирует local client API schema. | official_documented |
| KeeneticOS overview | Keenetic GmbH | https://keenetic.com/en/keenetic-os | KeeneticOS описан как modular OS для Keenetic products с monitoring и device-oriented management. | official_documented |

## Доказанные факты

- Официальный CLI reference существует и различает команды, меняющие настройки, и read-only команды.
- `show ip dhcp bindings`, `show associations` и `show ip hotspot summary` описаны как candidate read-only commands с `Change settings No`.
- Документированные поля покрывают часть #21: IP/MAC/hostname из DHCP, Wi-Fi association MAC/interface/radio state и hotspot active/name summaries.
- `/rci` описан как REST Core Interface base, но local auth и точное mapping-to-command behavior нужно подтвердить на целевом устройстве.

## Выводы с требованием проверки

- Production adapter, вероятно, должен объединять несколько источников.
- DHCP leases недостаточны для безопасного assignment: IP может переиспользоваться, а lease может быть stale/offline.
- Wi-Fi association может доказать online wireless presence, но не всегда даёт friendly name или policy.
- Hotspot data может дать registered-host state и policy, но поля и coverage нужно проверить на hardware.

## Нерешённые детали

- Exact schema на Netcraze Hopper 4G+ NC-2312 firmware `5.00.C.12.0-0`.
- Доступен ли `/rci/show/...` с machine-readable JSON для нужных источников.
- Authentication model для local `/rci` или CLI automation.
- Какой источник безопасно показывает existing policy assignment.
- Нужны ли отдельные Ethernet/FDB и policy bindings commands.
- Не содержит ли raw output unrelated sensitive configuration.

## Решение в реализации

Сделано:

- fixture-first models и adapter states;
- только protected local fixture input;
- deterministic normalization, trusted-ID selection gating, sorting, JSON/text output, public-evidence redaction и fail-closed selection;
- `routerkit devices` и `scripts/routerkit-devices.py`;
- `routerkit setup --discover-devices` как explicit read-only stage после strict planning.

Не сделано:

- выполнение команд на реальном роутере;
- active LAN scanning;
- policy writes или device assignment;
- proxy connection changes;
- default-policy changes.
