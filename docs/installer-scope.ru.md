# Область работы установщика

Планируемый guided installer — это one-command core setup, а не установщик “с голого роутера”.

## Предварительные условия

Перед запуском guided installer на роутере уже должны быть:

- Entware/OPKG на USB-накопителе;
- завершённая официальная activation Entware с доступным `/opt`;
- рабочий SSH-доступ в Entware shell;
- доступный для записи каталог `/opt/etc/xray/configs`;
- Python 3 для запуска RouterKit.

## Что входит в область работы guided installer

Установщик может:

- выполнить preflight checks;
- проверить, что он запущен на Entware/Linux, а не на macOS/Windows;
- проверить `/opt`, `/opt/sbin/xray` и каталоги конфигов;
- принять subscription URLs через скрытый ввод или environment variables;
- извлечь VLESS Reality/TCP ноды и показать их с маскировкой секретов;
- сгенерировать Xray config fragments;
- с явным `setup --apply --bootstrap-apply` установить фиксированный набор prerequisites и установить либо transactionally заменить manifest-pinned Xray binary;
- установить `S23xray-direct`;
- оставить `S24xray` выключенным;
- выполнить healthcheck;
- с явным `setup --apply --enable-autostart` или `install --apply --enable-autostart` включить только `S23xray-direct` после healthcheck и строгой runtime verification;
- с явным `setup --discover-devices` запустить read-only fixture-first device discovery и optional no-write selection после strict planning;
- с явными `setup --plan-netcraze --netcraze-state-file PATH` запустить consistency-validated fixture-first preview connections/policies/optional assignment до confirmation; при сочетании с apply явно сообщить, что все Netcraze actions исключены;
- вывести точные шаги для Web UI proxy connections и policies.

## Что не входит в первую версию guided installer

Первая версия не будет:

- форматировать USB-накопители;
- устанавливать компоненты прошивки Netcraze/Keenetic;
- устанавливать Entware/OPKG с нуля;
- слепо скачивать и устанавливать Xray из непроверенных источников;
- перезапускать services вне явной autostart transaction, доказывать reboot persistence или вызывать `xkeen -start`;
- автоматически кликать Web UI;
- менять default policy роутера;
- назначать discovered devices на policies до #15;
- по умолчанию активно сканировать LAN;
- создавать TPROXY/REDIRECT/firewall rules;
- публиковать или хранить реальные секреты.

## Почему

Подготовка USB, установка Entware, компоненты прошивки и назначение Web UI policies зависят от модели, прошивки и конкретной домашней сети. Эти шаги могут быть разрушительными, если автоматизировать их вслепую.

Установщик должен fail closed: если prerequisites не выполнены, он должен остановиться, а не угадывать. Package additions явного bootstrap могут остаться, а Xray replacement имеет отдельную проверенную границу backup/rollback. Autostart enable не является шагом firewall, Web UI, policy, default-policy, device-discovery или reboot validation. Fixture-first device discovery и offline план #15 не являются policy или assignment write. Hardware validation остаётся в #16.
