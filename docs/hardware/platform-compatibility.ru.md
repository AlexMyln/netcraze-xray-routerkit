# Совместимость платформ и прошивок RouterKit

Дата проверки: **2026-08-23**. Машиночитаемый источник: [`hardware/routerkit-platform-compatibility.v1.json`](../../hardware/routerkit-platform-compatibility.v1.json).

## 1. Область и смысл совместимости

Это public-source gate платформ RouterKit. Исследование начинается с полного текущего каталога роутеров Netcraze, каталога Keenetic с фильтрацией только после инвентаризации USB/package-кандидатов и объективно ограниченного набора недавних моделей для вторичного рынка. Положительный класс означает только совпадение статических предпосылок. Он не разрешает доступ к роутеру, установку или записи policy.

В каталоге Netcraze найдено 33 product entries: 26 роутеров вошли в инвентаризацию, семь ретрансляторов/точек доступа/аксессуаров исключены на границе каталога. Все 26 роутеров есть в матрице. В подробную текущую выборку Keenetic вошли 13 USB-capable или USB-ambiguous роутеров. Ограниченный legacy-набор содержит восемь моделей, выбранных по датированному механическому правилу из раздела 8; это не заявление о перечислении всех исторических моделей.

## 2. Source-reviewed и hardware-validated

`primary_canary_target` и `strong_candidate` означают только source review. Все модели имеют статус `not_hardware_tested`.

```text
hardware_validated=false
live_contract_confirmed=false
```

Поддержка vendor не равна проверке RouterKit. Source review не подтверждает live read/write contract. Только hardware P0–P4 может подтвердить фактическую модель, прошивку, архитектуру, mount, Entware feed, authentication, schemas, default policy, save timing и rollback.

Manifest связывает факты через стабильные source ID. Каждая запись источника объявляет строгий kind, точный scope моделей и типизированные facts; `evidence` каждой модели ссылается на эти ID. Валидатор в обе стороны проходит цепочку binding модели → source ID → source record → точная модель → тип факта. Product page или источник sibling-модели не может заменить architecture matrix, а несвязанный authoritative source отклоняется и не считается декоративной библиографией.

## 3. Обязательные предпосылки

Текущий RouterKit требует:

- общий USB storage, а не modem-only USB;
- стабильный EXT4-накопитель с Entware в `/opt` и рабочим OPKG;
- Linux userspace `aarch64`/`arm64`, соответствующий закреплённому `Xray-linux-arm64-v8a.zip`;
- `/opt/sbin/xray`, `/opt/etc/init.d/S23xray-direct` и loopback listeners `127.0.0.1:1082`–`1084`;
- CLI по SSH или TELNET и backup/recovery конфигурации;
- достаточную свободную RAM для базовой нагрузки роутера, Python и Xray;
- явный model/firmware review до hardware P4.

Само наличие CLI, SSH или `/rci` не доказывает live schema RouterKit. Реализация #21 остаётся read-only/fixture-first, #15 — offline-only.

## 4. Политика USB 2.0 и USB 3.x

USB 3.x предпочтителен по скорости, но не обязателен. USB 2.0 проходит storage dimension, если vendor документирует общий storage, EXT4 и OPKG/Entware. Viva NC-1913, Skipper KN-1913 и Skipper DSL KN-2112 отклонены из-за MIPSel/MIPS, а не USB 2.0. Extra, Carrier, 4G, Launcher и Speedster DSL отклонены потому, что официальная матрица описывает только USB-модемы без общего storage/OPKG.

## 5. Основная цель hardware canary

**Netcraze Hopper 4G+ NC-2312** остаётся основной целью: 512 MB RAM, общий USB 3.0 storage, EXT4, OPKG, CLI/SSH, recovery и официальное AArch64-сопоставление. Он также сохраняет packet с baseline alpha.16, выпущенный в alpha.17. Это решение о непрерывности evidence, а не утверждение об уникальности или эквивалентности KN-2312.

