# Основа guided installer

Это v0.2-alpha foundation для будущего guided one-click installer. На этом шаге добавлены безопасный локальный wizard и read-only preflight для роутера без автоматизации веб-интерфейса Netcraze и без изменений runtime-состояния роутера.

## Prerequisites

Перед этим flow роутер нужно подготовить вручную:

- Entware установлен на USB-накопитель;
- SSH-доступ к Entware shell работает;
- бинарник Xray доступен по пути `/opt/sbin/xray`;
- `/opt` и `/opt/etc` доступны на роутере.

## Что делает wizard

`scripts/routerkit-wizard.py` помогает создать локальный `profiles.json` без ручного редактирования JSON.

Он умеет:

- спрашивать имена профилей и локальные SOCKS-порты;
- принимать источник подписки как hidden URL, имя переменной окружения или путь к локальному файлу;
- настраивать выбор узла: первый подходящий узел, name contains, host contains или index;
- записывать локальный ignored `profiles.json`;
- по желанию запускать `python3 scripts/generate-xray-profiles.py --profiles profiles.json --out generated`.

Wizard использует только Python standard library и подавляет вывод generator при optional generation, чтобы детали подписки не печатались обратно в терминал.

## Что wizard не делает

Wizard не:

- подключается к роутеру;
- копирует файлы на роутер;
- меняет `/opt`;
- устанавливает или запускает Xray;
- выполняет Docker, database, server или production actions;
- автоматизирует Netcraze Web UI;
- создаёт TPROXY, REDIRECT или firewall automation.

## Read-only preflight

`scripts/preflight.sh` предназначен для запуска на Entware/Linux роутере перед установкой. Он проверяет prerequisites и печатает человекочитаемый отчёт.

Он проверяет:

- Linux OS;
- `/opt`, `/opt/etc`, `/opt/sbin/xray` и `/opt/etc/xray/configs`;
- базовые команды `sh`, `curl` и `tar`;
- optional `jq`;
- известные init scripts Xray;
- не открыты ли целевые локальные SOCKS-порты на `0.0.0.0`;
- firewall markers, связанные с xkeen, TPROXY и портами routerkit.

Preflight read-only: он не создаёт файлы, не меняет permissions, не запускает и не останавливает Xray, не вызывает xkeen start command.

## Пример flow

1. Запустить wizard локально:

```sh
python3 scripts/routerkit-wizard.py
```

2. Сгенерировать локальные config fragments:

```sh
python3 scripts/generate-xray-profiles.py --profiles profiles.json --out generated
```

3. Скопировать generated config fragments на роутер через ваш приватный способ передачи.
4. Запустить install script на роутере после review generated files.
5. Запустить healthcheck.
6. Вручную создать Netcraze Web UI proxy connections и policies.

## Security notes

- Не храните secrets в git.
- `profiles.json` ignored.
- `generated/` ignored.
- Не вставляйте реальные generated configs в public issues.
- Не вставляйте реальные subscription URLs, VLESS links, UUID, Reality public keys, short IDs, spiderX values, IP addresses, MAC addresses или hostnames в public issues.
