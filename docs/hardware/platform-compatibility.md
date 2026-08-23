# RouterKit platform and firmware compatibility

Reviewed: **2026-08-23**. Machine-readable source: [`hardware/routerkit-platform-compatibility.v1.json`](../../hardware/routerkit-platform-compatibility.v1.json).

## 1. Scope and meaning of compatibility

This is the public-source platform gate for RouterKit. It starts from the complete current Netcraze router catalog, the current Keenetic catalog filtered only after inventory for plausible USB/package-capable routers, and a bounded recent used-market set. A favorable class means only that documented static prerequisites line up. It never authorizes router access, installation, policy writes, or a hardware claim.

The catalog pass found 33 current Netcraze product entries: 26 routers were inventoried and seven repeaters/access points/accessories were excluded at the catalog boundary. All 26 routers remain in the matrix. The Keenetic pass entered 13 current USB-capable or USB-ambiguous routers into detailed assessment. Eight recent legacy models were added because official package guidance documents storage/OPKG and architecture close enough to the current software generation to be realistic used-market options.

## 2. Source-reviewed versus hardware-validated

`primary_canary_target` and `strong_candidate` mean source-reviewed, not tested. Every model below is `not_hardware_tested`.

```text
hardware_validated=false
live_contract_confirmed=false
```

The vendor can support a model while RouterKit has not reviewed it. RouterKit can review source compatibility while the live read/write contract remains unknown. Hardware P0–P4 is the only path that can confirm the actual model, firmware, architecture, mount, Entware feed, authentication, schemas, default policy, save timing, and rollback.

## 3. Mandatory prerequisites

RouterKit currently requires:

- a general-purpose USB storage port, not a modem-only USB port;
- stable EXT4 storage with Entware mounted at `/opt` and working OPKG;
- Linux `aarch64`/`arm64` userspace matching the pinned `Xray-linux-arm64-v8a.zip` artifact;
- `/opt/sbin/xray` and `/opt/etc/init.d/S23xray-direct` with loopback listeners at `127.0.0.1:1082`–`1084`;
- CLI over SSH or TELNET plus configuration export/recovery;
- enough free RAM for the router workload, Python and Xray;
- explicit model and firmware review before hardware phase P4.

CLI, SSH and `/rci` existing do not establish RouterKit's live schema. The implementation remains read-only/fixture-first for #21 and offline-only for #15.

## 4. USB 2.0 and USB 3.x policy

USB 3.x is preferred for throughput but is not mandatory. USB 2.0 passes the storage dimension when the vendor documents general storage, EXT4 and OPKG/Entware. Netcraze Viva NC-1913, Keenetic Skipper KN-1913 and Skipper DSL KN-2112 demonstrate that USB 2.0 is not itself a rejection; they fail the current execution path because their documented MIPSel/MIPS userspace does not match the arm64 artifact. Extra, Carrier, 4G, Launcher and Speedster DSL fail because their official feature matrices describe USB modem use without general storage/OPKG—not because the port is USB 2.0.

## 5. Primary hardware-canary target

**Netcraze Hopper 4G+ NC-2312** remains the primary target. It has 512 MB RAM, general USB 3.0 storage, EXT4, OPKG, CLI/SSH, configuration recovery and an official AArch64 package mapping. It also preserves the alpha.16-baseline packet shipped in the alpha.17 release. This is a continuity and evidence decision, not a claim that it is uniquely capable or hardware-equivalent to KN-2312.

The first canary should use the current vendor Main channel, record the exact observed firmware/build, and stop at P4 if the actual contract differs from the last source review.

## 6. Complete Netcraze compatibility matrix

All 26 routers listed in the official current catalog are included. Architecture is `unknown` unless first-party package guidance proves it.

