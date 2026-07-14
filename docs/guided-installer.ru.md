# Guided installer

Guided setup версии v0.2-alpha объединяет profile-source acquisition, приватную локальную генерацию, strict planning и явно подтверждённые router apply stages. Он не автоматизирует Netcraze Web UI, firewall, autostart или device policies.

## Prerequisites

Перед этим flow роутер нужно подготовить вручную:

- Entware установлен на USB-накопитель;
- SSH-доступ к Entware shell работает;
- `/opt/sbin` доступен; существующий `/opt/sbin/xray` можно заменить, но standalone bootstrap apply также поддерживает clean install;
- `/opt` и `/opt/etc` доступны на роутере.

## Единый CLI

`scripts/routerkit.py` — единая Python-точка входа. Она делегирует отдельные tools, а для setup также управляет созданием workspace, безопасным reuse profiles, sanitation source environment, lifecycle дочерних source/generator процессов, cleanup, confirmation и apply orchestration. Обычные delegated commands сохраняют exit code дочернего процесса.

```sh
python3 scripts/routerkit.py wizard
python3 scripts/routerkit.py generate --profiles profiles.json --out generated
python3 scripts/routerkit.py plan --generated generated
```

### Standalone bootstrap transaction

Read-only planner (#18) и отдельно gated standalone apply (#28) входят в [bootstrap #13](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/13):

```sh
python3 scripts/routerkit.py bootstrap
python3 scripts/routerkit.py bootstrap --dry-run
python3 scripts/routerkit.py bootstrap --apply
python3 scripts/routerkit.py bootstrap --apply --yes
python3 scripts/routerkit.py bootstrap --apply --dry-run
```

Поддерживается только Linux `aarch64`/`arm64`. Обычный режим и standalone `--dry-run` не выполняют package command, network, staging или запись. Apply требует буквальный `/opt`, свежий live inventory, trusted fixed-path Entware `opkg` и явное подтверждение; `--yes` пропускает только этот prompt. Apply dry-run — no-write preview без prompt. Synthetic inventory разрешён только для read-only tests/development и конфликтует с apply. Manifest репозитория используется по умолчанию; standalone `--manifest` — явный operator-controlled trust input, проходящий те же validation gates.

Apply запрашивает только отсутствующие top-level имена из фиксированного набора `ca-bundle`, `curl`, `unzip`, `coreutils-sha256sum` и `python3`; RouterKit не запрашивает upgrade или removal. Trusted dependencies и maintainer scripts остаются под управлением `opkg`, а additions могут остаться после частично неуспешного package install или более позднего сбоя, потому что автоматическое удаление небезопасно. Затем exact manifest URL stream-загружается через proxy-free HTTPS с per-hop destination/TLS validation и жёсткими limits, проверяется SHA-256, безопасно извлекается только root `xray`, а первая непустая строка version output обязана точно совпасть с pin. Существующий executable хэшируется и копируется в проверенный deterministic backup до same-filesystem atomic replacement. Ошибка post-install hash/version восстанавливает backup; failed clean install удаляет новый candidate. Restrictive receipt сохраняет non-secret provenance и разрешает idempotent no-network rerun только при совпадении всех identity fields.

Manual Entware activation остаётся обязательной. Bootstrap не перезапускает services, не включает autostart, не устанавливает configs, не читает profile secrets, не вызывает `xkeen -start` и не меняет firewall/Web UI/policies. Ctrl-C на apply confirmation prompt не запускает transaction action. Во время mutable apply `SIGINT`, `SIGTERM` и `SIGHUP` останавливают forward progress; после replacement повторные catchable signals откладываются до завершения проверенного rollback или удаления clean-install candidate и cleanup staging. Проверенное SIGINT recovery завершается exit `130`; неподтверждённое recovery возвращает отдельный exit `3` с указанием сохранённого backup. `SIGKILL`, потеря питания, kernel failure и host crash остаются residual risks. Setup вызывает bootstrap только с явной парой `--apply --bootstrap-apply`. Полный contract описан в [ADR модели выполнения](architecture/bootstrap-execution-model.ru.md) и [проверке Xray pin](xray-artifact-pin.ru.md). Hardware validation остаётся [#16](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/16), настоящий one-command [epic #5](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/5) не завершён.

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

## Безопасное получение profile-source и offline parsing

Команда `profile-source` проверяет локальный payload либо безопасно получает HTTPS subscription и создаёт совместимый с generator приватный `profiles.json`:

```sh
python3 scripts/routerkit.py profile-source
python3 scripts/routerkit.py profile-source --source-env ROUTERKIT_PROFILE_SOURCE
python3 scripts/routerkit.py profile-source --source-file /private/path/payload.txt --list
python3 scripts/routerkit.py profile-source --source-file /private/path/payload.txt --list --json
python3 scripts/routerkit.py profile-source --source-file /private/path/payload.txt --primary-index 1 --fallback-index 2 --yes
```

Источник принимается через hidden interactive input, имя environment variable или regular local UTF-8 file. `profile-source --source-file` использует hardened reader: он отклоняет symlink и не-regular files, а на POSIX файл должен иметь права только для владельца, например `0400` или `0600`, без group/other bits. Tool никогда не меняет права source-file автоматически; проверка permission bits применяется только на POSIX. Legacy-поле generator `subscription_file` остаётся расширенным compatibility/debug path и не предоставляет ту же policy для permissions и symlink; для секретного локального payload следует предпочесть `profile-source --source-file`. Обычного CLI-аргумента для raw secret value намеренно нет. Parser поддерживает raw VLESS link, newline-separated links, Base64 subscription text, string values во вложенном JSON и Base64-decoded JSON. Размер payload/decoded data, глубина JSON и число candidates ограничены.

Compatibility намеренно узкая: VLESS с синтаксически корректным UUID, endpoint и port; Reality security; TCP transport (`raw` нормализуется в `tcp`); структурно допустимый Base64URL-style Reality public key; пустой либо hexadecimal short ID чётной длины до 16 символов; пустой flow либо `xtls-rprx-vision`. SNI внутри parser по умолчанию берётся из endpoint host, а spider path нормализуется с начальным `/`. Совместимые nodes дедуплицируются детерминированно.

Нумерованный список secret-safe и очищает недоверенные fragments. Он не печатает raw payload, link, UUID, host, SNI, public key, short ID или spider path. Неподдерживаемые URI schemes отклоняются без вывода source. Нужно выбрать ровно один primary и ноль, один или два fallback. Профили получают имена `primary`, `fallback-1`, `fallback-2` и локальные SOCKS ports `1082`, `1083`, `1084`. Новый output атомарно публикуется без перезаписи destination, появившегося во время записи, и получает mode `0600` на POSIX. Замена требует явного `--force`; даже тогда symlink и не-regular destination отклоняются. `--list` и `--dry-run` ничего не записывают; `--yes` не подразумевает `--force`.

HTTPS source может быть прямой subscription или стандартным redirect-based shortlink. Outer whitespace вокруг полного HTTPS value удаляется, включая LF/CRLF в конце защищённого файла, без изменения path/query или raw/offline payload; internal whitespace, control characters, multiple lines и empty values отклоняются. Допускается только HTTPS port 443, без userinfo и fragments. Каждый hop повторяет URL validation и проверку всех DNS addresses, требует полностью global set по fixed reviewed special-purpose CIDR tables и дополнительным defense-in-depth проверкам standard-library `ipaddress`, закрепляет TCP за validated address, проверяет TLS для original hostname и сверяет peer address. IPv4-mapped IPv6, стандартизованные NAT64, Teredo, 6to4 и ORCHID ranges запрещены. Обычная cancellation прекращает retries и redirects, после чего выполняются ограниченные best-effort попытки cleanup. Максимум — 5 redirects и 16 DNS addresses на hop; лимиты DNS, connection, operational, URL/Location и body равны 5 секундам, 10 секундам, 30 секундам плюс bounded cleanup grace, 8192 bytes и 1 MiB. Dedicated compatibility job выполняет destination/address-policy test class на Python 3.8.18 и primary `3.x`; полный multiprocessing, TLS, HTTP, deadline и cancellation suite запускается только на основном CI Python. Compressed responses отклоняются. RouterKit следует только поддерживаемым HTTP 3xx redirects с `Location`; он не выполняет JavaScript и не интерпретирует HTML meta refresh, поэтому эти browser-style механизмы навигации не поддерживаются и не выполняются. Финальный HTTP 200 body передаётся offline parser, который завершается generic error, если не находит совместимый profile payload. Подробнее: [security ADR](architecture/profile-source-network-security.ru.md).

Для этой standalone-команды `--dry-run` означает no-write, а не no-network: HTTPS source загружается и разбирается для проверки selection, но output не записывается. Generator использует тот же resolver для `subscription_url` и `subscription_url_env`. Default integration `routerkit setup` теперь завершает #20/#24 и использует описанный ниже более строгий contract: без чтения source/reuse/secret/environment value, stdin prompt, network, subprocess, private workspace или write.

После cancellation profile-source не делает безусловного no-write утверждения: узко попавший по времени interrupt может прийти после atomic publication. Secret не печатается; перед повтором проверьте, существует ли запрошенный output file. Setup обычно удаляет setup-owned output во время cleanup private workspace.

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

`scripts/routerkit.py setup` объединяет существующие profile-source, generator, strict-plan и apply tools в stop-on-first-failure pipeline:

1. принимает source через hidden input, указанную environment variable или защищённый owner-only file;
2. при необходимости использует reviewed profile-source resolver для HTTPS;
3. разбирает compatible nodes и выбирает один primary плюс до двух fallback;
4. публикует выбранные profiles только внутри уникального private setup workspace;
5. генерирует local config fragments с подавленным выводом generator;
6. сразу после завершения generator удаляет private setup profiles и workspace;
7. запускает strict install plan;
8. после явного разрешения на apply при необходимости запускает standalone bootstrap transaction, затем preflight, backup, install и healthcheck.

По умолчанию setup работает только в режиме плана:

```sh
python3 scripts/routerkit.py setup
```

Без source option setup читает source через hidden input и интерактивно предлагает выбрать nodes. Случайного reuse `profiles.json` из current directory больше нет. Команда выполняет source acquisition/selection, локальную генерацию, cleanup и strict plan. Router apply stages не запускаются.

Для non-interactive input передавайте только имя environment variable или path защищённого file, но не raw source как обычный argument:

Для `--source-env` setup требует валидное имя выделенной переменной `ROUTERKIT_*`. Дочерний процесс profile-source копирует и удаляет значение до классификации URL или создания DNS resolver worker; generator, strict plan, integrated bootstrap и все последующие apply subprocesses получают обычный environment без одной выбранной переменной. Raw source не копируется в argv или output. Standalone `profile-source --source-env` сохраняет совместимость с другими валидными именами environment variables, потому что не использует внутренний setup-only consume option.

```sh
ROUTERKIT_PROFILE_SOURCE='...' \
python3 scripts/routerkit.py setup \
  --source-env ROUTERKIT_PROFILE_SOURCE \
  --primary-index 1 \
  --fallback-index 2

python3 scripts/routerkit.py setup \
  --source-file /protected/path/source.txt \
  --primary-index 1
```

Reuse существующих private profiles и старый wizard доступны как явные advanced/compatibility modes:

```sh
python3 scripts/routerkit.py setup \
  --reuse-profiles /protected/path/profiles.json

python3 scripts/routerkit.py setup --legacy-wizard
```

`--profiles PATH` — deprecated explicit alias для `--reuse-profiles PATH` без default value. `--force-wizard` — deprecated alias для `--legacy-wizard`. Secure reuse отклоняет symlink и не-regular file, требует owner-only permissions на POSIX, выполняет bounded UTF-8 read, проверяет identity/metadata path и descriptor и копирует validated content с mode `0600` внутрь private `0700` setup workspace. Original file не изменяется, не удаляется, не копируется в backup, не печатается и не передаётся generator.

В unified setup stdout и stderr generator подавляются и заменяются общими status messages, потому что могут содержать данные, производные от подписки или учётных данных. При прямом запуске generator сохраняет прежнюю диагностику.

Пока setup владеет private workspace, перехватываемые `SIGTERM` и `SIGHUP` приводят к управляемой остановке process group source/generator, bounded escalation при необходимости, reaping дочернего процесса и cleanup workspace до выхода. `SIGINT` остаётся обычной интерактивной отменой. `SIGKILL`, потеря питания, сбой kernel и crash хоста не могут выполнить user-space cleanup и способны оставить owner-only workspace для ручного удаления. Generated fragments намеренно сохраняются как secret-bearing local artifacts; cleanup временного workspace их не удаляет.

Чтобы продолжить через hardened apply pipeline, используйте:

```sh
python3 scripts/routerkit.py setup --apply
```

После успешного strict plan setup спрашивает `Proceed with router apply stages? [y/N]:`. Флаг `--yes` пропускает только этот confirmation prompt; preflight, backup, install и healthcheck всё равно выполняются:

```sh
python3 scripts/routerkit.py setup --apply --yes
```

Явная подготовка runtime — отдельный третий режим:

```sh
python3 scripts/routerkit.py setup --apply --bootstrap-apply
```

После cleanup generated profiles и успешного strict plan setup печатает предупреждение о packages/Xray и задаёт один видимый вопрос `Proceed with bootstrap and router apply stages? [y/N]:`. После подтверждения он делегирует repository-default standalone-команде `routerkit-bootstrap.py --apply --yes`, затем выполняет preflight, backup, install и healthcheck. Внутренний `--yes` исключает второй bootstrap prompt; setup `--yes` подавляет только один setup prompt. Сбой bootstrap, cancellation или неподтверждённый rollback останавливает все последующие router stages с сохранением standalone exit code. Любой catchable signal, замеченный setup bootstrap supervisor, также останавливает все последующие router stages. Добавленные packages могут остаться; Xray replacement использует отдельный проверенный backup/rollback standalone transaction. Service restart и autostart не выполняются.

Dry-run показывает абстрактный secret-free flow без чтения source, reuse file, secret input или environment value; без stdin prompt; и без DNS/HTTPS request, subprocess, private workspace, profiles, generated files, write или router actions:

```sh
python3 scripts/routerkit.py --dry-run setup
python3 scripts/routerkit.py setup --apply --dry-run
python3 scripts/routerkit.py setup --apply --bootstrap-apply --dry-run
```

Этот setup dry-run contract отличается от standalone `profile-source --dry-run`: standalone profile-source может выполнить HTTPS network read для validation selection, а setup dry-run не выполняет secret-input или network access. Загрузка Python modules и определение repository path не входят в secret-input contract.

Эта integration функционально завершает #29 и parent #13, но не epic #5. Autostart — #14, Netcraze proxy/policy — #15, device discovery — #21, hardware validation — #16. Path не считается hardware-tested до завершения #16. Обычный `setup` и `setup --apply` не запускают bootstrap; pinned Xray скачивается или устанавливается только в явном combined mode. Ни один setup mode не активирует Entware, не включает autostart, не обнаруживает devices, не меняет политики Netcraze или default policy, не автоматизирует Web UI, не создаёт firewall/TPROXY/REDIRECT rules и не вызывает `xkeen -start`. Generated fragments остаются secret-bearing local operational artifacts, которые нельзя публиковать.

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
