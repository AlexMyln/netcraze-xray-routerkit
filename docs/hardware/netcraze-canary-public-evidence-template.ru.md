# Публичный evidence для аппаратного canary Netcraze

Публиковать только после сверки с private manifest. Redaction не равна anonymity.

## Release и scope

- Release: `v0.2.0-alpha.16`
- Merge SHA: `<40-hex-sha>`
- Packet: `routerkit.netcraze.hardware-canary.v1` / `1`
- Model family category: `<planned-family-match | other-family-not-supported>`
- Firmware, если безопасно: `<version | withheld>`
- Architecture: `<architecture>`
- Hardware outcome: `<exact allowed verdict>`

## Non-claims

- Hardware validated: `<true | false>`
- Read contract confirmed: `<true | false>`
- Disposable write contract confirmed: `<true | false>`
- Full canary passed: `<true | false>`
- Live production adapter: `false`
- Beta/production claim: `false`

## Interface summary

- Read interface category: `<local-cli | local-structured-api | combined | unavailable | withheld>`
- Write interface category: `<local-cli | local-structured-api | combined | unavailable | withheld>`
- Authentication category: `<local-admin | role-limited | unavailable | withheld>`
- Limitations: `<public-safe summary>`

Не публиковать exact management paths, session material, object IDs и raw responses.

## Phase matrix

| Phase | Result | Minutes | Limitation |
| --- | --- | --- | --- |
| P0 preflight | `<pass/fail>` | `<n>` | `<summary>` |
| P1 platform | `<pass/partial/fail>` | `<n>` | `<summary>` |
| P2 discovery read | `<pass/partial/fail>` | `<n>` | `<summary>` |
| P3 policy read | `<pass/partial/fail>` | `<n>` | `<summary>` |
| P4 decision | `<go/patch/stop>` | `<n>` | `<summary>` |
| P5 connection | `<pass/skip/fail>` | `<n>` | `<summary>` |
| P6 policy | `<pass/skip/fail>` | `<n>` | `<summary>` |
| P7 assignment | `<pass/skip/fail>` | `<n>` | `<summary>` |
| P8 alpha.16 path | `<pass/partial/fail>` | `<n>` | `<summary>` |
| P9 rerun/update | `<pass/partial/fail>` | `<n>` | `<summary>` |
| P10 failure/rollback | `<pass/partial/fail>` | `<n>` | `<summary>` |
| P11 reboot/recovery | `<pass/partial/fail>` | `<n>` | `<summary>` |
| P12 invariant audit | `<pass/fail>` | `<n>` | `<summary>` |
| P13 cleanup/return | `<pass/fail>` | `<n>` | `<summary>` |

## Counts и invariants

- Connection count category: `<0 | 1–5 | 6–15 | >15 | withheld>`
- Policy count category: `<0 | 1–5 | 6–15 | >15 | withheld>`
- Disposable connections created/removed: `<n>/<n>`
- Disposable policies created/removed: `<n>/<n>`
- Assignments changed/restored: `<n>/<n>`
- Default unchanged: `<true | false | unproven>`
- Unrelated unchanged: `<true | false | unproven>`
- Loopback-only: `<true | false | unproven>`
- Rollback: `<not_needed | complete | manual_recovery_required>`
- Reboot: `<pass | partial | fail | not_run>`
- USB recovery: `<pass | fail | not_run>`
- Cleanup complete: `<true | false>`
- Device returned: `<true | false>`

## Failure matrix

| Layer | Result | Recovery summary |
| --- | --- | --- |
| Planning | `<pass/fail/not_run>` | `<summary>` |
| Bootstrap precondition | `<pass/fail/not_run>` | `<summary>` |
| Router preflight | `<pass/fail/not_run>` | `<summary>` |
| Backup gate | `<pass/fail/not_run>` | `<summary>` |
| Install staging | `<pass/fail/not_run>` | `<summary>` |
| Autostart | `<pass/fail/not_run>` | `<summary>` |
| Healthcheck | `<pass/fail/not_run>` | `<summary>` |
| Disposable connection | `<pass/fail/not_run>` | `<summary>` |
| Disposable policy | `<pass/fail/not_run>` | `<summary>` |
| Optional assignment | `<pass/fail/not_run>` | `<summary>` |

## Sanitized artifacts

| Artifact | SHA-256 | Sensitivity | Decision |
| --- | --- | --- | --- |
| `<public-safe name>` | `<64-hex>` | `public_safe` | `<publish/withhold>` |

## Limitations и next action

`<Public-safe summary: narrow patch, rerun или manual recovery.>`

## Forbidden

Не включать credentials/cookies/session data, startup config, backup, raw export, subscription/VLESS/UUID/Reality/private keys, MAC/IP/device names/private hostnames, local object IDs, raw responses, internal paths, unredacted screenshots, private filenames/references.
