# ADR: модель выполнения bootstrap

- Статус: принято для read-only planning slice; apply остаётся заблокирован
- Дата: 2026-07-13
- Issues: [#13](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/13), [#18](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/18), epic [#5](https://github.com/AlexMyln/netcraze-xray-routerkit/issues/5)

## Вопрос

Где должна выполняться финальная one-command bootstrap-команда до появления полноценного Python-окружения RouterKit и Xray?

## Решение

Выбрана документированная гибридная модель:

1. доверенный host оркестрирует процесс и подключается к роутеру по SSH только через локальный/private interface;
2. до появления Entware Python допустим только минимальный проверяемый POSIX `sh`-этап на роутере с возможностями, подтверждёнными на целевом железе;
3. после подтверждения Entware и Python 3 управление передаётся Python CLI репозитория для строгой проверки manifest, планирования и будущих явно разрешённых apply-стадий;
4. в текущем slice реализован только read-only Python planner: он не подключается к роутеру и ничего не применяет.

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

После activation planner только сообщает о наличии `opkg`, `python3`, `curl`, `unzip` и checksum tooling. Отсутствие даёт warning и не исправляется в этом PR.

## Что требует hardware validation

- фактическое значение architecture на целевой модели/firmware;
- покрывают ли `aarch64` и `arm64` нужное устройство;
- поведение и rollback `opkg disk`/`initrc` и UI activation;
- минимальный shell и trust-store до Entware;
- storage и atomic replacement на USB;
- init paths, reboot persistence и recovery;
- устойчивость SSH при переходах.

## Rollback boundary

Текущий slice заканчивается до первой записи, поэтому восстанавливать нечего. Будущий apply обязан сохранить и проверить backup `/opt/sbin/xray`, проверить candidate, заменить его атомарно и оставить recovery path. Entware activation, USB formatting, router components, service/firewall/policy state вне текущего slice.

## Влияние на `routerkit setup`

`routerkit bootstrap` остаётся отдельной read-only командой. `routerkit setup` её пока не вызывает. Следующий slice #13 сможет добавить явный planner gate только после review контракта и результатов hardware validation; скрытая package installation или Xray replacement недопустимы.

## Не входит в задачу

USB formatting, автоматическая Entware activation, package installation/update, download/replacement Xray, autostart/services, `xkeen -start`, firewall/TPROXY/REDIRECT, Web UI/API/policies и любые router runtime, Docker, database, server или production actions.
