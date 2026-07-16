# Fixture-first планирование политик Netcraze

Программное ядро #15 — чистая функция: приватный manifest локальных endpoints + защищённый синтетический snapshot + необязательный `DeviceSelection` из #21 → детерминированные `ChangePlan` и обратный `RollbackPlan`.

В модуле нет транспорта, live adapter, apply-команды, сетевого клиента, процессов и сохранения выбора устройства.

Fixture описывает только наблюдаемое состояние. Он не может объявить ownership, права update/delete, trusted revision, успешный backup или live capability — такие поля отвергаются. В production planner больше нет caller-created authorization object: разрешены exact reuse и план создания отсутствующего объекта; совпадение имени с другой семантикой и перемещение существующего assignment блокируются; pre-existing объекты не обновляются и не удаляются. Будущий hardware-confirmed adapter должен определить ownership markers, revision binding, exact before-state, concurrency checks и rollback semantics.

Canonical validator проверяет все policy→connection, assignment→policy и default references, уникальность assignment, нормализованный trusted MAC, default flags/status и semantic completeness. Статическое поле плана `default_policy_not_targeted` вычисляется из validated default identity, targets, generated names/IDs и typed dependencies. Simulator отдельно сравнивает canonical before/after projection default policy вместе с семантикой её connection. Unknown/ambiguous default остаётся явной diagnostic state и блокирует readiness.

`routerkit.local-endpoints.v1` содержит только слоты 1–3, code-owned labels `primary`/`fallback-1`/`fallback-2`, `127.0.0.1`/`::1`, порты 1082–1084, enabled и SOCKS5. Raw profile names не попадают в manifest. Publication использует private parent, exclusive bounded temp, file `fsync`, replacement только распознанного предыдущего RouterKit manifest и parent-directory `fsync`. Unrelated/unsafe target сохраняется без изменений. Предыдущий valid manifest удаляется до current generation, поэтому failed generation не оставляет stale current evidence.

`routerkit.netcraze.state.fixture.v1` читается общим защищённым reader: bounded UTF-8, без symlink/hardlink, owner-only на POSIX, с проверками identity до/после чтения, строгими полями, лимитами и запретом duplicate IDs/names, orphan references, duplicate/multi-policy assignments, inconsistent default evidence и impossible state/capability combinations.

Порядок: create/reuse connections → create/reuse policies → необязательный assignment только для unassigned synthetic device → verification → static default non-targeting proof. Новая policy содержит typed planned-connection dependency; simulator сначала создаёт connection, присваивает deterministic simulation-only ID и разрешает dependency. Validator запускается до planning/simulation, после каждой mutation, после rollback, перед success и idempotent rerun. Fingerprint включает dependency categories и derived default invariant, но не raw profile names. Simulator не является доказательством поведения железа.

При сочетании `setup --plan-netcraze` с `--apply` непосредственно перед confirmation или первым `--yes` mutation печатается явный блок: Netcraze plan — только offline preview и исключён из RouterKit apply. Cancellation и final summary сохраняют эту границу.
