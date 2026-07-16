# Исследование интерфейса политик Netcraze/Keenetic

Статус: `SOFTWARE_PLAN_CORE_READY_HARDWARE_WRITE_CONTRACT_PENDING`.

Использовались только публичные источники. Доступа к роутеру, LAN и локальному API не было. Команды и пути ниже — кандидаты для будущего аппаратного контракта; в production-коде планировщика их нет.

Основной источник — официальный [справочник KeeneticOS 4.0 для Hero KN-1011](https://docs.help.keenetic.com/cli/4.0/en/cli_manual_kn-1011.pdf). Он документирует дерево NDM, REST Core Interface `/rci`, GET/POST/DELETE для настроек, Proxy-интерфейс с SOCKS5 и upstream host/port, создание и просмотр IP Policy, привязку политики к известному хосту по MAC, асинхронное сохранение конфигурации и fail-safe timer/commit/rollback. Обработка вложенного JSON идёт сверху вниз, но атомарность пакета не заявлена.

Официальная [инструкция по startup-config](https://destek.keenetic.com.tr/titan/kn-1811/tr/16479.html) подтверждает скачивание резервной конфигурации и восстановление загрузкой файла. Она не доказывает безопасный автоматизированный контракт для целевой прошивки Netcraze.

| Возможность | Интерфейс-кандидат | Режим | Доказательство | Уверенность | Что проверить на устройстве |
|---|---|---:|---|---|---|
| Инвентарь соединений | статус/config интерфейсов, RCI | read | общая схема интерфейсов | `official_inferred` | точные JSON-поля и утечки чувствительных данных |
| Инвентарь политик | `show ip policy`, RCI | read | справочник модели | `official_documented` | ID, default-маркер, схема Netcraze |
| Привязки устройств | hotspot/known-host | read | связь записи документирована | `official_inferred` | полное соединение данных с #21 |
| Глобальная политика | статус политик + Web UI | read | универсальный ID не доказан | `hardware_confirmation_required` | однозначная идентичность default |
| Backup/export | startup-config | read | инструкция производителя | `official_documented` | auth, ограниченный export, restore-canary |
| Создание/изменение Proxy | NDM/RCI settings | write | протокол и upstream документированы | `official_inferred` | полный порядок, ответы, удаление |
| Создание/изменение Policy | NDM/RCI settings | write | create/remove документированы | `official_documented` | ссылка на Proxy, лимиты, защита default |
| Assign/unassign | host policy по MAC | write | справочник модели | `official_documented` | prerequisites, schema, rollback |
| Транзакция/commit | nested RCI, config save, fail-safe | write | механизмы документированы раздельно | `official_inferred` | атомарность, revision, границы отказа |
| Верификация | status интерфейса/политики/хоста | read | status-команды существуют | `official_inferred` | propagation и точная эквивалентность |
| Rollback | обратные операции, restore, fail-safe | write | полного контракта нет | `hardware_confirmation_required` | disposable canary и отказные сценарии |

Не доказаны стабильность ID, уникальность имён, revision token, ownership marker, атомарность, безопасный состав ответов и роли авторизации. Поэтому fixture — только наблюдение; совпадение имени не даёт права на reuse/update; fixture не может сообщить ownership, успешный backup или write authority. Live adapter и apply-команда отсутствуют. Контракты #21/#15 и аппаратная проверка #16 остаются открытыми.
