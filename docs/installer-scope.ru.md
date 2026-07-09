# Область работы установщика

Планируемый guided installer — это one-command core setup, а не установщик “с голого роутера”.

## Предварительные условия

Перед запуском guided installer на роутере уже должны быть:

- Entware/OPKG на USB-накопителе;
- рабочий SSH-доступ в Entware shell;
- Xray binary по пути `/opt/sbin/xray`;
- доступный для записи каталог `/opt/etc/xray/configs`;
- базовые утилиты: `curl`, `jq`, `tar`.

## Что входит в область работы guided installer

Установщик может:

- выполнить preflight checks;
- проверить, что он запущен на Entware/Linux, а не на macOS/Windows;
- проверить `/opt`, `/opt/sbin/xray` и каталоги конфигов;
- принять subscription URLs через скрытый ввод или environment variables;
- извлечь VLESS Reality/TCP ноды и показать их с маскировкой секретов;
- сгенерировать Xray config fragments;
- установить `S23xray-direct`;
- оставить `S24xray` выключенным;
- запустить Xray напрямую без `xkeen -start`;
- выполнить healthcheck;
- предложить включить autostart после явного подтверждения;
- вывести точные шаги для Web UI proxy connections и policies.

## Что не входит в первую версию guided installer

Первая версия не будет:

- форматировать USB-накопители;
- устанавливать компоненты прошивки Netcraze/Keenetic;
- устанавливать Entware/OPKG с нуля;
- слепо скачивать и устанавливать Xray из непроверенных источников;
- автоматически кликать Web UI;
- менять default policy роутера;
- создавать TPROXY/REDIRECT/firewall rules;
- публиковать или хранить реальные секреты.

## Почему

Подготовка USB, установка Entware, компоненты прошивки и назначение Web UI policies зависят от модели, прошивки и конкретной домашней сети. Эти шаги могут быть разрушительными, если автоматизировать их вслепую.

Установщик должен fail closed: если prerequisites не выполнены, он должен остановиться и вывести checklist, а не угадывать.