Первый canary должен использовать текущий vendor Main, записать точную наблюдаемую прошивку/build и остановиться на P4 при расхождении с последним source review.

## 6. Полная матрица Netcraze

| Модель | RAM | USB/storage | Архитектура / Xray | Класс | Причина |
| --- | ---: | --- | --- | --- | --- |
| Ultra NC-1812 | 1024 MB | USB 3.2+2.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Комфортные ресурсы, полный static path |
| Hero 5G NC-4110 | 512 MB | USB 3.0+2.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Полный static path |
| Giga NC-1012 | 512 MB | USB 3.0+2.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Полный static path |
| Hopper 4G+ NC-2312 | 512 MB | USB 3.0, EXT4, OPKG | AArch64 / match | `primary_canary_target` | Исторический packet и полный static path |
| Hopper DSL NC-3611 | 512 MB | USB 3.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Полный static path |
| Hopper SE NC-3812 | 512 MB | USB 3.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Полный static path |
| Hopper NC-3811 | 512 MB | USB 3.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Полный static path |
| Challenger SE NC-3911 | 512 MB | нет | unknown / no match | `unsupported_current_routerkit_path` | Нет USB `/opt` storage |
| Challenger NC-3910 | 512 MB | нет | unknown / no match | `unsupported_current_routerkit_path` | Нет USB `/opt` storage |
| Sprinter SE NC-3712 | 512 MB | нет | unknown / no match | `unsupported_current_routerkit_path` | Нет USB `/opt` storage |
| Sprinter NC-3711 | 512 MB | нет | unknown / no match | `unsupported_current_routerkit_path` | Нет USB `/opt` storage |
| Racer NC-4010 | unknown | нет | unknown / no match | `unsupported_current_routerkit_path` | Нет USB `/opt` storage |
| Speedster 4G+ NC-2911 | unknown | нет внешнего storage | unknown / no match | `unsupported_current_routerkit_path` | Нет общего USB storage |
| Speedster DSL NC-2113 | 128 MB | USB 2.0 modem-only | unknown / no match | `unsupported_current_routerkit_path` | Нет storage/OPKG; 128 MB |
| Viva NC-1913 | 256 MB | USB 2.0, EXT4, OPKG | MIPSel / mismatch | `unsupported_current_routerkit_path` | Storage проходит, архитектура bootstrap — нет |
| Speedster NC-3013 | unknown | нет | unknown / no match | `unsupported_current_routerkit_path` | Нет USB `/opt` storage |
| Explorer 4G NC-4910 | unknown | нет внешнего storage | unknown / no match | `unsupported_current_routerkit_path` | Нет общего USB storage |
| Extra NC-1714 | 128 MB | USB 2.0 modem-only | unknown / no match | `unsupported_current_routerkit_path` | Нет storage/OPKG; 128 MB |
| Carrier NC-1721 | 128 MB | USB 2.0 modem-only | unknown / no match | `unsupported_current_routerkit_path` | Нет storage/OPKG; 128 MB |
| Air NC-1613 | unknown | нет | unknown / no match | `unsupported_current_routerkit_path` | Нет USB `/opt` storage |
| Explorer NC-1621 | unknown | нет | unknown / no match | `unsupported_current_routerkit_path` | Нет USB `/opt` storage |
| Runner 4G NC-2212 | unknown | нет внешнего storage | unknown / no match | `unsupported_current_routerkit_path` | Нет общего USB storage |
| Netcraze 4G NC-1213 | 128 MB | USB 2.0 modem-only | unknown / no match | `unsupported_current_routerkit_path` | Нет storage/OPKG; 128 MB |
| Launcher NC-1221 | 128 MB | USB 2.0 modem-only | unknown / no match | `unsupported_current_routerkit_path` | Нет storage/OPKG; 128 MB |
| Start NC-1112 | unknown | нет | unknown / no match | `unsupported_current_routerkit_path` | Нет USB `/opt` storage |
| Starter NC-1121 | unknown | нет | unknown / no match | `unsupported_current_routerkit_path` | Нет USB `/opt` storage |

