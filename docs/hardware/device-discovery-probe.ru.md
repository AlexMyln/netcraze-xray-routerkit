# Read-only probe packet для обнаружения устройств

Статус: `SOFTWARE_CORE_READY_HARDWARE_CONTRACT_PENDING`.

Не запускайте packet на production router. Используйте только spare/disposable hardware window. Raw outputs являются local-sensitive. Не публикуйте raw output.

## Текущее состояние

`scripts/probe-device-discovery-readonly.sh` намеренно не выполняет Netcraze/Keenetic discovery commands по умолчанию. Он только печатает contract-pending status. Так RouterKit не превращает guessed commands в executable defaults.

## Цели hardware window

Подтвердить на Netcraze Hopper 4G+ NC-2312 firmware `5.00.C.12.0-0`:

- доступны ли documented read-only CLI commands;
- может ли `/rci` отдавать equivalent structured output;
- exact output fields и encoding;
- authentication model;
- consistency между DHCP leases, Wi-Fi associations, hotspot hosts, Ethernet/FDB data и policy bindings;
- соответствие Web UI;
- можно ли читать existing policy assignment без unrelated configuration.

## Candidate sources для проверки

Официальный KeeneticOS CLI reference документирует read-only candidates:

- `show ip dhcp bindings`;
- `show associations`;
- `show ip hotspot summary`;
- `show ip arp`;
- `/rci` REST Core Interface.

Это candidates, а не executable RouterKit defaults, пока они не проверены на target hardware.

## Safety rules

- no configuration mode;
- no write endpoints;
- no reboot;
- no service action;
- no active scan;
- no firewall, TPROXY, REDIRECT или `xkeen -start`;
- stop on first unexpected result;
- output только в private `0700` directory с `0600` files;
- перед sharing evidence redact/hash stable local identifiers;
- удалить private probe output после review.

## Sanitized evidence

Оставлять только command availability, schema summaries, field lists, source consistency notes, firmware/model metadata и pass/fail status. Не сохранять raw device names, addresses, MACs, policy names, backups, credentials или inventories в repository.
