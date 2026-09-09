# Установка с нуля — RU

Этот документ описывает **актуальный короткий путь** от USB-накопителя до работающего RouterKit/Xray и VPN-политик для выбранных устройств.

Для production-работы также обязательны:

- [`../AGENTS.md`](../AGENTS.md) — нормативные правила для AI/automation-агентов;
- [`live-install-runbook.ru.md`](live-install-runbook.ru.md) — боевой runbook без лишних audit-loop.

> Не превращайте эту процедуру в цепочку повторных preflight/audit. Один PASS действует до изменения соответствующего состояния.

## 1. Сначала обычная сеть

До RouterKit должны нормально работать:

- WAN/PPPoE или другой основной Internet uplink;
- LAN/DHCP/DNS;
- Wi-Fi;
- доступ к роутеру через локальный management или RMM.

Если RouterKit ставится во время миграции на новый роутер, сначала закончите обычный network cutover. RouterKit не должен быть инструментом восстановления базовой сети.

## 2. Один preflight

За один проход зафиксируйте только необходимые факты:

- модель и точную firmware/build;
- архитектуру;
- внешний USB и его размер;
- доступность RMM/management;
- установлен ли системный компонент **Proxy client**, если задача включает VPN для конкретных устройств;
- требуется ли reboot после установки компонентов.

Для текущего RouterKit execution path требуется Linux `aarch64`/`arm64`.

Если состояние не изменилось, повторно этот audit не выполняйте.

## 3. Подготовить USB

Нужен однозначно идентифицированный **внешний** USB-накопитель.

Рекомендуется:

- нормальная брендовая флешка или USB SSD;
- EXT4;
- не хранить там лишние пользовательские данные;
- не публиковать будущий `/opt` через SMB/WebDAV/FTP/DLNA;
- не вытаскивать накопитель во время работы.

Форматировать можно только после однозначной идентификации target и явного разрешения оператора.

Ожидаемо:

```text
USB_FS=EXT4
USB_RW=OK
```

## 4. Установить Entware/OPKG

Штатным способом для текущей модели/прошивки установите необходимые компоненты USB/EXT4/OPKG/SSH и активируйте Entware.

Критическое конечное состояние:

```text
/opt -> внешний EXT4 USB
/opt/etc exists
/opt/sbin exists
/opt/bin/opkg или /opt/sbin/opkg работает
uname -m = aarch64|arm64
```

Не считайте наличие пустого каталога `/opt` доказательством: проверьте реальный mount source.

Если штатная установка компонента требует reboot и оператор разрешил завершить установку, reboot является частью процедуры, а не автоматическим blocker.

## 5. Получить актуальный RouterKit

Используйте только текущий репозиторий:

```text
https://github.com/AlexMyln/netcraze-xray-routerkit
```

Зафиксируйте используемый commit SHA.

Не подменяйте RouterKit случайными xkeen/gist/iptables-инструкциями.

## 6. Bootstrap Xray

Сначала read-only check:

```sh
python3 scripts/routerkit.py bootstrap
```

Затем штатный transactional apply:

```sh
python3 scripts/routerkit.py bootstrap --apply --yes
```

Bootstrap обязан сохранить следующие gates:

- поддерживаемая архитектура;
- literal `/opt`;
- trusted `/opt`-scoped `opkg`;
- HTTPS/TLS/destination policy;
- repository-pinned Xray artifact;
- SHA-256 архива;
- semantic version pinned release;
- safe extraction;
- transactional replacement/rollback.

Не скачивайте вручную другой `latest Xray` вместо pinned artifact.

Подтверждённые live-формы Entware installed-status:

```text
Status: install ok installed
Status: install user installed
```

Официальный `xray version` может добавлять стандартный banner/build metadata после точной pinned semantic version. Это уже поддерживаемый compatibility case и не требует нового исследования.

## 7. Получить profile source безопасно

Нормальные способы ввода:

- hidden interactive input;
- owner-only protected file;
- выделенная переменная `ROUTERKIT_*`.

Raw subscription/VLESS данные нельзя помещать в argv, Git, обычный отчёт или чат.

RouterKit поддерживает raw VLESS и HTTPS subscription/shortlink. Если shortlink возвращает HTML landing page, а не raw/Base64 VLESS, используйте штатное защищённое действие получения subscription source из этой страницы и передайте полученный source обратно в RouterKit resolver.

Не нужно заставлять оператора вручную собирать прямой `vless://`, если сервис уже предоставляет subscription action.

## 8. Setup + install + healthcheck + autostart

Предпочтительный integrated flow:

```sh
python3 scripts/routerkit.py setup --apply --enable-autostart
```

Конкретные source/index options зависят от выбранного способа ввода. Логическая последовательность одна:

```text
profile source
-> secret-safe node selection
-> generation
-> strict plan
-> preflight
-> RouterKit backup
-> install
-> healthcheck
-> autostart enable
```

Не вставляйте дополнительные audit stages между успешными шагами.

Ожидаемые listener:

```text
127.0.0.1:1082  primary
127.0.0.1:1083  fallback-1, если выбран
127.0.0.1:1084  fallback-2, если выбран
```

Они не должны слушать `0.0.0.0`, LAN IP или WAN IP.

## 9. Proxy client проверять ДО финала

