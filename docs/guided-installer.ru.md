# Основа guided installer

Это v0.2-alpha foundation для будущего guided one-click installer. На этом шаге добавлены безопасный локальный wizard и read-only preflight для роутера без автоматизации веб-интерфейса Netcraze и без изменений runtime-состояния роутера.

## Prerequisites

Перед этим flow роутер нужно подготовить вручную:

- Entware установлен на USB-накопитель;
- SSH-доступ к Entware shell работает;
- бинарник Xray доступен по пути `/opt/sbin/xray`;
- `/opt` и `/opt/etc` доступны на роутере.

## Единый CLI

`scripts/routerkit.py` — единая Python-точка входа для foundation guided installer. Она только делегирует запуск существующим скриптам и возвращает exit code дочернего процесса.

```sh
python3 scripts/routerkit.py wizard
python3 scripts/routerkit.py generate --profiles profiles.json --out generated
python3 scripts/routerkit.py plan --generated generated
```

### Read-only bootstrap planner

Первый slice [bootstrap #13](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/13) ([#18](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/18)) проверяет prerequisites и repository-owned pinned Xray manifest:

```sh
python3 scripts/routerkit.py bootstrap
python3 scripts/routerkit.py bootstrap --json
python3 scripts/routerkit.py bootstrap --dry-run
```

Начальный scope — только Linux `aarch64`/`arm64`. Обычный режим и `--dry-run` одинаково read-only. Planner не устанавливает Entware/packages, не скачивает и не заменяет Xray, не пишет в `/opt`, не управляет services/autostart, firewall или Netcraze policies. Для tests/development доступны offline inventory files.

Решение описано в [ADR модели выполнения](architecture/bootstrap-execution-model.ru.md), а evidence официального release/checksum и независимого hash — в [проверке Xray pin](xray-artifact-pin.ru.md). Hardware validation остаётся заблокирован [#16](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/16), настоящий one-command [epic #5](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/5) не завершён.

Проверки на стороне роутера:

```sh
python3 scripts/routerkit.py preflight
python3 scripts/routerkit.py healthcheck
```

Флаг `--dry-run` показывает, какую команду wrapper запустил бы:

```sh
python3 scripts/routerkit.py --dry-run plan --generated generated --strict
```

Wrapper не автоматизирует Netcraze Web UI, не создаёт firewall rules, не вызывает `xkeen -start` и не делает скрытых изменений в `/opt`. Команда `backup` делегирует запуск `scripts/backup.sh`; backup archives могут содержать secrets, их нельзя публиковать.

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

## Install plan / dry-run

`scripts/routerkit-plan.py` показывает install operations для локальных generated config fragments без изменений в `/opt`.

```sh
python3 scripts/routerkit-plan.py --generated generated
```

Он проверяет, что `03_inbounds.json`, `04_outbounds.json` и `05_routing.json` являются valid JSON, проверяет loopback-only inbound listeners, показывает summary профилей без outbound secrets и выводит planned copy targets в `/opt/etc/xray/configs`.

План оставляет `S24xray` disabled и явно не вызывает `xkeen -start`, не трогает firewall rules, не включает autostart автоматически, не публикует/не сохраняет secrets и не меняет политики Netcraze Web UI.

Для machine-readable output:

```sh
python3 scripts/routerkit-plan.py --generated generated --json
```

## Команда setup

`scripts/routerkit.py setup` — первый implementation slice дорожной карты one-command setup. Команда оркестрирует существующие безопасные стадии, а не заменяет их:

1. создаёт `profiles.json` через wizard или безопасно переиспользует существующий файл, не печатая его содержимое;
2. генерирует локальные config fragments;
3. запускает strict install plan;
4. после явного разрешения на apply запускает preflight, backup, install и healthcheck.

По умолчанию setup работает только в режиме плана:

```sh
python3 scripts/routerkit.py setup
```

Команда выполняет только локальный сбор или reuse профилей, генерацию и strict plan. Router apply stages не запускаются.

В unified setup stdout и stderr generator подавляются и заменяются общими status messages, потому что могут содержать данные, производные от подписки или учётных данных. При прямом запуске generator сохраняет прежнюю диагностику.

Чтобы продолжить через hardened apply pipeline, используйте:

```sh
python3 scripts/routerkit.py setup --apply
```

После успешного strict plan setup спрашивает `Proceed with router apply stages? [y/N]:`. Флаг `--yes` пропускает только этот confirmation prompt; preflight, backup, install и healthcheck всё равно выполняются:

```sh
python3 scripts/routerkit.py setup --apply --yes
```

Dry-run показывает предполагаемый flow, не запуская wizard, generator, plan, apply stages или confirmation prompt и не создавая локальные profile/generated files:

```sh
python3 scripts/routerkit.py --dry-run setup
python3 scripts/routerkit.py setup --apply --dry-run
```

Это milestone на пути к epic #5, а не финальная реализация. Bootstrap apply остаётся в #13, autostart — в #14, Netcraze proxy/policy — в #15, hardware validation — в #16. `setup` в этом release не вызывает `bootstrap`; интеграция отложена до review planner/manifest contract и hardware evidence. Setup не скачивает и не устанавливает Xray, не включает autostart, не меняет политики Netcraze или default policy, не автоматизирует Web UI, не создаёт firewall/TPROXY/REDIRECT rules и не вызывает `xkeen -start`.

## Команда install

`scripts/routerkit.py install` безопасна по умолчанию. Без `--apply` она запускает такой же strict plan mode и не меняет файлы:

```sh
python3 scripts/routerkit.py install --generated generated
```

С альтернативным target для плана:

```sh
python3 scripts/routerkit.py install --generated generated --target-root /opt
```

`--apply` запускает усиленный apply pipeline:

```sh
python3 scripts/routerkit.py install --generated generated --apply
```

Pipeline по умолчанию:

1. строгий install plan;
2. router preflight;
3. backup;
4. установка generated configs и S23xray-direct;
5. healthcheck.

Если шаг до install завершается ошибкой, pipeline останавливается и не запускает следующие шаги. Если install падает после backup, CLI печатает rollback hint со ссылкой на backup output/path, который вывел `scripts/backup.sh`. Если healthcheck падает после install, CLI предупреждает, что install мог завершиться, и предлагает смотреть logs и использовать pre-apply backup при необходимости rollback.

Backup archives могут содержать secret-bearing router files. Не публикуйте backup archives.

Команда не автоматизирует политики Netcraze Web UI, не создаёт firewall rules, не вызывает `xkeen -start` и не включает autostart. Флаг `--enable-autostart` зарезервирован и сейчас завершает команду до любого install step. Autostart остаётся ручным действием после healthcheck.

Посмотреть apply pipeline без запуска:

```sh
python3 scripts/routerkit.py --dry-run install --generated generated --apply
python3 scripts/routerkit.py install --generated generated --apply --dry-run
```

Для advanced/debug usage доступны skip flags, но они не recommended:

- `--skip-preflight`;
- `--skip-backup`;
- `--skip-healthcheck`.

Default apply flow выполняет все safety steps. Если пропустить backup, rollback может быть сложнее.

## Пример flow

1. Запустить wizard локально:

```sh
python3 scripts/routerkit.py wizard
```

2. Сгенерировать локальные config fragments:

```sh
python3 scripts/routerkit.py generate --profiles profiles.json --out generated
```

3. Посмотреть локальный install plan:

```sh
python3 scripts/routerkit.py install --generated generated
```

4. Скопировать generated config fragments на роутер через ваш приватный способ передачи.
5. Запустить `python3 scripts/routerkit.py install --generated generated --apply` на роутере после review generated files.
6. Проверить apply summary и healthcheck output.
7. Вручную создать Netcraze Web UI proxy connections и policies.

## Security notes

- Не храните secrets в git.
- `profiles.json` ignored.
- `generated/` ignored.
- Не вставляйте реальные generated configs в public issues.
- Не вставляйте реальные subscription URLs, VLESS links, UUID, Reality public keys, short IDs, spiderX values, IP addresses, MAC addresses или hostnames в public issues.
