# ADR: модель выполнения bootstrap

- Статус: принято для read-only planner и явно gated standalone apply
- Дата: 2026-07-13
- Issues: [#13](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/13), planner [#18](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/18), standalone apply [#28](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/28), будущая setup integration [#29](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/29), epic [#5](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/5)

## Вопрос

Где должна выполняться финальная one-command bootstrap-команда до появления полноценного Python-окружения RouterKit и Xray?

## Решение

Выбрана документированная гибридная модель:

1. доверенный host оркестрирует процесс и подключается к роутеру по SSH только через локальный/private interface;
2. до появления Entware Python допустим только минимальный проверяемый POSIX `sh`-этап на роутере с возможностями, подтверждёнными на целевом железе;
3. после ручной activation Entware и подтверждения Python 3 управление передаётся Python CLI репозитория для строгой проверки manifest и планирования;
4. `routerkit bootstrap` и standalone `--dry-run` остаются read-only. `routerkit bootstrap --apply` — отдельная write-capable transaction, требующая fresh live inventory, буквальный `/opt`, trusted fixed-path Entware `opkg`, Linux arm64 и явное подтверждение. `--yes` пропускает только prompt, а `--apply --dry-run` остаётся no-write preview;
5. standalone transaction устанавливает только отсутствующие фиксированные prerequisites, получает только reviewed manifest artifact, проверяет private candidate, сохраняет verified rollback binary, выполняет atomic replacement, post-validation, rollback при ошибке и публикует non-secret provenance receipt;
6. обычный `routerkit setup` не вызывает bootstrap. Эта integration остаётся #29.

Так trust decision, review pin и подтверждение оператора остаются на полноценном host, а проект не предполагает наличие Python на неподготовленном роутере.

## Официальные источники

- Netcraze документирует SSHv2 для безопасного доступа к CLI и отдельный компонент SSH server: [удалённый доступ через SSH](https://support.netcraze.ru/4g/nc-1213/ru/22340-ssh-remote-access-to-the-router-command-line.html).
- Netcraze предупреждает, что Web CLI неполон, и рекомендует полноценное CLI-подключение через Telnet/SSH: [интерфейс командной строки](https://support.netcraze.ru/ultra/nc-1812/ru/18480-command-line-interface--cli-.html). Проект выбирает SSH, а не Telnet.
- Официальная процедура Keenetic требует EXT-раздел USB, рекомендует EXT4, компонент Open Packages, architecture-specific installer и активацию в UI: [установка Entware на USB](https://support.keenetic.ru/eaeu/orbiter-pro/kn-2810/ru/20980-installing-the-entware-repository-on-a-usb-drive.html).
- Официальный CLI reference содержит `opkg disk`, `opkg chroot` и `opkg initrc`, но наличие команд не доказывает безопасную эквивалентность полного activation flow на целевой модели Netcraze: [KeeneticOS 4.0 CLI reference](https://docs.help.keenetic.com/cli/4.0/en/cli_manual_kn-1011.pdf).
- Официальный проект Entware публикует architecture-specific feeds и installers: [Entware](https://github.com/Entware/Entware).
- Xray-core публикует versioned release assets и digest sidecars: [официальные releases](https://github.com/XTLS/Xray-core/releases).

## Рассмотренные варианты

### Python orchestrator на роутере

Подходит после установки Entware Python, но не может быть первым этапом: наличие Python — одна из проверяемых prerequisites. Иначе one-command claim становится циклическим.

### Host orchestrator через SSH/NDM

На host уже есть Python, storage и TLS trust; SSH официально поддержан. Но host не может безопасно угадать, подготовлен ли USB и завершена ли activation Entware, а документированные интерфейсы не доказывают одинаковый non-interactive flow на целевой модели.

### Минимальный POSIX shell на роутере с handoff в Python

Устраняет bootstrap paradox, но слишком ранний перенос download, trust-store, package-manager и recovery логики на ограниченное устройство опасен. Shell-этап должен оставаться минимальным и останавливаться на любой неподдерживаемой среде.

### Гибрид

Объединяет безопасное review-окружение host, минимальный compatibility stage на роутере и существующий Python после появления Python. Это выбранный вариант.

## Минимальная ручная prerequisite

До hardware validation в [#16](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/16) оператор вручную:

1. форматирует и подключает подходящий USB-накопитель как EXT4;
2. устанавливает официальный компонент Open Packages/OPKG;
3. завершает официальную activation Entware и подтверждает доступность `/opt` и Entware shell;
4. включает SSH только для local/private access, не открывая управление роутером в Интернет.

Форматирование USB намеренно вне RouterKit: операция разрушительна и зависит от правильного выбора физического диска и host OS.

## Entware activation и Python

Официальные материалы показывают UI и отдельные CLI building blocks, но не доказывают единую безопасную non-interactive последовательность для целевой модели, firmware, layout диска и architecture. Поэтому Entware activation остаётся manual gate; проект не выдумывает NDM-команды.

После activation read-only planner сообщает о command/Xray state без запуска package manager. Standalone apply разрешает `opkg` только из `/opt/bin/opkg` или `/opt/sbin/opkg`; symlink допустим лишь тогда, когда resolved regular executable остаётся внутри `/opt`. Произвольный `PATH` не является источником доверия для записи.

Фиксированный package set: `ca-bundle`, `curl`, `unzip`, `coreutils-sha256sum` и `python3`. Apply проверяет каждое имя, устанавливает только отсутствующие одним bounded `opkg install` и повторно проверяет все требования. `opkg upgrade`, package input и removal/update unrelated packages запрещены. Установка аддитивна: новые packages могут остаться после сбоя Xray-этапа, потому что автоматическое удаление dependencies небезопасно.

## Artifact и candidate transaction

Runtime artifact inputs — только `linux-arm64` manifest `download_url` и `sha256`; initial URL обязан точно совпадать с validated manifest. Proxy-free HTTPS transport использует только port 443, TLS hostname и connected-peer verification, существующую reviewed global-destination policy, fail-closed mixed DNS answers и отдельную validation каждого redirect. Разрешено не более 5 redirects только на `github.com` или dot-boundary subdomains `githubusercontent.com`; signed query не выводится. Limits: 16 DNS addresses на hop, 5 секунд DNS, 10 секунд connect на address, 180 секунд overall, 8192 bytes URL/Location и 128 MiB archive. Response stream-записывается в exclusive `0600` file и одновременно хэшируется без полной загрузки в RAM.

SHA-256 обязан совпасть с manifest до extraction/execution. Python ZIP handling отклоняет malformed, encrypted, traversal, absolute, backslash-confused, duplicate-normalized, directory, symlink/special, unsupported-compression, oversized и excessive-ratio entries. Записывается только один root `xray`; limits — 128 entries, 96 MiB candidate и ratio 200:1. Candidate становится executable только после полного CRC-checked extraction и обязан вернуть точную первую строку `Xray 26.3.27` в sanitized environment.

Private staging создаётся как unique `0700` directory в RouterKit-owned `/opt/var/tmp/routerkit`, обязан находиться на filesystem destination и удаляется с identity/flat-entry checks при success, failure, `SIGINT`, `SIGTERM` и `SIGHUP`. Scoped handlers останавливают и reap process group в bounded сроки. До replacement catchable signal переходит к cleanup staging. После replacement он входит в recovery critical section: pending и repeated catchable signals откладываются, пока прежний binary восстанавливается и проверяется либо clean-install candidate удаляется с проверкой отсутствия. Обычный signal exit разрешён только после verified recovery и cleanup staging. Неподтверждённый rollback остаётся ошибкой с наивысшим приоритетом и показывает retained backup; cleanup failure имеет приоритет над verified signal exit. `SIGKILL`, потеря питания, kernel failure и host crash остаются residual risks.

## Что требует hardware validation

- фактическое значение architecture на целевой модели/firmware;
- покрывают ли `aarch64` и `arm64` нужное устройство;
- поведение и rollback `opkg disk`/`initrc` и UI activation;
- минимальный shell и trust-store до Entware;
- storage и atomic replacement на USB;
- init paths, reboot persistence и recovery;
- устойчивость SSH при переходах.

## Backup, replacement, rollback и provenance

До replacement `/opt/sbin/xray` открывается без following symlink, identity-check, bounded hash и safe version probe. Существующий target обязан быть regular executable. После checksum/candidate gates binary копируется exclusive в `/opt/var/lib/routerkit/backups/xray-<full-sha256>`; existing deterministic backup переиспользуется только после metadata/hash verification и сохраняется после success.

Validated candidate копируется в exclusive `0755` file destination directory, fsync/hash-check, затем устанавливается same-filesystem `os.replace()` и directory fsync. Service не останавливается и не перезапускается. Installed path обязан совпасть по hash и exact version. Любая post-replacement или receipt-publication ошибка восстанавливает и проверяет hash/version предыдущего backup либо удаляет clean-install candidate и проверяет его отсутствие. Provenance удаляется только после verified binary recovery. Неподтверждённый rollback имеет отдельный nonzero result, показывает retained backup path и никогда не понижается до обычного signal termination; package additions находятся вне binary rollback boundary.

После success атомарно публикуется restrictive `/opt/var/lib/routerkit/bootstrap-state.json` с deterministic release/archive/installed hashes, exact version, backup identity и fixed packages installed by RouterKit. Receipt не содержит secrets, transient URLs, response body, environment, timestamp или staging path. Rerun пропускает install/network/backup/replacement только при совпадении packages, receipt, release, archive hash, current executable hash и exact version. Одна совпавшая version без provenance недостаточна; stale/corrupt state запускает обычную verified transaction.

## Влияние на `routerkit setup`

`routerkit bootstrap --apply` остаётся standalone. `routerkit setup` его не вызывает. #29 сможет добавить явный setup gate позже, сохранив confirmation/safety contract; скрытая package installation или Xray replacement недопустимы.

## Не входит в задачу

USB formatting, автоматическая Entware activation, package index update/upgrade/removal, любые artifacts кроме exact reviewed pin, config/profile consumption, autostart/services, `xkeen -start`, firewall/TPROXY/REDIRECT, Web UI/API/policies и любые router runtime, Docker, database, server или production actions.
