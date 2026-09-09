# RouterKit: боевой runbook установки

Этот документ — **короткий production-путь** для оператора или AI/automation-агента, который устанавливает RouterKit на поддерживаемый роутер семейства Netcraze/Keenetic.

Сначала прочитайте и соблюдайте корневой [`AGENTS.md`](../AGENTS.md). Он является нормативным контрактом поведения агента. Этот runbook превращает его правила в последовательность установки.

Цель — безопасно закончить задачу оператора, а не превращать одно окно обслуживания в бесконечный аудит.

## 1. Модель работы

На текущем состоянии выполняется один ограниченный discovery/preflight, после чего нужно действовать.

Новая «эпоха состояния» начинается только после существенного изменения:

- reboot роутера;
- изменение firmware/components;
- изменение USB или `/opt`;
- замена RouterKit/Xray;
- оператор сообщил drift;
- новая команда дала данные, противоречащие ранее подтверждённому состоянию.

Внутри одной эпохи повторно используйте уже полученные PASS. Не повторяйте тот же аудит ради дополнительной уверенности.

## 2. Полный happy path

Если задача включает VPN для конкретных устройств, используйте такой порядок:

1. обычная сеть роутера стабильна;
2. один ограниченный preflight;
3. внешний USB однозначно определён;
4. EXT4 + Entware смонтированы в буквальный `/opt`;
5. RouterKit bootstrap;
6. защищённое получение profile source;
7. setup/generation/strict plan/install/healthcheck;
8. включение autostart;
9. установлен штатный компонент Proxy client;
10. один controlled reboot, если он разрешён/необходим;
11. after-reboot proof;
12. native Proxy interfaces;
13. native Internet access policies;
14. назначение только выбранных оператором клиентов;
15. финальная проверка production-сети;
16. SSH hardening и backup как follow-up.

Если конечная цель включает маршрутизацию отдельных клиентов, наличие компонента **Proxy client** нужно проверять заранее, а не после заявления «RouterKit установлен».

## 3. Один ограниченный preflight

До destructive/mutable действий нужно установить только факты, необходимые для безопасного выполнения:

- точная модель и firmware/build;
- архитектура (`aarch64`/`arm64` для текущего RouterKit path);
- доступность management/RMM;
- здоровье обычных WAN/LAN/Wi-Fi;
- идентичность и размер внешнего USB;
- установлен ли native Proxy client, если нужен per-device VPN;
- нужен ли reboot для ожидающих component changes.

Не запускайте второй inventory/configuration audit, пока не изменилась эпоха состояния.

## 4. USB и Entware

Форматирование разрушительно. Выполняйте его только если внешний накопитель определён однозначно и оператор разрешил форматирование.

Ожидаемое конечное состояние:

```text
USB=нужный внешний накопитель
FILESYSTEM=EXT4
MOUNT=/dev/... -> /opt
/opt/etc=present
/opt/sbin=present
opkg=working and /opt-scoped
ARCH=aarch64|arm64
```

Если Entware activation или обязательный системный компонент требуют reboot и оператор уже разрешил закончить установку, reboot является частью работы, а не автоматическим blocker.

## 5. Bootstrap

Используйте реализацию и manifest репозитория. Настоящие integrity gates сохраняются полностью:

```sh
python3 scripts/routerkit.py bootstrap
python3 scripts/routerkit.py bootstrap --apply --yes
```

Нельзя обходить:

- architecture validation;
- literal `/opt` validation;
- trusted `/opt`-scoped `opkg`;
- HTTPS/TLS/destination checks;
- repository-pinned Xray artifact;
- SHA-256 архива;
- проверку semantic Xray release;
- safe extraction;
- transactional replacement/rollback.

Узкое отличие формата вывода не является разрешением отключить проверку. Если invariant доказан, внесите минимальный строгий compatibility fix, добавьте regression test, запустите targeted tests и продолжайте уже разрешённую установку.

Подтверждённые live-варианты:

- Entware `Status: install ok installed`;
- Entware `Status: install user installed`;
- точная pinned semantic version Xray с официальным banner/build metadata после неё.

## 6. Profile source

Используйте только защищённые способы ввода:

- hidden interactive input;
- owner-only protected file;
- выделенную переменную `ROUTERKIT_*`.

Raw subscription/VLESS secret никогда не должен попадать в argv, отчёт, Git или обычный chat log.

HTTPS shortlink — допустимый источник. Если shortlink возвращает HTML landing page вместо raw/Base64 VLESS, не заставляйте оператора вручную собирать прямой `vless://`. Используйте штатное защищённое действие subscription/browser flow для получения реального source и снова передайте его в защищённый RouterKit resolver.

HTTPS/TLS/SSRF protections при этом не отключаются.

## 7. Setup, install, healthcheck, autostart

Обычный интегрированный путь:

```sh
python3 scripts/routerkit.py setup --apply --enable-autostart
```