Если конечная цель — VPN для конкретных устройств, штатный системный компонент **Proxy client** должен быть установлен до объявления всей задачи завершённой.

Если его установка требует reboot:

1. сохранить configuration;
2. выполнить один controlled reboot, если он разрешён;
3. после возврата проверить обычную сеть;
4. одновременно выполнить реальный RouterKit after-reboot proof.

## 10. After-reboot proof

После controlled reboot проверьте:

```text
RMM/management=ONLINE
PPPoE/default route=UP
DNS=OK
LAN=OK
Wi-Fi=OK
USB=present
/opt=mounted from intended external EXT4 device
Entware=available
Xray=RUNNING
127.0.0.1:1082=LISTENING
127.0.0.1:1083=LISTENING, если настроен
127.0.0.1:1084=LISTENING, если настроен
```

Listener ownership должен принадлежать ожидаемому Xray process.

Если вспомогательный verifier имеет известный false-negative, а независимые bounded checks точно доказывают PID/executable/listener ownership и реальную работу через SOCKS, фиксируйте tooling defect отдельно. Не переустанавливайте рабочий Xray только из-за diagnostic helper.

## 11. Создать native Proxy connections

Для каждого RouterKit SOCKS endpoint создайте отдельный штатный Proxy interface роутера:

```text
XRAY-NL       -> SOCKS5 127.0.0.1:1082
XRAY-SMART-RU -> SOCKS5 127.0.0.1:1083
XRAY-US       -> SOCKS5 127.0.0.1:1084
```

Имена — пример; используйте осмысленные названия для выбранных профилей.

Не включайте SOCKS authentication, если RouterKit listener её не использует.

Не открывайте локальные SOCKS порты наружу.

## 12. Создать Internet access policies

На каждый Proxy создайте отдельную native policy:

```text
VPN-NL       -> только XRAY-NL
VPN-SMART-RU -> только XRAY-SMART-RU
VPN-US       -> только XRAY-US
```

Штатная схема:

```text
Xray loopback SOCKS
-> native Proxy interface
-> native Internet access policy
-> выбранное зарегистрированное устройство
```

Не заменяйте её TPROXY, REDIRECT, xkeen, iptables marks или ручным редактированием config.

## 13. Назначить только выбранные устройства

Сначала покажите оператору список зарегистрированных клиентов и попросите выбрать, какие устройства отправить в какой профиль.

Нельзя автоматически назначать:

- весь segment;
- Home network целиком;
- all clients;
- все устройства по умолчанию.

Не выбранные клиенты должны сохранить прямой PPPoE как обычный путь, если оператор явно не просил другое.

Если оператор отдельно разрешил добавить Proxy как fallback в Default, это допустимо в рамках его решения, но итоговый отчёт обязан точно указать изменение. После этого нельзя писать `DEFAULT_POLICY_UNCHANGED=TRUE`.

## 14. Проверить реальный клиентский трафик

Для каждого назначенного устройства подтвердите, насколько позволяет router/RMM:

- правильную policy assignment;
- активные session/counters на соответствующей policy/Proxy;
- ответный трафик;
- при возможности — реальный внешний egress выбранного профиля.

Одновременно финально проверьте:

```text
PPPoE=UP
DNS=OK
LAN=OK
Wi-Fi=OK
RMM=ONLINE
Xray=RUNNING
```

## 15. SSH hardening

После рабочей установки можно установить public SSH key оператора.

Правила:

- private key не читать и не копировать;
- `~/.ssh` обычно `0700`;
- `authorized_keys` обычно `0600`;
- сначала доказать реальный отдельный key login;
- только потом отключать password authentication.

Если key login удалённо проверить нельзя, оставьте password authentication включённой и запишите follow-up. Не блокируйте удалённый доступ ради «идеального hardening».

## 16. Backup

После успешной настройки желательно иметь:

1. штатно сохранённый startup-config роутера;
2. RouterKit/Entware backup:

```sh
sh scripts/backup.sh
```

Backup может содержать секреты. Не публикуйте его.

Отсутствие отдельно скачанного post-cutover backup не должно бесконечно блокировать уже принятую production-установку, если оператор явно принял этот риск и сохранённый startup-config/recovery path достаточны для текущей задачи.

## 17. Финальный отчёт

Разделяйте service state и tooling state.

Пример:

```text
INSTALLATION_RESULT=PASS
VPN_SERVICE=PASS
CLIENT_ROUTING=PASS
AFTER_REBOOT=PASS
TOOLING_ISSUES=#...
SSH_HARDENING=FOLLOW_UP
```

Не маркируйте работающий VPN как `PARTIAL` только из-за известного false-negative вспомогательного verifier.

## 18. Подтверждённый NC-3812 baseline

Live-установка 2026-09-09 подтвердила на Netcraze Hopper SE NC-3812 / NetcrazeOS 5.1.5:

- `aarch64`;
- EXT4 USB -> `/opt`;
- Entware/OPKG;
- pinned Xray 26.3.27;
- RouterKit setup/healthcheck;
- autostart после controlled reboot;
- три loopback SOCKS endpoint;
- native Proxy client + policies;
- selected-client VPN routing;
- сохранение рабочей обычной сети.

Для того же model/firmware contract это установленный live evidence, а не повод каждый раз начинать исследование совместимости с нуля.