Buddy 4/5/6/6 SE, Stellar 6, Orbiter 6 и PoE adapter исключены как non-router products, а не как скрытые кандидаты.

## 7. Полная релевантная матрица Keenetic

| Модель | RAM | USB/storage | Архитектура / Xray | Класс | Причина |
| --- | ---: | --- | --- | --- | --- |
| Titan SE KN-4210 | 1024 MB | USB 3.2+2.0, NVMe | userspace unknown | `insufficient_evidence` | Coming soon; feed/userspace не доказаны |
| Titan KN-1812 | 1024 MB | USB 3.2+2.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Полный static path |
| Hero 5G KN-4110 | 512 MB | USB 3.0+2.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Полный static path |
| Hero KN-1012 | 512 MB | USB 3.0+2.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Полный static path |
| Hopper 4G+ KN-2312 | 512 MB | USB 3.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Нет заявления equivalence с NC-2312 |
| Hero 4G+ KN-2311 | 256 MB | USB 3.0, EXT4, OPKG | MIPSel / mismatch | `unsupported_current_routerkit_path` | Arm64 bootstrap mismatch |
| Hopper KN-3811 | 512 MB | USB 3.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Полный static path |
| Hopper DSL KN-3610 | 256 MB | USB 3.0, EXT4, OPKG | MIPS / mismatch | `unsupported_current_routerkit_path` | Arm64 bootstrap mismatch |
| Hopper SE KN-3812 | 512 MB | USB 3.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Полный static path |
| Speedster DSL KN-2113 | 128 MB | USB 2.0 modem-only | unknown / no match | `unsupported_current_routerkit_path` | Нет storage/OPKG; 128 MB |
| Skipper DSL KN-2112 | 256 MB | USB 2.0, EXT4, OPKG | MIPS / mismatch | `unsupported_current_routerkit_path` | Arm64 bootstrap mismatch |
| Skipper KN-1913 | 256 MB | USB 2.0, EXT4, OPKG | MIPSel / mismatch | `unsupported_current_routerkit_path` | Arm64 bootstrap mismatch |
| Carrier KN-1721 | 128 MB | USB 2.0 modem-only | unknown / no match | `unsupported_current_routerkit_path` | Нет storage/OPKG; 128 MB |

## 8. Recent legacy / вторичный рынок

Политика `routerkit-recent-legacy-v1` проверена **2026-08-23** с трёхлетней границей retrieval evidence **2023-08-23**. Модель входит в этот ограниченный evaluation set только при выполнении всех машиночитаемых условий: её нет в текущем каталоге; first-party package matrix называет точную модель; зафиксированы общий USB storage и OPKG relevance; firmware family относится к текущему поколению KeeneticOS; qualifying evidence получено в пределах cutoff window. Правило задаёт воспроизводимый used-market review set, а не математическую полноту всей исторической линейки.

| Модель | Архитектура | Результат |
| --- | --- | --- |
| Peak KN-2710 | AArch64 | `research_candidate`: проверить RAM/lifecycle и `/opt` persistence |
| Titan KN-1811 | AArch64 | `research_candidate`: есть 5.1 evidence, но point/resources проверить перед покупкой |
| Giga KN-1011 | MIPSel | `unsupported_current_routerkit_path` |
| Ultra KN-1810 | MIPSel | `unsupported_current_routerkit_path` |
| Viva KN-1912 | MIPSel | `unsupported_current_routerkit_path` |
| Hero 4G KN-2310 | MIPSel | `unsupported_current_routerkit_path` |
| Skipper 4G KN-2910 | MIPSel | `unsupported_current_routerkit_path` |
| Hopper KN-3810 | MIPSel | `unsupported_current_routerkit_path`, несмотря на Preview 5.1 |

