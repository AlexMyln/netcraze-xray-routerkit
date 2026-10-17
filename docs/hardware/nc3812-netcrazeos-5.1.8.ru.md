# Hopper SE NC-3812: совместимость с NetcrazeOS 5.1.8

Дата evidence: **2026-10-17**, Europe/Samara. English: [nc3812-netcrazeos-5.1.8.md](nc3812-netcrazeos-5.1.8.md).

## Поддержка в коде и уровень проверки

RouterKit принимает точный software contract `nc3812-netcrazeos-5.1.8` в
native CLI, external planner/verifier, а также validators intent, receipt и
evidence для `live-install`. Контракт `nc3812-netcrazeos-5.1.5` сохранён и
остаётся значением по умолчанию для обратной совместимости. Для 5.1.8 нужно
явно выбрать новый контракт после проверки модели и firmware цели.
Автоопределения firmware, расширения на диапазон версий или подстановки
контракта 5.1.5 для другой firmware нет. Другие модели и не перечисленные
версии эти live adapters не поддерживают.

**Для 5.1.8 подтверждена совместимость native-чтения; полная hardware write и
runtime acceptance не выполнена.** Добавление software contract не превращает
неизвестные runtime-факты в PASS, не разрешает изменения роутера и не закрывает
hardware matrix #16 или brownfield acceptance rerun #41. Доказательства
установки и reboot recovery от 2026-09-09 для 5.1.5 относятся к той firmware.

## Наблюдения существующей установки

Проверены публичный RouterKit main `084a42f` и один живой Hopper SE.
Настройки, службы, firmware, назначения клиентов и reboot не менялись.
Конфигурационные снимки использовались для офлайн-парсинга в owner-only
временных файлах и затем были удалены.

| Проверка | Наблюдение | Граница доказательства |
| --- | --- | --- |
| Цель | Hopper SE NC-3812, stable 5.1.8, build `5.01.C.8.0-2`, `aarch64` | Живой ответ устройства |
| Связность | RMM отвечает; PPPoE, LAN и три native Proxy подняты/connected | Текущее состояние, без клиентской traffic acceptance |
| Накопитель | Внешний USB EXT4 смонтирован и используется OPKG; init path настроен как `/opt/etc/init.d/rc.unslung` | Literal `/opt` mount и целостность пакетов Entware через Linux shell не проверялись |
| Xray | Процесс `xray` запущен с `run -confdir /opt/etc/xray/configs` | Executable/hash, pinned semantic release и владение listeners не проверялись |
| Proxy | Три включённых SOCKS5 upstream без аутентификации на `127.0.0.1:1082`–`1084` | Native-конфигурация, не доказательство фактического bind Xray |
| Политики | Каждая из трёх policies разрешает только свой Proxy; live IPv4/IPv6 default routes ведут через него | Учтены явные negative permits для остальных global interfaces |
| Сохранение | Семантика Proxy/Policy/assignment, разобранная RouterKit, совпадает в running и saved configuration | Только чтение; новый save и reboot не выполнялись |
| Планирование | Существующие parser и semantic-reuse planner распознают три пары Proxy/Policy без записи | Ожидаемый endpoint manifest построен по native config; установленный runtime manifest не прочитан |
| DNS/клиент | Настроен unbound DoH; единственный клиент с явным VPN assignment был offline | DNS/HTTPS через этого клиента не проверены |

В исходной проверке прошёл 81 focused software test. Тесты не выполняли запись
на роутер. Сохранённый
[native fixture](../../tests/fixtures/netcraze/nc3812-5.1.8-native.txt) содержит
только наблюдавшийся синтаксис Proxy/Policy и синтетический locally administered
MAC клиента. Это не полный config export и не runtime evidence.
Regression tests проверяют LF/CRLF parsing, semantic reuse, NOOP verification,
оба контракта и отклонение неизвестных либо несовпадающих контрактов.

## Выбор контракта

Офлайн-план orchestration для 5.1.8 не обращается к роутеру:

```sh
python3 scripts/routerkit.py live-install plan \
  --transport external \
  --hardware-contract nc3812-netcrazeos-5.1.8 \
  --selected-device-mac 02:00:00:00:00:01 \
  --profile-slot 1
```

MAC в примере синтетический. Standalone native и external tools используют
`--contract nc3812-netcrazeos-5.1.8`. Native apply по-прежнему требует явный
`--contract`; external plan без этого параметра сохраняет default 5.1.5.
External verify использует контракт packet; если указан `--contract`, он обязан
совпадать. При resume нельзя менять firmware contract receipt. Evidence и
transaction одного контракта не принимаются для receipt другого контракта.
После смены firmware нужен новый scope со свежим evidence.

## Что ещё требуется для полной acceptance

Нужны свежие target-side доказательства literal external EXT4 `/opt`, Entware,
точной pinned версии и идентичности Xray, фактических loopback-only listeners
с проверкой владельца, установленного endpoint manifest, autostart и recovery
после отдельно разрешённого reboot. Native write/save/rollback требуют своей
авторизованной live-проверки: software tests её не заменяют. Итоговая клиентская
acceptance по-прежнему требует selected assignment и реальных domain DNS/HTTPS
через выбранную policy.

Runtime-adoption, protected-DNS, read-before-write, fresh readback, verified
save и rollback gates сохранены. Поддержка добавлена офлайн; на Hopper SE
никаких действий не выполняется.