Используйте точные актуальные CLI options выбранной версии и source mode. Логическая последовательность:

```text
source
-> selection
-> generation
-> strict plan
-> preflight
-> backup
-> install
-> healthcheck
-> autostart enable
```

Не вставляйте дополнительные аудиты между успешно завершёнными стадиями.

Listeners должны оставаться только на loopback:

```text
127.0.0.1:1082  primary
127.0.0.1:1083  fallback-1, если настроен
127.0.0.1:1084  fallback-2, если настроен
```

Нельзя открывать их на `0.0.0.0`, LAN или WAN.

## 8. Controlled reboot и after-reboot proof

Если оператор разрешил reboot, используйте **один** controlled reboot для проверки реальной цепочки, а не серию экспериментальных restart.

До reboot сохраните только минимальные recovery facts.

После reboot подтвердите:

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
expected 127.0.0.1 listeners=present and owned by Xray
```

Если вспомогательный verifier падает, а независимые bounded checks доказывают эти exact invariants, отдельно зафиксируйте tooling defect. Нельзя переустанавливать исправный сервис только из-за известного ограничения диагностического helper.

## 9. VPN для конкретных устройств

Само наличие RouterKit listeners не означает, что клиентский трафик использует VPN.

Нужна штатная цепочка:

```text
Xray loopback SOCKS
-> native Proxy interface
-> native Internet access policy
-> selected registered client
```

Для трёх профилей обычно:

```text
Proxy/profile A -> 127.0.0.1:1082
Proxy/profile B -> 127.0.0.1:1083
Proxy/profile C -> 127.0.0.1:1084
```

Создайте отдельную native policy для каждого профиля и назначайте только явно выбранные оператором устройства.

Не используйте TPROXY, REDIRECT, xkeen, iptables marking или ручное редактирование config вместо штатных Proxy/policy объектов.

Не выбранные клиенты должны по умолчанию сохранять прямой PPPoE, если оператор явно не запросил другое поведение.

Если оператор явно разрешил добавить Proxy как fallback в Default, так и укажите в отчёте. Нельзя после этого писать `DEFAULT_POLICY_UNCHANGED=TRUE`.

## 10. Узкие условия остановки

Остановиться и привлечь оператора нужно только если:

- destructive target неоднозначен;
- требуется secret или выбор профиля/устройства;
- pinned artifact/checksum/architecture/semantic version действительно не совпадают;
- `/opt`, Entware, PPPoE, LAN или RMM недоступны и следующая запись может ухудшить recovery;
- hardware behavior противоречит известному live contract и безопасная следующая команда не определена;
- следующая операция выходит за рамки выданного оператором разрешения.

**Не** останавливайтесь только потому что:

- валидное состояние использует уже известный эквивалентный status/banner;
- CI ещё идёт после узкого compatibility fix, targeted tests PASS и оператор не требовал CI;
- ноутбук оператора ушёл из локальной сети, но RMM остаётся рабочим transport;
- secondary verifier даёт false-negative, а independent evidence доказывает service health;
- следующая стадия уже входит в исходную цель установки.

## 11. Классификация результата

Разделяйте состояние сервиса и дефекты инструмента.

Пример:

```text
INSTALLATION_RESULT=PASS
VPN_SERVICE=PASS
CLIENT_ROUTING=PASS
AFTER_REBOOT=PASS
TOOLING_ISSUES=#...
SSH_HARDENING=FOLLOW_UP
```

Нельзя маркировать исправный production VPN как `PARTIAL` только потому, что один вспомогательный verifier имеет известный false-negative.

## 12. Подтверждённый baseline NC-3812

Live-установка 2026-09-09 подтвердила рабочий путь для **Netcraze Hopper SE NC-3812**, NetcrazeOS **5.1.5 / 5.01.C.5.0-0**:

- `aarch64`;
- внешний EXT4 USB -> `/opt`;
- Entware/OPKG;
- pinned Xray 26.3.27 с SHA-256 verification;
- RouterKit setup и healthcheck;
- восстановление autostart после controlled reboot;
- три loopback SOCKS listener;
- native Proxy client interfaces и native policies;
- selected-client routing через профиль RouterKit;
- штатный PPPoE/LAN/Wi-Fi/RMM остались рабочими.

Для того же model/firmware contract считайте эти факты установленным evidence, пока новая live-информация им не противоречит.

## 13. После окна обслуживания

Не начинайте рефакторинг проекта посреди успешного production run, если конкретный defect не блокирует выполнение.

После достижения service goal:

1. сохраните sanitized factual installation record;
2. превратите повторяемые проблемы в code + regression tests, agent rules, concise docs или tracked issue;
3. не публикуйте production secrets, MAC inventories, startup configs и subscription material;
4. сокращайте следующую установку вместо добавления ещё одного слоя approval/audit ceremony.
