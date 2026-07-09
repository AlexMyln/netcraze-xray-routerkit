# netcraze-xray-routerkit

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![CI](https://github.com/AlexMyln/netcraze-xray-routerkit/actions/workflows/ci.yml/badge.svg)](https://github.com/AlexMyln/netcraze-xray-routerkit/actions/workflows/ci.yml)
[![Shell](https://img.shields.io/badge/Shell-POSIX%20sh-4EAA25.svg)](scripts)
[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB.svg)](scripts/generate-xray-profiles.py)

Безопасный публичный стартовый набор для запуска клиентских профилей Xray VLESS/Reality на роутерах семейства Netcraze/Keenetic с USB-накопителем, Entware/OPKG, локальными SOCKS-портами и политиками подключений в веб-интерфейсе.

English: [README.md](README.md)

Changelog: [CHANGELOG.md](CHANGELOG.md)

Документация guided installer: [docs/guided-installer.ru.md](docs/guided-installer.ru.md)

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
sh scripts/install-xray-direct.sh generated
```

7. Запустить Xray напрямую:

```sh
sh /opt/etc/init.d/S23xray-direct start
```

8. Выполнить read-only healthcheck:

```sh
sh scripts/healthcheck.sh
```

9. Вручную создать proxy connections и политики подключений в веб-интерфейсе Netcraze/Keenetic.

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
scripts/generate-xray-profiles.py  Генерация 03/04/05 фрагментов конфига Xray
scripts/routerkit-wizard.py        Интерактивный локальный wizard для profiles.json
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
- Добавить dry-run режим для планирования установки.
- Добавить предпросмотр конфигов с замаскированными секретами.
- Добавить примеры чеклистов для имён в Web UI.
- Добавить полный гайд с нуля: USB → Entware → Xray → Web UI.
- Добавить скриншоты без IP/MAC/секретов.
- Добавить shellcheck после фиксации матрицы совместимости для Entware shell.
- Держать CI-правила для секретов строгими.

## Лицензия

MIT.
