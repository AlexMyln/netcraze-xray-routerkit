# netcraze-xray-routerkit

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![CI](https://github.com/AlexMyln/netcraze-xray-routerkit/actions/workflows/ci.yml/badge.svg)](https://github.com/AlexMyln/netcraze-xray-routerkit/actions/workflows/ci.yml)
[![Shell](https://img.shields.io/badge/Shell-POSIX%20sh-4EAA25.svg)](scripts)
[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB.svg)](scripts/generate-xray-profiles.py)

Безопасный публичный стартовый набор для запуска клиентских профилей Xray VLESS/Reality на роутерах семейства Netcraze/Keenetic с USB-накопителем, Entware/OPKG, локальными SOCKS-портами и политиками подключений в веб-интерфейсе.

English: [README.md](README.md)

Changelog: [CHANGELOG.md](CHANGELOG.md)

Документация guided installer: [docs/guided-installer.ru.md](docs/guided-installer.ru.md)

Bootstrap design: [ADR модели выполнения](docs/architecture/bootstrap-execution-model.ru.md) · [проверка Xray pin](docs/xray-artifact-pin.ru.md)

## Медиа репозитория

- Asset для GitHub Social preview: [assets/social-preview.png](assets/social-preview.png)

## Архитектура

```text
+-----------------------------+
| Entware на USB-накопителе   |
+-------------+---------------+
              |
              v
+-----------------------------+
| Прямой init-скрипт Xray     |
| без xkeen firewall-обвязки  |
+-------------+---------------+
              |
              v
+-----------------------------+
| Локальные SOCKS-порты       |
| 127.0.0.1:1082 / 1083 / ... |
+-------------+---------------+
              |
              v
+-----------------------------+
| Proxy connections Netcraze  |
+-------------+---------------+
              |
              v
+-----------------------------+
| Политики для устройств      |
| только выбранные клиенты    |
+-----------------------------+
```

## Зачем это нужно

Настройки proxy/VLESS на роутере часто превращаются в сложную смесь firewall-правил, transparent proxy, TPROXY, широких политик по умолчанию и секретов, разбросанных по файловой системе.

Этот набор держит модель маленькой и проверяемой:

- генерирует фрагменты конфига Xray из локального `profiles.json`, который не попадает в git;
- запускает Xray напрямую из Entware;
- привязывает локальные SOCKS-порты только к `127.0.0.1`;
- переключает только выбранные устройства через политики подключений в веб-интерфейсе;
- держит публичный репозиторий без секретов.

## Безопасность по умолчанию

- Xray слушает только loopback.
- Публичный SOCKS-порт не создаётся.
- Прямой init-скрипт не вызывает `xkeen -start`.
- Проект не устанавливает TPROXY, REDIRECT или transparent firewall mode.
- Политика роутера по умолчанию не меняется.
- Сгенерированные конфиги, локальные файлы профилей, резервные копии роутера и архивы игнорируются через `.gitignore`.
- CI включает проверки синтаксиса и защиту от случайной публикации секретов.

## Быстрый старт

> Это не образ флешки и не автоматический установщик. Entware/OPKG нужно подготовить отдельно; standalone bootstrap apply поддерживает как замену существующего Xray, так и clean install.

Guided setup версии v0.2-alpha теперь объединяет profile-source acquisition, приватную генерацию, strict planning и явно подтверждённые apply stages. Flow описан в [документации guided installer](docs/guided-installer.ru.md).

### Единый CLI

```sh
python3 scripts/routerkit.py setup
python3 scripts/routerkit.py setup --apply
python3 scripts/routerkit.py setup --apply --bootstrap-apply
python3 scripts/routerkit.py setup --apply --yes
```

Standalone bootstrap по умолчанию остаётся read-only и теперь имеет отдельно gated transactional apply:

```sh
python3 scripts/routerkit.py bootstrap
python3 scripts/routerkit.py bootstrap --dry-run
python3 scripts/routerkit.py bootstrap --apply
python3 scripts/routerkit.py bootstrap --apply --yes
python3 scripts/routerkit.py bootstrap --apply --dry-run
```

Обычный запуск и standalone `--dry-run` строго проверяют выбранный manifest, поддерживают только Linux `aarch64`/`arm64` и остаются read-only: без package command, network, staging или записи. `--apply` требует свежий live inventory, буквальный `/opt`, фиксированный `/opt`-scoped `opkg` и подтверждение, если не передан `--yes`. `--apply --dry-run` показывает абстрактный no-write план транзакции без prompt. Manifest репозитория используется по умолчанию; standalone `--manifest` — явный operator-controlled trust input, и выбранный manifest обязан пройти те же structural, repository/URL, checksum и version gates.

Apply проверяет фиксированный набор `ca-bundle`, `curl`, `unzip`, `coreutils-sha256sum` и `python3`, затем запрашивает только отсутствующие top-level имена в детерминированном порядке. RouterKit фиксирует top-level `opkg` verb и package arguments, но trusted dependencies и maintainer scripts остаются в области полномочий `opkg`. Package install аддитивен: additions могут остаться после частично неуспешного `opkg install` или более позднего Xray-этапа, потому что автоматическое удаление dependencies небезопасно. Точный manifest-pinned archive загружается через bounded proxy-free HTTPS, проверяется SHA-256 и безопасно извлекается только один кандидат `xray`. Для существующего binary создаётся проверенная hash-addressed backup в `/opt/var/lib/routerkit/backups/`; замена выполняется атомарно на той же filesystem, проверяется после установки и автоматически откатывается при сбое. Restrictive provenance receipt разрешает полный no-op повтор только при совпадении release, archive hash, installed hash и точной версии.

Bootstrap apply не активирует Entware, не перезапускает services, не включает autostart, не загружает configs, не вызывает `xkeen -start` и не меняет Web UI, firewall, proxy или policy. Ctrl-C на confirmation prompt отменяет запуск до package, network, staging или write action. Внутри mutable transaction `SIGINT` координируется вместе с `SIGTERM`/`SIGHUP`: до replacement он останавливает forward progress и очищает staging; после replacement проверяет восстановление backup или удаление clean-install candidate до обычного exit `130`. Повторные catchable signals откладываются до завершения recovery и cleanup, а итоговый signal exit определяется первым сигналом. Неподтверждённое recovery возвращает отдельный exit `3` с указанием сохранённого backup, а не обычный signal result. `SIGKILL`, потеря питания, сбой kernel и crash хоста остаются residual risks. Profile inputs и generated secrets не относятся к bootstrap и не читаются им. Manual Entware activation всё ещё обязательна, а #16 должен завершиться до заявления о hardware-tested статусе. Подробнее: [ADR](docs/architecture/bootstrap-execution-model.ru.md) и [evidence для pin](docs/xray-artifact-pin.ru.md).

По умолчанию `setup` использует завершённый стек profile-source, private workspace и strict plan. Обычный `setup` останавливается после plan: без bootstrap и router apply. `setup --apply` сохраняет существующий подтверждаемый flow preflight → backup → install → healthcheck и не запускает bootstrap. Только `setup --apply --bootstrap-apply` добавляет reviewed standalone bootstrap transaction после strict plan и единственного видимого setup confirmation, но до preflight; внутренний `--yes` исключает второй prompt. Сбой bootstrap, cancellation, любой catchable signal, замеченный setup bootstrap supervisor, или внутренний bootstrap-supervision failure не запускает последующие router stages. `setup --apply --enable-autostart` добавляет explicit autostart transaction после healthcheck и использует тот же transactional child supervisor. Добавленные packages могут остаться, а Xray replacement и autostart имеют отдельные проверенные rollback boundaries. Reboot proof, service management вне reviewed init script, Web UI, firewall или policy action не выполняются.

Read-only device discovery реализован fixture-first. `routerkit devices status` показывает live Netcraze/Keenetic adapter как `contract_unverified`, пока hardware probe не подтвердит exact command/API contract для target firmware. Offline validation использует protected synthetic inventory: `routerkit devices discover --inventory-file PATH` или `routerkit devices select --inventory-file PATH`; JSON помечает local-sensitive names, addresses, stable identifiers, source names и raw errors, а `--public-evidence` разрешён только для discover JSON и использует schema-controlled source categories плюс counted generic error codes. Nonzero selection fail-closed, если не все required sources имеют `supported`, есть sanitized errors или выбранный device не имеет trusted assignment-stable identity. `setup --discover-devices --device-inventory-file PATH` добавляет тот же read-only selection stage после strict planning и до apply confirmation. Option `0`, blank input и EOF означают no device assignment. В #21 нет policy write, proxy write, default-policy change, active scan, persisted inventory, persisted selection handle или device assignment; #15 остаётся write boundary. См. [архитектуру](docs/architecture/device-discovery.ru.md), [исследование интерфейса](docs/research/device-discovery-interface.ru.md) и [read-only hardware probe packet](docs/hardware/device-discovery-probe.ru.md).

Пока существует private workspace, перехватываемые `SIGTERM` и `SIGHUP` запускают согласованную остановку process group source/generator, reaping дочернего процесса и cleanup workspace до выхода setup. `SIGINT` сохраняет обычное поведение интерактивной отмены. `SIGKILL`, потеря питания, сбой kernel и crash хоста не могут выполнить in-process cleanup и способны оставить owner-only workspace для ручного удаления.

В setup параметр `--source-env` принимает только валидное имя выделенной переменной `ROUTERKIT_*`. Raw value не попадает в argv или output, доступно только дочернему процессу profile-source acquisition и удаляется там до классификации URL, создания DNS resolver worker, parsing или selection. Generator, strict-plan, integrated bootstrap, preflight, backup, install и healthcheck получают копию обычного environment без одной выбранной переменной. Standalone `profile-source --source-env` сохраняет прежнюю совместимость с произвольными валидными именами environment variables, если внутренний consume option, используемый setup, не указан явно.

Неинтерактивный выбор не помещает raw source в argv:

```sh
ROUTERKIT_PROFILE_SOURCE='...' \
python3 scripts/routerkit.py setup --source-env ROUTERKIT_PROFILE_SOURCE --primary-index 1 --fallback-index 2
python3 scripts/routerkit.py setup --source-file /protected/path/source.txt --primary-index 1
```

Reuse готовых профилей и legacy wizard доступны только как явные advanced modes:

```sh
python3 scripts/routerkit.py setup --reuse-profiles /protected/path/profiles.json
python3 scripts/routerkit.py setup --legacy-wizard
```

Старые варианты `--profiles` и `--force-wizard` остаются deprecated aliases для этих явных режимов. Setup больше не обнаруживает и не переиспользует `./profiles.json` случайно. Reuse отклоняет symlink, не-regular file, не-owner-only POSIX permissions, слишком большой или не-UTF-8 content и изменения identity между path и descriptor; проверенный input копируется в setup workspace, а оригинал не изменяется и не передаётся generator.

`setup --dry-run`, включая `setup --apply --bootstrap-apply --dry-run`, абстрактный и secret-free: он не читает source, reuse file, secret input или значение environment, не выполняет stdin prompt, DNS или HTTPS request, subprocess, создание private workspace, file write, package/staging/Xray action или router action. Загрузка Python modules и определение repository path не входят в этот secret-input contract. Это намеренно строже standalone `profile-source --dry-run`, который может получить HTTPS source, но не записывает profiles.

Этот milestone функционально завершает setup integration #29 и parent #13, но не [epic #5](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/5). Bootstrap остаётся явным; read-only fixture-first device discovery — software часть #21 с pending hardware contract confirmation; Netcraze proxy/policy — #15, hardware validation — #16. Integrated path не считается hardware-tested до завершения #16.

### Безопасный выбор источника профилей

Команда profile-source принимает значение через скрытый ввод, указанную переменную окружения или защищённый локальный текстовый файл. Она разбирает одну raw VLESS-ссылку, newline subscription, Base64 subscription text, строки во вложенном JSON и Base64-encoded JSON. Тот же offline parser теперь может получить прямую HTTPS subscription или HTTPS shortlink со стандартными HTTP redirects:

```sh
python3 scripts/routerkit.py profile-source
python3 scripts/routerkit.py profile-source --source-env ROUTERKIT_PROFILE_SOURCE
python3 scripts/routerkit.py profile-source --source-file /private/path/payload.txt --list
python3 scripts/routerkit.py profile-source --source-file /private/path/payload.txt --primary-index 1 --fallback-index 2
```

Можно выбрать только VLESS Reality nodes с TCP (включая нормализованный Xray alias `raw`), структурно допустимым Reality public key, валидным optional hexadecimal short ID и без flow либо с `xtls-rprx-vision`. Summary не содержит ссылки, identifiers, host, SNI, Reality keys, short IDs или spider paths. `profile-source --source-file` отклоняет symlink и не-regular files; на POSIX права должны быть только для владельца, например `0400` или `0600`, и tool никогда не меняет их автоматически. Legacy-поле generator `subscription_file` остаётся расширенным compatibility/debug path и не применяет ту же policy для permissions и symlink; для секретного локального payload следует предпочесть `profile-source --source-file`. Неподдерживаемые URI schemes отклоняются без вывода source. Выбор включает ровно один primary и до двух fallback на детерминированных портах `1082`, `1083` и `1084`. Итоговый `profiles.json` атомарно публикуется с mode `0600` на POSIX и без явного `--force` не перезаписывает файл, появившийся во время публикации; даже `--force` отклоняет symlink и не-regular destination. Файл содержит secrets, поэтому его нельзя коммитить или публиковать.

Network acquisition принимает только HTTPS на port 443, без URL userinfo и fragments. Outer whitespace вокруг одного полного HTTPS source одинаково удаляется для hidden input, environment, защищённого file и generator, поэтому окончания LF/CRLF работают; internal whitespace, control characters, multiple lines и empty values отклоняются, а raw/offline payload не меняется. Каждый HTTP `Location` redirect отдельно проходит URL validation и DNS resolution; все адреса в ответе должны пройти fixed reviewed special-purpose CIDR tables и дополнительные defense-in-depth проверки standard-library `ipaddress`, TCP connection закрепляется за validated address, TLS продолжает проверять original hostname, а connected peer сверяется. IPv4-mapped IPv6, стандартизованные NAT64, Teredo, 6to4 и ORCHID ranges консервативно запрещены. Обычная cancellation прекращает retries и redirects, после чего выполняются ограниченные best-effort попытки cleanup ресурсов. Лимиты: 5 redirects, 16 DNS addresses на hop, 5 секунд на DNS hop, 10 секунд на address connection, 30-секундный operational deadline плюс bounded cleanup grace, 8192 bytes на URL/redirect value и 1 MiB на response. Dedicated compatibility job на Python 3.8.18 и primary `3.x` выполняет destination/address-policy test class; полный suite запускается на основном CI Python. Compressed responses отклоняются. JavaScript не выполняется, а HTML meta refresh не интерпретируется, поэтому эти browser-style механизмы навигации не поддерживаются и не выполняются; финальный HTTP 200 body вместо этого передаётся offline parser. `profile-source --dry-run` может выполнить network read и parsing, но не записывает `profiles.json`. Существующие поля generator `subscription_url` и `subscription_url_env` используют тот же resolver. Подробнее: [ADR безопасности сети](docs/architecture/profile-source-network-security.ru.md).

Default profile-source setup integration завершила [#24](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/24) и parent [#20](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/20). У standalone bootstrap transaction теперь также есть явный setup gate; live device discovery hardware confirmation, policies и hardware validation остаются отдельной работой.

Отдельные команды также доступны:

```sh
python3 scripts/routerkit.py wizard
python3 scripts/routerkit.py generate --profiles profiles.json --out generated
python3 scripts/routerkit.py plan --generated generated
```

Проверки на стороне роутера:

```sh
python3 scripts/routerkit.py preflight
python3 scripts/routerkit.py healthcheck
```

Флаг `--dry-run` показывает, какую команду wrapper запустил бы.

1. Установить Entware/OPKG на EXT4 USB-накопитель.
2. Установить бинарник Xray на роутер.
3. Скопировать `examples/profiles.example.json` в локальный файл `profiles.json`, который не попадает в git.
4. Положить URL подписок в переменные окружения или локальные файлы, а не в git.
5. Сгенерировать фрагменты конфига Xray:

```sh
python3 scripts/generate-xray-profiles.py \
  --profiles profiles.json \
  --out generated
```

6. Установить сгенерированные конфиги и прямой init-скрипт на роутере:

```sh
python3 scripts/routerkit.py install --generated generated --apply
```

7. Запустить Xray напрямую:

```sh
sh /opt/etc/init.d/S23xray-direct start
```

8. Ещё раз выполнить read-only healthcheck после ручного start:

```sh
sh scripts/healthcheck.sh
```

9. Вручную создать proxy connections и политики подключений в веб-интерфейсе Netcraze/Keenetic.

### Install plan / dry-run

Посмотреть, что guided installer сделал бы, без изменений в `/opt`:

```sh
python3 scripts/routerkit-plan.py --generated generated
```

План скрывает secret-bearing поля outbounds и не вызывает `xkeen -start`, не трогает firewall, не включает autostart и не меняет политики Web UI.

### Команда install

Режим плана без изменений:

```sh
python3 scripts/routerkit.py install --generated generated
```

Реальная установка:

```sh
python3 scripts/routerkit.py install --generated generated --apply
```

### Усиленный apply flow

`install --apply` выполняет safety-steps вокруг установки:

```sh
python3 scripts/routerkit.py install --generated generated --apply
```

Pipeline по умолчанию:

1. строгий install plan;
2. router preflight;
3. backup;
4. установка generated configs и S23xray-direct;
5. healthcheck.

Backup-архивы могут содержать секретные файлы роутера. Не публикуйте backup archives.

`install --apply` не автоматизирует политики Web UI, не вызывает `xkeen -start`, не трогает firewall и не включает autostart, если явно не указан `--enable-autostart`.

Для advanced/debug usage доступны skip flags: `--skip-preflight`, `--skip-backup` и `--skip-healthcheck`. Они не recommended; default apply flow выполняет все safety steps. Если пропустить backup, rollback может быть сложнее.

Посмотреть apply pipeline без запуска:

```sh
python3 scripts/routerkit.py --dry-run install --generated generated --apply
```

Включить autostart после healthcheck через проверенную transaction:

```sh
python3 scripts/routerkit.py install --generated generated --apply --enable-autostart
```

Autostart stage запускается только после healthcheck и сохраняет standalone confirmation prompt. Он выключает `S24xray`, при необходимости перезапускает runtime через проверенный `S23xray-direct`, проверяет стабильный process epoch и ownership loopback listeners, а затем включает только `S23xray-direct`. Если autostart уже включён и runtime verified, команда сообщает no-op и не утверждает restart verification. Disable выполняется явно и не останавливает running process:

```sh
python3 scripts/routerkit.py autostart --verify
python3 scripts/routerkit.py autostart --enable --apply
python3 scripts/routerkit.py autostart --disable --apply
```

Reboot не выполняется и не доказывается. После реальной перезагрузки роутера запустите read-only `autostart --verify`. Hardware/reboot validation остаётся #16; device discovery #21, Netcraze policy automation #15 и epic #5 остаются открытыми. Подробнее: [модель выполнения autostart](docs/architecture/autostart-execution-model.ru.md).

### Тесты

Локальный запуск тестов:

```sh
python3 -m unittest discover -s tests -v
```

## Пример топологии

```text
Локальные SOCKS-порты Xray:
  127.0.0.1:1082 -> PROFILE-A
  127.0.0.1:1083 -> PROFILE-B
  127.0.0.1:1084 -> PROFILE-C

Web UI proxy connections:
  XRAY-PROFILE-A -> SOCKS5 127.0.0.1:1082
  XRAY-PROFILE-B -> SOCKS5 127.0.0.1:1083
  XRAY-PROFILE-C -> SOCKS5 127.0.0.1:1084

Политики подключений:
  CLIENT-PROFILE-A -> only XRAY-PROFILE-A
  CLIENT-PROFILE-B -> only XRAY-PROFILE-B
  CLIENT-PROFILE-C -> only XRAY-PROFILE-C
```

## Чем это не является

- Не Docker-образ.
- Не готовый образ флешки.
- Не сервис подписок.
- Не transparent proxy/firewall automation layer.
- Не место для хранения реальных конфигов роутера, сгенерированных конфигов Xray или архивов резервных копий.

## Работа с секретами

Никогда не коммитьте:

- URL подписок;
- VLESS-ссылки;
- UUID из реальных ссылок;
- Reality public keys, short IDs или spiderX values;
- реальные конфиги `/opt/etc/xray`;
- локальные `profiles.json` с URL;
- router startup-config files;
- Entware/Xray backup directories или archives.

Репозиторий специально содержит только пример профиля без секретов. Реальные значения должны жить в локальных файлах вне git, переменных окружения или приватных каналах передачи.

## Проверенная базовая схема

- Роутер семейства Netcraze/Keenetic с Entware/OPKG на USB-накопителе.
- Xray установлен в `/opt/sbin/xray`.
- Директория конфигов Xray: `/opt/etc/xray/configs`.
- POSIX `sh` для роутерных скриптов.
- Python 3.8+ для локальной генерации профилей.
- Локальные SOCKS-порты, например `1082`, `1083`, `1084`.

## Структура репозитория

```text
scripts/routerkit.py                Единый CLI wrapper для routerkit helpers
scripts/generate-xray-profiles.py  Генерация 03/04/05 фрагментов конфига Xray
scripts/routerkit-wizard.py        Интерактивный локальный wizard для profiles.json
scripts/routerkit-plan.py          Dry-run install plan без изменений на роутере
scripts/preflight.sh               Read-only preflight checks для Entware/router
scripts/install-xray-direct.sh     Установка сгенерированных конфигов и init-скрипта
scripts/healthcheck.sh             Проверки без изменений: runtime, порты, firewall и IP
scripts/backup.sh                  Создание локального backup-архива; не публиковать

templates/S23xray-direct           Прямой init-скрипт для Entware
examples/profiles.example.json     Шаблон профиля без секретов
assets/social-preview.png          Картинка для GitHub Social preview
README.ru.md                       Русская версия README

docs/netcraze-ui.md                Web UI guide для proxy/policy
docs/install-from-zero.ru.md       Установка с нуля на русском
docs/guided-installer.md           Guided installer workflow
docs/guided-installer.ru.md        Основа guided installer на русском
docs/installer-scope.md            Installer scope и prerequisites
docs/installer-scope.ru.md         Область работы установщика и prerequisites
docs/netcraze-ui.ru.md             Русская инструкция по Web UI
docs/restore.md                    Restore notes
docs/troubleshooting.md            Troubleshooting
docs/troubleshooting.ru.md         Troubleshooting на русском
docs/friend-instruction.md         End-user switching guide
docs/friend-instruction.ru.md      Русская инструкция для пользователя
docs/announcement.ru.md            Черновик анонса на русском
```

## Документация

- [Changelog](CHANGELOG.md)
- [Guided installer](docs/guided-installer.md)
- [Основа guided installer](docs/guided-installer.ru.md)
- [Область работы установщика](docs/installer-scope.ru.md)
- [Netcraze/Keenetic Web UI guide](docs/netcraze-ui.md)
- [Netcraze/Keenetic Web UI guide — RU](docs/netcraze-ui.ru.md)
- [Install from zero — RU](docs/install-from-zero.ru.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Troubleshooting — RU](docs/troubleshooting.ru.md)
- [Friend instruction](docs/friend-instruction.md)
- [Инструкция для пользователя](docs/friend-instruction.ru.md)
- [Restore notes](docs/restore.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

Для багов и идей используйте GitHub issue templates; примеры и логи должны быть очищены от секретов.

## Roadmap

- Двигаться к guided one-click installer после подготовки поддерживаемого USB-накопителя, официальной активации Entware/OPKG и приватного SSH-доступа: генерировать профили, при явном `--bootstrap-apply` подготавливать pinned Xray runtime, устанавливать конфиги и `S23xray-direct`, запускать healthchecks и печатать точные шаги для Netcraze Web UI. Для явного bootstrap-пути заранее установленный Xray не обязателен.
- Оставить from-zero путь ручным для подготовки USB, официальной активации Entware, SSH-доступа и решений в Netcraze Web UI по устройствам/политикам; RouterKit не предоставляет готовый образ флешки/роутера, а обычные setup-режимы не устанавливают Xray скрытно.
- Расширить dry-run план установки optional masked previews.
- Добавить предпросмотр конфигов с замаскированными секретами.
- Добавить примеры чеклистов для имён в Web UI.
- Добавить полный гайд с нуля: USB → Entware → Xray → Web UI.
- Добавить скриншоты без IP/MAC/секретов.
- Добавить shellcheck после фиксации матрицы совместимости для Entware shell.
- Держать CI-правила для секретов строгими.

## Лицензия

MIT.