| Model | RAM | USB/storage | Architecture / Xray | Class | Deciding reason |
| --- | ---: | --- | --- | --- | --- |
| Ultra NC-1812 | 1024 MB | USB 3.2+2.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Comfortable resources and complete static path |
| Hero 5G NC-4110 | 512 MB | USB 3.0+2.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Complete static path |
| Giga NC-1012 | 512 MB | USB 3.0+2.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Complete static path |
| Hopper 4G+ NC-2312 | 512 MB | USB 3.0, EXT4, OPKG | AArch64 / match | `primary_canary_target` | Historical packet continuity plus complete static path |
| Hopper DSL NC-3611 | 512 MB | USB 3.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Complete static path |
| Hopper SE NC-3812 | 512 MB | USB 3.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Complete static path |
| Hopper NC-3811 | 512 MB | USB 3.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Complete static path |
| Challenger SE NC-3911 | 512 MB | none | unknown / no match | `unsupported_current_routerkit_path` | No USB `/opt` storage path |
| Challenger NC-3910 | 512 MB | none | unknown / no match | `unsupported_current_routerkit_path` | No USB `/opt` storage path |
| Sprinter SE NC-3712 | 512 MB | none | unknown / no match | `unsupported_current_routerkit_path` | No USB `/opt` storage path |
| Sprinter NC-3711 | 512 MB | none | unknown / no match | `unsupported_current_routerkit_path` | No USB `/opt` storage path |
| Racer NC-4010 | unknown | none | unknown / no match | `unsupported_current_routerkit_path` | No USB `/opt` storage path |
| Speedster 4G+ NC-2911 | unknown | no external storage port | unknown / no match | `unsupported_current_routerkit_path` | No general USB storage path |
| Speedster DSL NC-2113 | 128 MB | USB 2.0 modem-only | unknown / no match | `unsupported_current_routerkit_path` | No documented storage/OPKG; 128 MB |
| Viva NC-1913 | 256 MB | USB 2.0, EXT4, OPKG | MIPSel / mismatch | `unsupported_current_routerkit_path` | Storage passes; current bootstrap architecture fails |
| Speedster NC-3013 | unknown | none | unknown / no match | `unsupported_current_routerkit_path` | No USB `/opt` storage path |
| Explorer 4G NC-4910 | unknown | no external storage port | unknown / no match | `unsupported_current_routerkit_path` | No general USB storage path |
| Extra NC-1714 | 128 MB | USB 2.0 modem-only | unknown / no match | `unsupported_current_routerkit_path` | No storage/OPKG; 128 MB |
| Carrier NC-1721 | 128 MB | USB 2.0 modem-only | unknown / no match | `unsupported_current_routerkit_path` | No storage/OPKG; 128 MB |
| Air NC-1613 | unknown | none | unknown / no match | `unsupported_current_routerkit_path` | No USB `/opt` storage path |
| Explorer NC-1621 | unknown | none | unknown / no match | `unsupported_current_routerkit_path` | No USB `/opt` storage path |
| Runner 4G NC-2212 | unknown | no external storage port | unknown / no match | `unsupported_current_routerkit_path` | No general USB storage path |
| Netcraze 4G NC-1213 | 128 MB | USB 2.0 modem-only | unknown / no match | `unsupported_current_routerkit_path` | No storage/OPKG; 128 MB |
| Launcher NC-1221 | 128 MB | USB 2.0 modem-only | unknown / no match | `unsupported_current_routerkit_path` | No storage/OPKG; 128 MB |
| Start NC-1112 | unknown | none | unknown / no match | `unsupported_current_routerkit_path` | No USB `/opt` storage path |
| Starter NC-1121 | unknown | none | unknown / no match | `unsupported_current_routerkit_path` | No USB `/opt` storage path |

The catalog also lists Buddy 4/5/6/6 SE repeaters, Stellar 6 and Orbiter 6 access points, and a PoE adapter. They are outside the router universe, not silently rejected candidate routers.

## 7. Complete relevant Keenetic compatibility matrix

The Keenetic catalog was not limited to mobile models. These are all current USB-capable or storage-ambiguous routers found across the main, mobile and DSL categories.

| Model | RAM | USB/storage | Architecture / Xray | Class | Deciding reason |
| --- | ---: | --- | --- | --- | --- |
| Titan SE KN-4210 | 1024 MB | USB 3.2+2.0, NVMe, storage/OPKG | userspace unknown | `insufficient_evidence` | Coming soon; userspace/Entware feed not proven |
| Titan KN-1812 | 1024 MB | USB 3.2+2.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Comfortable complete static path |
| Hero 5G KN-4110 | 512 MB | USB 3.0+2.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Complete static path |
| Hero KN-1012 | 512 MB | USB 3.0+2.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Complete static path |
| Hopper 4G+ KN-2312 | 512 MB | USB 3.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Complete static path; no NC-2312 equivalence claim |
| Hero 4G+ KN-2311 | 256 MB | USB 3.0, EXT4, OPKG | MIPSel / mismatch | `unsupported_current_routerkit_path` | Current arm64 bootstrap mismatch |
| Hopper KN-3811 | 512 MB | USB 3.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Complete static path |
| Hopper DSL KN-3610 | 256 MB | USB 3.0, EXT4, OPKG | MIPS / mismatch | `unsupported_current_routerkit_path` | Current arm64 bootstrap mismatch |
| Hopper SE KN-3812 | 512 MB | USB 3.0, EXT4, OPKG | AArch64 / match | `strong_candidate` | Complete static path |
| Speedster DSL KN-2113 | 128 MB | USB 2.0 modem-only | unknown / no match | `unsupported_current_routerkit_path` | No documented storage/OPKG; 128 MB |
| Skipper DSL KN-2112 | 256 MB | USB 2.0, EXT4, OPKG | MIPS / mismatch | `unsupported_current_routerkit_path` | Current arm64 bootstrap mismatch |
| Skipper KN-1913 | 256 MB | USB 2.0, EXT4, OPKG | MIPSel / mismatch | `unsupported_current_routerkit_path` | Current arm64 bootstrap mismatch |
| Carrier KN-1721 | 128 MB | USB 2.0 modem-only | unknown / no match | `unsupported_current_routerkit_path` | No storage/OPKG; 128 MB |