## 9. Совместимость архитектуры и Xray artifact

| Token RouterKit | Upstream artifact | Протестирован repo | Официальные совпадения | Unknown |
| --- | --- | --- | --- | --- |
| `linux-arm64` (`aarch64`, `arm64`) | `Xray-linux-arm64-v8a.zip`, Xray `v26.3.27` | да | NC-1812/4110/1012/2312/3611/3812/3811; KN-1812/4110/1012/2312/3811/3812; legacy KN-2710/1811 | KN-4210 и все без официальной ISA mapping |

MIPS/MIPSel не поддерживаются. Новая архитектура не добавлена: она потребовала бы отдельный pinned artifact, checksum, strict mapping, tests и hardware resource review.

Совпадающие суффиксы не доказывают equivalence. Пары NC/KN-2312, -1012, -3811, -3812 и -4110 остаются `insufficient_evidence` до first-party доказательства board/platform/package/firmware relationship.

## 10. RAM и ресурсы

Bootstrap stream-записывает bounded 128 MiB archive на диск и извлекает один bounded candidate; весь архив не буферизуется в RAM. Измеренного Xray RSS для этих роутеров в repo нет, поэтому универсальный порог не выдумывается.

- 1024/512 MB: `comfortable`, всё равно измерить на P1.
- 256 MB: `borderline_requires_hardware_measurement`; в 5.1 статистика приложений по умолчанию отключена ниже 256 MB.
- 128 MB: `insufficient` без отдельного сильного hardware proof; все текущие 128 MB модели также проваливают storage/architecture.

## 11. Политика firmware channels

1. На hardware preflight записать точные display version и build string.
2. Для первого canary предпочесть текущий vendor Main.
3. Preview — только явный opt-in с меткой Preview.
4. Отличие от последнего source review требует явного P4 decision.
5. Source review не равен live contract.
6. Exact runtime schemas проверяются только hardware P0–P4.

## 12. Текущие Main и Preview

Для NC-2312 на дату review:

| Смысл | Display version | Exact build | Статус |
| --- | --- | --- | --- |
| Исторический packet с baseline alpha.16 | `5.00.C.12.0-0` | `5.00.C.12.0-0` | immutable packet, выпущенный в alpha.17 |
| Текущий Main | `5.1.3` | unknown | 2026-08-10, рекомендуемый канал |
| Текущий Preview | `5.1.4` | unknown | 2026-08-18, deliberate opt-in |
| Последний source review | `5.1.4` Preview notes | unknown | только public-source review |
| Фактический canary | не наблюдался | не наблюдался | поле hardware preflight |

Для 5.1.3/5.1.4 exact build оставлен `null`; mapping вида `5.01.C.*` не выдумывается.

## 13. Анализ совместимости 5.1.3

Вердикт: **`NO_SOURCE_LEVEL_BLOCKER`**. Это не hardware verdict.

- 5.1.1 добавляет Storages & Devices, CLI filesystem check/format, показ policy/traffic/IPv6 в Client Lists, USB stability fixes и новую навигацию Wi-Fi/Segments.
- 5.1.2 Preview улучшает timing назначения connection policy при startup/bulk operations.
- 5.1.3 Main включает это улучшение и исправляет отображение client connection, DNS/DDNS/Web UI.
- 5.1.4 Preview дополнительно меняет Wi-Fi Monitor, Segments и traffic UI.

Для #21 read candidates остаются, UI лучше показывает policy, но exact 5.1 JSON/RCI schema, identity joins, auth и sensitive spillover не опубликованы. Live adapter остаётся disabled.

Для #15 assignment существует и timing улучшен, но revisions, atomicity, ownership, preconditions, save/apply и rollback не доказаны. Fixture-first planner остаётся верным; writes остаются hardware-gated.

