# netcraze-xray-routerkit

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![CI](https://github.com/AlexMyln/netcraze-xray-routerkit/actions/workflows/ci.yml/badge.svg)](https://github.com/AlexMyln/netcraze-xray-routerkit/actions/workflows/ci.yml)
[![Shell](https://img.shields.io/badge/Shell-POSIX%20sh-4EAA25.svg)](scripts)
[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB.svg)](scripts/generate-xray-profiles.py)

Безопасный публичный starter-kit для запуска Xray VLESS/Reality-клиентских профилей на роутерах Netcraze/Keenetic-стиля через USB-накопитель, Entware/OPKG, локальные SOCKS-порты и политики подключений в Web UI.

English: [README.md](README.md)

## Архитектура

```text
+-----------------------------+
| Entware на USB-накопителе   |
+-------------+---------------+
              |
              v
+-----------------------------+
| Прямой init-скрипт Xray     |
| без xkeen firewall wrapper  |
+-------------+---------------+
              |
              v
+-----------------------------+
| localhost SOCKS listeners   |
| 127.0.0.1:1082 / 1083 / ... |
+-------------+---------------+
              |
              v
+-----------------------------+
| Netcraze proxy connections  |
+-------------+---------------+
              |
              v
+-----------------------------+
| Политики для устройств      |
| только выбранные клиенты    |
+-----------------------------+
```

## Зачем это нужно

Настройки proxy/VLESS на роутере часто превращаются в сложную смесь firewall-правил, transparent proxy, TPROXY, широких default policy и секретов, разбросанных по файловой системе.

Этот набор держит модель маленькой и проверяемой:

- генерирует фрагменты Xray config из локального ignored profile-файла;
- запускает Xray напрямую из Entware;
- привязывает SOCKS listeners только к `127.0.0.1`;
- переключает только выбранные устройства через Web UI policies;
- держит публичный репозиторий свободным от секретов.

## Безопасность по умолчанию

- Xray слушает только loopback.
- Публичный SOCKS-порт не создаётся.
- Direct init script не вызывает `xkeen -start`.
- Проект не устанавливает TPROXY, REDIRECT или transparent firewall mode.
- Default policy роутера не меняется.
- Generated configs, local profile files, router backups и archives игнорируются через `.gitignore`.
- CI включает syntax checks и secret guard.

## Быстрый старт

> Это не образ флешки и не one-click installer. Entware/OPKG и Xray должны быть подготовлены отдельно.

1. Установить Entware/OPKG на EXT4 USB-накопитель.
2. Установить Xray binary на роутер.
3. Скопировать `examples/profiles.example.json` в локальный ignored-файл `profiles.json`.
4. Положить subscription URLs в environment variables или локальные файлы, а не в git.
5. Сгенерировать Xray config fragments:

```sh
python3 scripts/generate-xray-profiles.py \
  --profiles profiles.json \
  --out generated
```

6. Установить generated configs и direct init script на роутере:

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

9. Вручную создать proxy connections и connection policies в Web UI Netcraze/Keenetic.

## Пример топологии

```text
Xray local listeners:
  127.0.0.1:1082 -> PROFILE-A
  127.0.0.1:1083 -> PROFILE-B
  127.0.0.1:1084 -> PROFILE-C

Web UI proxy connections:
  XRAY-PROFILE-A -> SOCKS5 127.0.0.1:1082
  XRAY-PROFILE-B -> SOCKS5 127.0.0.1:1083
  XRAY-PROFILE-C -> SOCKS5 127.0.0.1:1084

Connection policies:
  CLIENT-PROFILE-A -> only XRAY-PROFILE-A
  CLIENT-PROFILE-B -> only XRAY-PROFILE-B
  CLIENT-PROFILE-C -> only XRAY-PROFILE-C
```

## Чем это не является

- Не Docker image.
- Не готовый образ флешки.
- Не subscription service.
- Не transparent proxy/firewall automation layer.
- Не место для хранения реальных router configs, generated Xray configs или backup archives.

## Работа с секретами

Никогда не коммитьте:

- subscription URLs;
- VLESS links;
- UUID из реальных ссылок;
- Reality public keys, short IDs или spiderX values;
- реальные `/opt/etc/xray` configs;
- локальные `profiles.json` с URL;
- router startup-config files;
- Entware/Xray backup directories или archives.

Репозиторий специально содержит только secret-free пример профиля. Реальные значения должны жить в локальных ignored-файлах, environment variables или приватных каналах передачи.

## Проверенная базовая схема

- Netcraze/Keenetic-style router с Entware/OPKG на USB storage.
- Xray установлен в `/opt/sbin/xray`.
- Xray config directory: `/opt/etc/xray/configs`.
- POSIX `sh` для router scripts.
- Python 3.8+ для локальной генерации профилей.
- Local SOCKS ports, например `1082`, `1083`, `1084`.

## Структура репозитория

```text
scripts/generate-xray-profiles.py  Генерация 03/04/05 Xray config fragments
scripts/install-xray-direct.sh     Установка generated configs и init script
scripts/healthcheck.sh             Read-only runtime/listener/firewall/IP checks
scripts/backup.sh                  Создание локального backup-архива; не публиковать

templates/S23xray-direct           Direct-run init script для Entware
examples/profiles.example.json     Secret-free profile template

docs/netcraze-ui.md                Web UI proxy/policy guide
docs/netcraze-ui.ru.md             Русская версия Web UI guide
docs/restore.md                    Restore notes
docs/restore.ru.md                 Русская версия restore notes
docs/troubleshooting.md            Troubleshooting
docs/troubleshooting.ru.md         Русская версия troubleshooting
docs/friend-instruction.md         End-user switching guide
docs/friend-instruction.ru.md      Русская инструкция для пользователя
```

## Документация

- [Netcraze/Keenetic Web UI guide](docs/netcraze-ui.md)
- [Netcraze/Keenetic Web UI guide — RU](docs/netcraze-ui.ru.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Troubleshooting — RU](docs/troubleshooting.ru.md)
- [Friend instruction](docs/friend-instruction.md)
- [Инструкция для пользователя](docs/friend-instruction.ru.md)
- [Restore notes](docs/restore.md)
- [Restore notes — RU](docs/restore.ru.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

## Roadmap

- Добавить dry-run mode для install planning.
- Добавить masked config previews.
- Добавить sample Web UI naming checklists.
- Добавить полный “from zero” guide: USB → Entware → Xray → Web UI.
- Добавить screenshots без IP/MAC/секретов.
- Добавить shellcheck после фиксации compatibility matrix для Entware shell.
- Держать CI secret rules строгими.

## Лицензия

MIT.
