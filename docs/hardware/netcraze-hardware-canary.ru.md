# Runbook аппаратного canary для Netcraze

Репозиторный/offline вердикт: `READY_FOR_HARDWARE_CANARY`.

Это основной операторский порядок для ограниченного окна с реальным Netcraze/Keenetic. Дополнительные материалы:

- [read-only packet #21](device-discovery-probe.ru.md);
- [write-contract packet #15](netcraze-policy-contract.ru.md);
- [машиночитаемый packet](../../hardware/netcraze-canary-packet.v1.json);
- [печатный checklist](netcraze-canary-checklist.ru.md).

## 1. Назначение и что не заявляется

В `v0.2.0-alpha.16` готовы fixture-first ядро discovery #21 и fixture-first planner connection/policy/optional assignment #15. Реальные read/write contracts пока неизвестны.

`READY_FOR_HARDWARE_CANARY` означает, что без устройства подготовлены repository, phase graph, time budget, evidence model, stop conditions, rollback, cleanup и matrix #16. Это не означает:

- hardware validation;
- подтверждённый live management interface;
- доказанный disposable write;
- доказанный reboot/recovery;
- наличие live adapter;
- hardware-tested one-command, beta или production readiness.

Packet не добавляется в обычный `routerkit setup`. Validator и consolidated probe работают только offline.

## 2. Плановая цель и mismatch

Плановая первая цель:

- Netcraze Hopper 4G+ NC-2312;
- firmware `5.00.C.12.0-0`;
- `aarch64`;
- вручную подготовленный EXT4 USB;
- Entware под `/opt`.

Все значения имеют статус `expected_unverified`. Наблюдаемые значения записываются отдельно в private manifest. При любом несовпадении model/firmware/architecture/storage:

1. остановить forward progress;
2. публично записать только sanitized mismatch category;
3. сохранить фактические значения только private;
4. не расширять supported scope автоматически;
5. перейти к cleanup/device return.

## 3. Инварианты

- Никогда не вызывать `xkeen -start`.
- Не добавлять TPROXY, REDIRECT или firewall rules.
- Не менять default/global policy.
- Не перезаписывать unrelated connections, policies, assignments или configuration.
- Не выбирать устройство неявно и не назначать его только по IP.
- Не публиковать credentials, backups, startup configuration, device inventory, MAC, subscription material, UUID, Reality values или private hostnames.
- Xray listeners остаются только на `127.0.0.1:1082`, `:1083`, `:1084`.
- Expected runtime: `/opt/sbin/xray` и `/opt/etc/init.d/S23xray-direct`.
- `S24xray` остаётся disabled.
- Каждый write требует observed state, explicit authorization, backup/export, preconditions, readback и rollback.
- Остановка на первой ошибке.
- Никаких hardware-tested claims до прохождения #16.

## 4. Prerequisites оператора

- физический доступ или owner-authorized local admin;
- spare/disposable/recoverable устройство без production-critical зависимости;
- стабильное питание и local LAN;
- доступный backup/export и понятный manual recovery;
- USB уже вручную отформатирован;
- Entware prerequisite state известен;
- clean checkout `v0.2.0-alpha.16` на `c8f697635c93584e85e76a1d734f8fa797a76b51`;
- offline-копии runbook, packet, schema и checklist;
- private evidence directory вне repository;
- окно не больше 120 минут;
- полномочия на rollback, reboot, cleanup и возврат устройства.

При сомнении не начинать hardware window.

## 5. Time budget

Hard ceiling — 120 минут. В packet v1 phase hard timeouts равны phase budgets; route validation доказывает, что normal route with/without P7, read-contract stop-and-cleanup и patch stop-and-cleanup помещаются в ceiling.

| Интервал | Phase | Бюджет |
| --- | --- | ---: |
| 0–5 | P0 preflight | 5 |
| 5–10 | P1 platform inventory | 5 |
| 10–20 | P2 read contract #21 | 10 |
| 20–30 | P3 read contract #15 | 10 |
| 30–35 | P4 compatibility decision | 5 |
| 35–45 | P5 disposable connection | 10 |
| 45–55 | P6 disposable policy | 10 |
| 55–60 | P7 optional assignment | 5 |
| 60–75 | P8 full alpha.16 software path | 15 |
| 75–80 | P9 rerun/profile update | 5 |
| 80–90 | P10 failures/rollback | 10 |
| 90–100 | P11 reboot/recovery | 10 |
| 100–105 | P12 invariant audit | 5 |
| 105–120 | P13 cleanup/return | 15 |

Минимум 15 минут всегда резервируются для P13. Reentry после narrow patch допустим только при остатке не менее 30 минут. Write и reboot не начинаются внутри cleanup reserve.

Canonical phase IDs:

```text
P0_OPERATOR_PREFLIGHT
P1_READ_ONLY_PLATFORM_INVENTORY
P2_READ_ONLY_DEVICE_DISCOVERY_CONTRACT
P3_READ_ONLY_POLICY_CONTRACT
P4_OFF_DEVICE_COMPATIBILITY_DECISION
P5_DISPOSABLE_CONNECTION_CANARY
P6_DISPOSABLE_POLICY_CANARY
P7_OPTIONAL_DISPOSABLE_ASSIGNMENT_CANARY
P8_FULL_ROUTERKIT_INSTALL_CANARY
P9_IDEMPOTENT_RERUN
P10_FAILURE_AND_ROLLBACK
P11_REBOOT_AND_RECOVERY
P12_FINAL_INVARIANT_AUDIT
P13_CLEANUP_AND_DEVICE_RETURN
```

## 6. Offline gate

На workstation, не на router:

```sh
python3 scripts/routerkit-hardware-canary.py status
python3 scripts/routerkit-hardware-canary.py validate
python3 scripts/routerkit-hardware-canary.py matrix
```

Ожидается `READY_FOR_HARDWARE_CANARY` и одновременно:

```text
hardware_validated=false
live_contract_confirmed=false
```

Иной результат блокирует hardware session.

## 7. P0 — operator preflight

- [ ] authorization и rollback authority;
- [ ] устройство безопасно для теста;
- [ ] backup/recovery доступны;
- [ ] нет production dependency;
- [ ] exact release/tag/commit;
- [ ] evidence directory вне repository;
- [ ] directory `0700`, files `0600`;
- [ ] нет symlink/hardlink, default cloud sync и public terminal recording;
- [ ] cleanup reserve защищён.

Любой fail означает STOP.

## 8. P1 — read-only platform inventory

Private capture:

- model, firmware, architecture, kernel category;
- shell/tool availability;
- USB filesystem/mount;
- `/opt`, Entware;
- Xray presence/version category;
- init scripts и RouterKit artifacts;
- listener category;
- default-policy identity category;
- backup/export;
- management interface availability;
- authentication-mode category.

Vendor-specific command/resource остаётся только official documented candidate. Он рассматривается оператором по одному для фактической firmware и не попадает в автоматическую executable branch.

## 9. P2 — read contract #21

Нужно установить:

- DHCP binding schema;
- association schema;
- hotspot/client summary schema;
- corroborating ARP/equivalent;
- stable identity;
- source precedence и joins;
- online/offline/stale;
- policy visibility;
- duplicates;
- соответствие Web UI;
- equivalence доступных local interfaces;
- auth/error categories.

Хранить только минимальные private raw artifacts, необходимые для schema/cardinality/join/error shape.

Результаты: `pass`, `partial`, `fail`, `stop`. P2 никогда не даёт write authorization.

## 10. P3 — read-only часть contract #15

До write захватить:

- connection inventory и SOCKS5 representation;
- policy inventory и policy→connection references;
- device→policy references;
- однозначную default policy;
- IDs/names/uniqueness;
- ownership/description markers;
- revision/state/preconditions;
- save/commit category;
- backup/export;
- verification readback;
- rollback;
- Web UI correspondence.

Write запрещён, пока default policy неоднозначна, inventory inconsistent, нет stable identity/preconditions, backup или exact reverse operation.

## 11. P4 — compatibility decision

Выбрать ровно один результат:

```text
GO_WITH_EXISTING_ALPHA16_CONTRACT
OFF_DEVICE_NARROW_PATCH_REQUIRED
STOP_UNSUPPORTED_OR_AMBIGUOUS
```

Для `OFF_DEVICE_NARROW_PATCH_REQUIRED` router writes прекращаются. Patch выполняется только off-device по [template](netcraze-canary-compatibility-patch.ru.md), с synthetic fixture, focused/full tests, static guard, independent review и новым explicit authorization. На router patch не делается.

## 12. P5 — disposable connection

Только после GO, fresh state, backup и explicit authorization:

1. collision-check synthetic name;
2. создать один disposable SOCKS5 connection к loopback listener;
3. проверить exact semantic readback;
4. доказать unchanged default policy;
5. удалить connection;
6. проверить удаление и unrelated state.

При rollback uncertainty P6 не начинается.

## 13. P6 — disposable policy

После полного P5:

1. создать synthetic non-default policy;
2. сослаться только на disposable connection;
3. проверить exact readback;
4. доказать unchanged default/unrelated state;
5. удалить policy и connection;
6. проверить running/saved consistency.

## 14. P7 — optional assignment

Можно пропустить. Если выполняется:

- explicit disposable client;
- trusted MAC/stable identity;
- exact previous assignment;
- никогда не IP-only;
- не production-critical device;
- verify;
- restore exact prior assignment;
- verify default and unrelated assignments.

## 15. P8 — full RouterKit canary

После disposable connection/policy:

- prerequisite state;
- clean plan;
- explicit bootstrap/install;
- pinned Xray/checksum;
- config generation;
- loopback listeners;
- health checks;
- device discovery;
- offline Netcraze plan;
- generic egress result без secret output;
- default/unrelated audit.

Разделять:

```text
alpha.16 full software path validation
hardware-confirmed interface prototype validation
```

В alpha.16 нет live Netcraze apply adapter. Future adapter требует отдельного reviewed change и нового #16.

## 16. P9 — idempotent rerun

Проверить отсутствие duplicate config/connection/policy, exact reuse, отсутствие implicit assignment, unchanged default, stable listeners, отсутствие unrelated changes и bounded profile update с documented transaction boundary.

## 17. P10 — failure/rollback

Использовать только low-risk injection:

| Layer | Proof |
| --- | --- |
| planning | stop before mutation |
| bootstrap precondition | stop before replacement |
| router preflight | stop before backup и всех последующих mutations |
| backup gate | later writes blocked |
| install staging | restore backup или remove clean candidate |
| autostart | stop и preserve reviewed state |
| healthcheck | explicit recovery decision |
| disposable connection | object absent/restored |
| disposable policy | connection/policy restored |
| optional assignment | exact prior relationship restored |

После каждого failure — default/unrelated audit. Нельзя создавать destructive failure только ради matrix.

## 18. P11 — reboot/recovery

Reboot только после prior success, authorization, backup, stable power и достаточного cleanup reserve.

После reboot:

- `/opt` и storage;
- Xray process/binary;
- loopback listeners;
- `S23xray-direct`;
- `S24xray` disabled;
- отсутствие forbidden routing markers;
- setup state и rerun.

USB detach/reattach только если это безопасно и отдельно разрешено; иначе limitation.

## 19. P12 — final invariant audit

- default policy unchanged;
- unrelated connections/policies/assignments unchanged;
- firewall unchanged;
- нет TPROXY/REDIRECT/xkeen markers;
- listeners loopback-only;
- нет secret leakage;
- disposable objects удалены;
- temporary files удалены;
- unsupported target корректно остановлен;
- backup/evidence decision записан.

Fail запрещает `PASS_FULL_CANARY`.

## 20. P13 — cleanup/device return

P13 доступен после P0 независимо от места остановки:

- удалить disposable connections/policies/test users/temp artifacts;
- восстановить assignment или доказать, что его не было;
- проверить default/unrelated state;
- убрать stale lock/PID/temp evidence;
- проверить USB/services;
- записать final private hash;
- применить retention/secure disposal decision;
- сообщить owner ограничения;
- физически вернуть устройство.

Если cleanup не доказан: `FAILED_MANUAL_RECOVERY_REQUIRED`.

## 21. Evidence

Private manifest `routerkit.netcraze.hardware-evidence.v1` содержит только metadata/reference/size/SHA-256/sensitivity/retention/redaction/cleanup, но не raw contents.

Rules:

- directory `0700`;
- files `0600`;
- без symlink/hardlink;
- cloud sync off by default;
- вне repository;
- без secret terminal recording;
- без public issue attachment;
- checksum до sanitization;
- explicit retention или secure deletion.

Evidence-directory initializer намеренно не добавлен. Новый write-capable helper расширил бы attack/cleanup surface во время короткого окна; strict schema, permission checklist и созданная оператором private directory достаточны.

Public output создаётся только по [public template](netcraze-canary-public-evidence-template.ru.md). Redaction не равна anonymity.

## 22. Hardware-session verdict

```text
PASS_CONTRACT_CAPTURE_ONLY
PASS_DISPOSABLE_WRITE_CONTRACT
PASS_FULL_CANARY
PARTIAL_NEEDS_OFF_DEVICE_PATCH
FAILED_ROLLBACK_COMPLETE
FAILED_MANUAL_RECOVERY_REQUIRED
STOP_UNSUPPORTED
```

Documentation alone не переводит проект в `READ_CONTRACT_CONFIRMED`, `WRITE_CONTRACT_CONFIRMED` или `HARDWARE_CANARY_PASS`.
