# Fixture-first планирование политик Netcraze

Программное ядро #15 — чистая функция: приватный manifest локальных endpoints + защищённый синтетический snapshot + необязательный `DeviceSelection` из #21 + необязательные доказательства будущего adapter-кода → детерминированные `ChangePlan` и обратный `RollbackPlan`.

В модуле нет транспорта, live adapter, apply-команды, сетевого клиента, процессов и сохранения выбора устройства.

Fixture описывает только наблюдаемое состояние. Он не может объявить ownership, права update/delete, trusted revision, успешный backup или live capability — такие поля отвергаются. `AdapterOwnershipProof` создаётся только программно и содержит точное состояние rollback. Без него разрешены лишь exact reuse и план создания отсутствующего объекта; совпадение имени с другой семантикой — конфликт; delete не планируется.

Default policy неизменяема. Даже известная метка fixture не даёт полномочий. Каждый план заканчивается проверкой `default_policy_unchanged`; неизвестная или неоднозначная default policy блокирует write readiness.

`routerkit.local-endpoints.v1` содержит только слоты 1–3, безопасную метку, `127.0.0.1`/`::1`, порты 1082–1084, enabled и SOCKS5. Manifest записывается атомарно с owner-only permissions и не содержит upstream, UUID, ключи, SNI, subscription/provider/credentials.

`routerkit.netcraze.state.fixture.v1` читается общим защищённым reader: bounded UTF-8, без symlink/hardlink, owner-only на POSIX, с проверками identity до/после чтения, строгими полями, лимитами и запретом duplicate IDs/names.

Порядок: connections → policies → необязательный assignment → verification → доказательство неизменности default. Любой конфликт блокирует весь план. Fingerprint не включает snapshot ID, имя/MAC устройства, сырой inventory и секреты. Simulator работает только в памяти, откатывает в обратном порядке и не является доказательством поведения железа. Public evidence удаляет локальные идентификаторы; редактирование не гарантирует анонимность.
