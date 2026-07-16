# Аппаратный контракт политик Netcraze

Текущий вердикт: `SOFTWARE_PLAN_CORE_READY_HARDWARE_WRITE_CONTRACT_PENDING`.

`scripts/probe-netcraze-policy-contract.sh` инертен: только help и печать статуса. Нельзя добавлять туда непроверенные команды.

Остановиться при неожиданной модели/прошивке, неполном backup, неоднозначной default policy, выдаче чужих секретов, отсутствии revision/preconditions, несовпадении inventory, проблеме auth, неоднозначности name/ID, изменении default policy, ошибке verification или сомнительном rollback. Сырые данные хранить только локально с owner-only доступом и не публиковать.

## Фаза A — read-only

За ограниченное время подтвердить модель/firmware, роль администратора, доступный CLI/API, структуры Proxy/Policy/assignment/default, стабильность ID и имён, revision token, export/backup и соответствие Web UI. Отдельно проверить, не возвращает ли выбранный read-интерфейс credentials или лишнюю конфигурацию.

## Фаза B — только явно разрешённый disposable canary

Создать один синтетический connection, проверить и удалить; затем одну policy, проверить и удалить. Assignment допускается только для расходного устройства после подтверждения контракта: проверить и восстановить точную прежнюю привязку. До и после каждого шага доказать неизменность default policy.

Каждая мутация требует свежего snapshot/revision, проверенного backup, точных preconditions, одной операции, read-back verification, обратной операции и сравнения unrelated objects. Первый сбой прекращает дальнейшие стадии.

Публичные evidence могут содержать только family/firmware, capability/result categories, время, counts, редактированные semantic hashes, `default-policy-unchanged`, rollback result и ограничения. Нельзя публиковать credentials/cookies, raw output, backup, реальные names/IP/MAC/IDs и локальные пути.

Cleanup: восстановить/удалить assignment; удалить disposable policy и connection; сравнить default и unrelated objects; подтвердить согласованность running/saved config; сохранить приватный backup до read-back; любой неполный cleanup блокирует #16.