## 8. Recent legacy and used-market candidates

The cutoff retains recent models named by official OPKG architecture guidance and having general storage. It excludes ancient hardware without a documented current-generation package path or realistic lifecycle evidence.

| Model | Architecture | Result |
| --- | --- | --- |
| Peak KN-2710 | AArch64 | `research_candidate`: artifact matches, but exact RAM/lifecycle and `/opt` persistence must be rechecked |
| Titan KN-1811 | AArch64 | `research_candidate`: current 5.1 support evidence exists; exact point/resources need purchase-time recheck |
| Giga KN-1011 | MIPSel | `unsupported_current_routerkit_path`: arm64 bootstrap mismatch |
| Ultra KN-1810 | MIPSel | `unsupported_current_routerkit_path`: arm64 bootstrap mismatch |
| Viva KN-1912 | MIPSel | `unsupported_current_routerkit_path`: arm64 bootstrap mismatch |
| Hero 4G KN-2310 | MIPSel | `unsupported_current_routerkit_path`: arm64 bootstrap mismatch |
| Skipper 4G KN-2910 | MIPSel | `unsupported_current_routerkit_path`: arm64 bootstrap mismatch |
| Hopper KN-3810 | MIPSel | `unsupported_current_routerkit_path`: arm64 bootstrap mismatch even though Preview 5.1 exists |

## 9. Architecture and Xray artifact compatibility

| RouterKit token | Upstream artifact | Repository tested | Official model matches | Unknown mapping |
| --- | --- | --- | --- | --- |
| `linux-arm64` (`aarch64`, `arm64`) | `Xray-linux-arm64-v8a.zip` pinned at Xray `v26.3.27` | yes, fixtures/unit tests | NC-1812/4110/1012/2312/3611/3812/3811; KN-1812/4110/1012/2312/3811/3812; legacy KN-2710/1811 | KN-4210 and all models whose official package matrix does not name an ISA |

MIPS and MIPSel are not supported. No new architecture was added because doing so would require a separate pinned upstream artifact, checksum, strict mapping, tests and hardware-relevant resource review.

Matching suffixes are not equivalence evidence. NC-2312/KN-2312, NC-1012/KN-1012, NC-3811/KN-3811, NC-3812/KN-3812 and NC-4110/KN-4110 remain `insufficient_evidence` as alias pairs until a first-party source proves the board/platform and package/firmware relationship.

## 10. RAM and resource assessment

The bootstrap streams a bounded 128 MiB archive to disk, extracts one bounded candidate and requires Python plus Xray under `/opt`; it does not hold the whole archive in RAM. The repository does not publish a measured Xray RSS on these routers, so no invented universal threshold is used.

- 1 GB and 512 MB: `comfortable` for static screening, still measured at P1.
- 256 MB: `borderline_requires_hardware_measurement`; vendor 5.1 also disables application traffic statistics below 256 MB by default, reinforcing the need for measurement.
- 128 MB: `insufficient` for this path without unusually strong hardware proof. All current 128 MB catalog models also fail storage or architecture evidence.

## 11. Firmware channel policy

1. Record the exact observed display version and exact build string during hardware preflight.
2. Prefer current vendor Main for the first canary unless a reviewed reason says otherwise.
3. Preview is explicit opt-in and must remain labeled Preview.
4. A version different from the last source-reviewed point requires an explicit P4 compatibility decision.
5. Source-reviewed compatibility never means live-contract confirmation.
6. Exact runtime schemas still require P0–P4 hardware validation.

## 12. Current Main and Preview firmware

For NC-2312 on the review date:

| Meaning | Display version | Exact build string | Status |
| --- | --- | --- | --- |
| Historical alpha.16-baseline packet | `5.00.C.12.0-0` | `5.00.C.12.0-0` | immutable packet shipped in alpha.17 |
| Current vendor Main | `5.1.3` | unknown | released 2026-08-10, recommended channel |
| Current vendor Preview | `5.1.4` | unknown | released 2026-08-18, deliberate opt-in only |
| Last source-reviewed point | `5.1.4` Preview notes | unknown | public-source review only |
| Actual canary firmware | not observed | not observed | hardware preflight field |

Exact low-level strings for 5.1.3/5.1.4 are deliberately `null`; the display version is not reverse-mapped into an invented `5.01.C.*` value.

## 13. Firmware 5.1.3 compatibility analysis

Verdict: **`NO_SOURCE_LEVEL_BLOCKER`**. This is not a hardware verdict.

