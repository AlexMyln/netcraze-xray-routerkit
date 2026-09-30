# NC-3812 / NetcrazeOS 5.1.6: предварительное read-only наблюдение

Дата наблюдения: **2026-09-30** (Europe/Samara, UTC+04).
Статус: **предварительные данные ограниченной проверки устройства только чтением**.

## Источник и граница версий

Это обезличенное резюме переданного оператором файла
`RouterKit-5.1.6-readonly-report.md`, а не независимый повтор проверки или fixture
с исходными ответами. В отчёте указан один Netcraze Hopper SE **NC-3812**,
display version **5.1.6**, точный наблюдаемый build **`5.01.C.6.0-1`**, канал
**stable**. Build прочитан с устройства, а не выведен из display version.

В отчёте использовалась чистая ветка `main` на
[`6e416de00a201e65b05ef7358b8f4d369f092665`](https://github.com/AlexMyln/netcraze-xray-routerkit/commit/6e416de00a201e65b05ef7358b8f4d369f092665)
с Python 3.14.7. Отдельная проверка репозитория использовала
[`084a42fbce13997948b9586ac8c2a9b684a03742`](https://github.com/AlexMyln/netcraze-xray-routerkit/commit/084a42fbce13997948b9586ac8c2a9b684a03742).
[Сравнение коммитов](https://github.com/AlexMyln/netcraze-xray-routerkit/compare/6e416de00a201e65b05ef7358b8f4d369f092665...084a42fbce13997948b9586ac8c2a9b684a03742)
показывает, что второй опережает первый на пять коммитов, а первый является
merge base. В этих изменениях добавлены external native transactions и
live-install orchestration, включая исправление границы runtime execution.
Результаты старого checkout не проверяют эти новые пути.

## Метод

В отчёте перечислены 84 доступных инструмента Netcraze MCP; это не означает,
что были выполнены все 84. Использовались typed reads и одиночные команды
`cli_show`, синтаксис которых предварительно проверялся через `kb_command`.
Не применялись native writes, `cli`/`cli_batch`, браузер, SSH, полные снимки
конфигурации, активные HTTP/DNS-пробы, изменения служб или прошивки.

## Что подтверждено в отчёте

| Наблюдение | Указанные доказательства | Граница утверждения |
| --- | --- | --- |
| Точная модель/build и доступность управления | `list_devices`, `get_device_info`, `show version`, `device_is_available` | Один экземпляр; нет обобщения на всё семейство |
| WAN и LAN доступны | `get_internet_status`: PPPoE0 и Bridge0 up/connected/link up; основной default route через PPPoE0 | Нет сопоставимого снимка до обновления; неизменность не доказана |
| Установлен native proxy component, присутствуют три Proxy-интерфейса | Список компонентов и status; Proxy0/1/2 up/connected/link up, remote endpoint на loopback | SOCKS-протокол и точные порты не раскрыты |
| Default routes политик только через соответствующий Proxy | `list_ip_policies`: у Policy0/1/2 единственные IPv4 и IPv6 default routes идут через Proxy0/1/2 соответственно | Есть и не-дефолтные служебные маршруты; клиентский трафик не проверялся |
| Текущие назначения клиентов | 18 зарегистрированных записей: одна в Policy0, ни одной в Policy1/2, 17 без явной policy | Назначенная запись неактивна; ожидаемый набор назначений и состав Default policy независимо не доказаны |
| Настроен HTTPS DNS | `show dns-proxy`: по два HTTPS DNS-сервера у System и каждой из трёх политик | Конфигурация не доказывает успешный DNS или доступность resolver из policy |
| USB присутствует | `get_usb_status`; компоненты storage/opkg установлены | Не доказывает filesystem, `/opt`, Entware или Xray |

Сообщение владельца о работающем сервисе остаётся пользовательским наблюдением,
отдельным от перечисленных проверок. Здесь нет приватных идентификаторов
устройства, имён клиентов, адресов, credentials или полной конфигурации.

## Офлайн-проверки из отчёта для `6e416de`

- Валидатор platform compatibility: exit 0.
- Первая unittest-команда с пятью модулями не импортировала
  `tests.test_routerkit_netcraze_external` и
  `tests.test_routerkit_live_install`: их не было в этом checkout. Оба модуля
  добавлены в указанном выше диапазоне коммитов. Ошибки импорта означают
  несовпадение checkout и списка тестов, а не дефект совместимости устройства.
- Повтор с тремя существующими модулями (`test_routerkit_netcraze_live_entrypoint`,
  `test_routerkit_netcraze_live`, `test_routerkit_netcraze_live_compat`) завершился
  успешно: **14 тестов**. Ожидаемые отрицательные contract cases не являются
  live-сбоями.
- После тестов изменений отслеживаемых исходников не было. Это офлайн-проверки
  ПО, а не запуск этих code paths против устройства.

## Вывод о совместимости и неизменные gates

В пределах описанных чтений несовместимого ответа или команды не обнаружено.
Это поддерживает только предварительную read-only оценку. В отчёте нет полного
`show running-config`, поэтому штатный live parser RouterKit не был проверен,
а точное семантическое переиспользование существующих объектов не доказано.
Наблюдаемого дефекта, требующего адаптации кода, эти данные не содержат.

Поддерживаемый hardware contract остаётся **`nc3812-netcrazeos-5.1.5`** в live
adapter, external transaction path и live-install schemas. Нельзя выдавать
наблюдаемое устройство 5.1.6 за контракт 5.1.5, чтобы обойти gate. Датированный
[public-source compatibility manifest](../../hardware/routerkit-platform-compatibility.v1.json)
не меняется: этот отчёт не является vendor release source или завершённым
hardware canary. Он не закрывает требования brownfield live-install и
clean-hardware validation.

## Что нужно для более сильных утверждений

1. Зафиксировать нужный RouterKit commit и запустить его применимые офлайн-тесты;
   отделять их результат от live evidence прошивки.
2. При отдельном разрешении на защищённое чтение конфигурации подтвердить точные
   SOCKS5 loopback-порты, семантику Proxy/Policy, выбранные назначения и Default
   guard. Проверить штатные parser/verifier RouterKit вне устройства без подмены
   hardware contract и без записи на роутер.
3. Через подходящий авторизованный target-side read path подтвердить архитектуру,
   внешний EXT4 `/opt`, доверенный Entware, pinned Xray identity, процесс,
   принадлежность/bindings слушателей и autostart. Одного MCP status недостаточно.
4. С разрешением на активные проверки и активным выбранным клиентом проверить
   domain DNS и HTTPS через нужную policy, доступность DNS из неё и ожидаемый
   прямой путь невыбранных клиентов.
5. До расширения write contract на 5.1.6 отдельно рассмотреть точный контракт и
   выполнить необходимые авторизованные проверки write/readback/save, rollback,
   идемпотентности и reboot/recovery. Работающий сервис не является поводом
   автоматически переустанавливать его или запускать эти вмешательства.

См. [platform compatibility](platform-compatibility.ru.md) и
[external native transport](../architecture/netcraze-external-transport.ru.md).