Для #16 checklist должен ссылаться на **Wi-Fi settings**, **Segments**, **Client Lists**, **Storages & Devices**. P4 отдельно фиксирует model/channel/display/build и выбирает existing contract, narrow off-device patch или stop.

## 14. Повышение статуса модели

`strong_candidate` требует primary evidence с точным model scope для identity, hardware/resources, общего storage, EXT4, OPKG/Entware, поддерживаемой userspace architecture, management/recovery и lifecycle. Централизованный eligibility predicate также требует 512 или 1024 MB RAM с оценкой `comfortable`, CLI/SSH, проходящих storage и management prerequisites, не-unsupported lifecycle и отсутствия нерешённого static blocking reason. Любая записанная версия Main или Preview должна быть связана с release source для точной модели и канала. `primary_canary_target` назначается только отдельным review. Hardware validation появляется только после полного canary. Unknown model/architecture/firmware всегда fail closed.

## 15. Hardware status и shortlist

Все модели: `not_hardware_tested`.

- **BEST TARGET:** Netcraze Hopper 4G+ NC-2312.
- **GOOD ALTERNATIVES:** Netcraze Ultra NC-1812, Hero 5G NC-4110, Giga NC-1012, Hopper DSL NC-3611, Hopper SE NC-3812, Hopper NC-3811; Keenetic Titan KN-1812, Hero 5G KN-4110, Hero KN-1012, Hopper 4G+ KN-2312, Hopper KN-3811, Hopper SE KN-3812.
- **ACCEPTABLE FOR RESEARCH:** Titan SE KN-4210 после proof userspace/feed; used Peak KN-2710 и Titan KN-1811 после lifecycle/resource recheck.
- **DO NOT BUY FOR THIS PROJECT:** все MIPS/MIPSel и все no-storage/modem-only USB модели без отдельного architecture/storage design.

## 16. Официальная библиография

Все источники получены 2026-08-23:

Source registry в manifest является authoritative для machine validation: source ID и URL уникальны, fact types перечислены enum-ом, model scopes точны, каждый non-catalog source связан хотя бы с одним фактом модели, и только явно помеченные catalog-boundary records могут не входить в model evidence map.

- [полный каталог Netcraze](https://netcraze.ru/ru/products);
- [полный каталог Keenetic](https://keenetic.com/en/products), [routers](https://keenetic.com/en/products/routers), [DSL routers](https://keenetic.com/en/products/dsl-routers);
- individual first-party product pages из каталогов;
- [Netcraze OPKG/Asterisk architecture matrix](https://support.netcraze.ru/viva/nc-1913/ru/69806-installation-of-the-asterisk-ip-pbx-opkg-package.html) и [Keenetic matrix](https://support.keenetic.com/hero-5g/kn-4110/en/69806-installation-of-the-asterisk-ip-pbx-opkg-package.html);
- NC-2312 [Main](https://support.netcraze.ru/hopper-4g-plus/nc-2312/ru/46907-latest-main-release.html), [Preview](https://support.netcraze.ru/hopper-4g-plus/nc-2312/ru/46988-latest-preview-release.html), [lifecycle](https://support.netcraze.ru/hopper-4g-plus/nc-2312/en/31171-product-lifecycle-support-policy.html), [download/recovery](https://support.netcraze.ru/hopper-4g-plus/nc-2312/en/50341-download-center.html);
- KN-2312 [Main](https://support.keenetic.com/hopper-4g-plus/kn-2312/en/41380-latest-main-release.html) и [Preview](https://support.keenetic.com/hopper-4g-plus/kn-2312/en/41167-latest-preview-release.html);
- [KeeneticOS 4.0 CLI guide](https://docs.help.keenetic.com/cli/4.0/en/cli_manual_kn-1011.pdf) — CLI/read/policy/save/fail-safe/`rci`, но не proof exact 5.1 live schemas.

Reseller, marketplace, scraped-spec, случайные forum posts и AI summaries не используются для классификации.