The public 5.1 notes show that the relevant surfaces still exist and have evolved compatibly with RouterKit's fail-closed design:

- 5.1.1 adds a dedicated Storages & Devices page, CLI filesystem check/format commands, client policy/traffic/IPv6 visibility, improved USB-drive stability, and a reorganized Wi-Fi/Segments UI.
- 5.1.2 Preview improves connection-policy assignment timing during startup and bulk operations.
- 5.1.3 Main carries that assignment improvement and fixes displayed client connection information plus DNS/DDNS/Web UI issues.
- 5.1.4 Preview changes additional Wi-Fi Monitor, Segments and traffic UI details.

Impact on #21: documented CLI read candidates remain the public basis, and the UI now exposes connection policy more clearly. Public notes do not publish the exact 5.1 JSON/RCI schema, identity joins, authentication or sensitive spillover; the live adapter stays disabled.

Impact on #15: policy assignment still exists and timing improved, but no public source proves object revisions, atomicity, ownership, preconditions, exact save/apply behavior or rollback. The fixture-first planner remains correct; writes remain hardware-gated.

Impact on #16: the checklist must use current **Wi-Fi settings**, **Segments**, **Client Lists**, and **Storages & Devices** locations rather than stale navigation. P4 must record model, channel, display version and exact build separately, then choose existing contract, narrow off-device patch, or stop.

## 14. Promoting a new model

A model can move to `strong_candidate` only when primary sources prove general storage, EXT4, OPKG/Entware, an exact supported userspace architecture, adequate resources, management/recovery, lifecycle and separate Main/Preview information. It becomes `primary_canary_target` only by an explicit reviewed decision. It becomes hardware validated only after the full canary and evidence schema pass. Unknown model, architecture or firmware always stops automatic promotion.

## 15. Hardware validation status and purchase shortlist

All entries remain `not_hardware_tested`.

- **BEST TARGET:** Netcraze Hopper 4G+ NC-2312.
- **GOOD ALTERNATIVES:** Netcraze Ultra NC-1812, Hero 5G NC-4110, Giga NC-1012, Hopper DSL NC-3611, Hopper SE NC-3812, Hopper NC-3811; Keenetic Titan KN-1812, Hero 5G KN-4110, Hero KN-1012, Hopper 4G+ KN-2312, Hopper KN-3811, Hopper SE KN-3812.
- **ACCEPTABLE FOR RESEARCH:** Keenetic Titan SE KN-4210 only after shipping/userspace proof; used Peak KN-2710 and Titan KN-1811 after purchase-time lifecycle/resource verification.
- **DO NOT BUY FOR THIS PROJECT:** every listed MIPS/MIPSel model and every no-storage/modem-only USB model unless a separate reviewed architecture/storage design is funded.

## 16. Official bibliography

All retrieved 2026-08-23:

- [Netcraze current model catalog](https://netcraze.ru/ru/products) — complete current product inventory.
- [Keenetic complete product catalog](https://keenetic.com/en/products), [routers](https://keenetic.com/en/products/routers), and [DSL routers](https://keenetic.com/en/products/dsl-routers) — current relevant universe.
- Individual official product pages linked through the catalogs — model index, CPU/resources, USB/storage, filesystems, OPKG, CLI/SSH and backup capabilities where listed.
- [Netcraze official OPKG/Asterisk architecture matrix](https://support.netcraze.ru/viva/nc-1913/ru/69806-installation-of-the-asterisk-ip-pbx-opkg-package.html) and [Keenetic equivalent](https://support.keenetic.com/hero-5g/kn-4110/en/69806-installation-of-the-asterisk-ip-pbx-opkg-package.html) — authoritative package architecture mapping.
- NC-2312 [Main](https://support.netcraze.ru/hopper-4g-plus/nc-2312/ru/46907-latest-main-release.html), [Preview](https://support.netcraze.ru/hopper-4g-plus/nc-2312/ru/46988-latest-preview-release.html), [lifecycle](https://support.netcraze.ru/hopper-4g-plus/nc-2312/en/31171-product-lifecycle-support-policy.html), and [download/recovery center](https://support.netcraze.ru/hopper-4g-plus/nc-2312/en/50341-download-center.html).
- KN-2312 [Main](https://support.keenetic.com/hopper-4g-plus/kn-2312/en/41380-latest-main-release.html) and [Preview](https://support.keenetic.com/hopper-4g-plus/kn-2312/en/41167-latest-preview-release.html) — independent Keenetic channel confirmation.
- [KeeneticOS 4.0 CLI guide](https://docs.help.keenetic.com/cli/4.0/en/cli_manual_kn-1011.pdf) — read commands, policy operations, save/fail-safe and `/rci` basis; not proof of 5.1 live schemas.

No reseller, marketplace, scraped-spec, random-forum or AI-generated source is used for classification.
