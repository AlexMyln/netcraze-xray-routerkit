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

> Это не образ флешки и не автоматический установщик. Entware/OPKG и Xray должны быть подготовлены отдельно.

v0.2-alpha foundation для guided installer уже в работе. Новый read-only preflight и локальный wizard для `profiles.json` описаны в [документации guided installer](docs/guided-installer.ru.md).

### Единый CLI

```sh
python3 scripts/routerkit.py setup
python3 scripts/routerkit.py setup --apply
python3 scripts/routerkit.py setup --apply --yes
```

Первый bootstrap slice доступен как read-only planner окружения и pinned artifact:

```sh
python3 scripts/routerkit.py bootstrap
python3 scripts/routerkit.py bootstrap --json
python3 scripts/routerkit.py bootstrap --inventory-file tests/fixtures/bootstrap/supported-aarch64.json --dry-run
```

`bootstrap` строго проверяет manifest репозитория, поддерживает только Linux `aarch64`/`arm64` и показывает prerequisites/состояние Xray. Обычный запуск и `--dry-run` одинаково read-only. Команда не активирует Entware, не устанавливает packages, не скачивает и не заменяет Xray, не меняет `/opt`, services/autostart, firewall или policies. Подробнее: [ADR](docs/architecture/bootstrap-execution-model.ru.md) и [evidence для pin](docs/xray-artifact-pin.ru.md).

Planner фиксирует явные соответствия команд пакетам Entware; в частности, `sha256sum` планируется через `coreutils-sha256sum`, а `ca-bundle` остаётся базовым требованием. Имена пакетов относятся к документированному начальному Entware-окружению arm64/aarch64 и всё ещё требуют hardware validation. Планирование остаётся read-only, а установка пакетов — более поздним slice #13.

`setup` — первый implementation slice дорожной карты one-command installer. Команда объединяет существующие стадии wizard, локальной генерации, strict plan, явного apply confirmation, preflight, backup, install и healthcheck. Без `--apply` она останавливается после локальной генерации и успешного strict plan. С `--apply` она запрашивает подтверждение, если не передан `--yes`; `--yes` пропускает только prompt, но не safety stages.

Unified setup перехватывает и подавляет вывод generator, потому что он может содержать данные, производные от подписки или учётных данных; standalone generation сохраняет прежнее диагностическое поведение.

Это milestone, а не финальная реализация [epic #5](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/5). Read-only planner/manifest закрывает [#18](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/18), а bootstrap apply остаётся в [#13](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/13). Autostart — #14, Netcraze proxy/policy — #15, hardware validation всё ещё заблокирован #16. `setup` пока не вызывает `bootstrap`.

### Безопасный выбор источника профилей

Команда profile-source принимает значение через скрытый ввод, указанную переменную окружения или защищённый локальный текстовый файл. Она разбирает одну raw VLESS-ссылку, newline subscription, Base64 subscription text, строки во вложенном JSON и Base64-encoded JSON. Тот же offline parser теперь может получить прямую HTTPS subscription или HTTPS shortlink со стандартными HTTP redirects:

```sh
python3 scripts/routerkit.py profile-source
python3 scripts/routerkit.py profile-source --source-env ROUTERKIT_PROFILE_SOURCE
python3 scripts/routerkit.py profile-source --source-file /private/path/payload.txt --list
python3 scripts/routerkit.py profile-source --source-file /private/path/payload.txt --primary-index 1 --fallback-index 2
```

Можно выбрать только VLESS Reality nodes с TCP (включая нормализованный Xray alias `raw`), структурно допустимым Reality public key, валидным optional hexadecimal short ID и без flow либо с `xtls-rprx-vision`. Summary не содержит ссылки, identifiers, host, SNI, Reality keys, short IDs или spider paths. `--source-file` отклоняет symlink и не-regular files; на POSIX права должны быть только для владельца, например `0400` или `0600`, и tool никогда не меняет их автоматически. Неподдерживаемые URI schemes отклоняются без вывода source. Выбор включает ровно один primary и до двух fallback на детерминированных портах `1082`, `1083` и `1084`. Итоговый `profiles.json` атомарно публикуется с mode `0600` на POSIX и без явного `--force` не перезаписывает файл, появившийся во время публикации; даже `--force` отклоняет symlink и не-regular destination. Файл содержит secrets, поэтому его нельзя коммитить или публиковать.

Network acquisition принимает только HTTPS на port 443, без URL userinfo и fragments. Каждый redirect отдельно проходит URL validation и DNS resolution; все адреса в ответе должны быть globally routable, TCP connection закрепляется за validated address, TLS продолжает проверять original hostname, а connected peer сверяется. Лимиты: 5 redirects, 16 DNS addresses на hop, 5 секунд на DNS hop, 10 секунд на address connection, 30 секунд overall, 8192 bytes на URL/redirect value и 1 MiB на response. Compressed responses, JavaScript redirects и HTML meta refresh не поддерживаются. `profile-source --dry-run` может выполнить network read и parsing, но не записывает `profiles.json`. Существующие поля generator `subscription_url` и `subscription_url_env` используют тот же resolver. Подробнее: [ADR безопасности сети](docs/architecture/profile-source-network-security.ru.md).

Автоматическая default-интеграция с `setup` остаётся в [#24](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/24), parent [#20](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/20) остаётся открытым, обычное поведение `routerkit setup` не изменено.

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

`install --apply` не автоматизирует политики Web UI, не вызывает `xkeen -start`, не трогает firewall и не включает autostart.

Для advanced/debug usage доступны skip flags: `--skip-preflight`, `--skip-backup` и `--skip-healthcheck`. Они не recommended; default apply flow выполняет все safety steps. Если пропустить backup, rollback может быть сложнее.

Посмотреть apply pipeline без запуска:

```sh
python3 scripts/routerkit.py --dry-run install --generated generated --apply
```

Флаг `--enable-autostart` зарезервирован для отдельного будущего flow. Autostart остаётся ручным шагом после healthcheck.

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
docs/guided-installer.md           Guided installer foundation
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
- [Guided installer foundation](docs/guided-installer.md)
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

- Двигаться к guided one-click installer после того, как Entware/OPKG и Xray уже подготовлены: генерировать профили, устанавливать конфиги, устанавливать `S23xray-direct`, запускать healthchecks и печатать точные шаги для Netcraze Web UI. Установщик предполагает, что Entware, SSH и Xray уже доступны.
- Оставить from-zero путь ручным для USB-накопителя, установки Entware/Xray и решений в Netcraze Web UI по устройствам/политикам; готовый образ флешки или роутера не обещается.
- Расширить dry-run план установки optional masked previews.
- Добавить предпросмотр конфигов с замаскированными секретами.
- Добавить примеры чеклистов для имён в Web UI.
- Добавить полный гайд с нуля: USB → Entware → Xray → Web UI.
- Добавить скриншоты без IP/MAC/секретов.
- Добавить shellcheck после фиксации матрицы совместимости для Entware shell.
- Держать CI-правила для секретов строгими.

## Лицензия

MIT.
