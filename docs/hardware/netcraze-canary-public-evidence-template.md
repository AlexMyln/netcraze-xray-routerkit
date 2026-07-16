# Netcraze hardware-canary public evidence

Publish only after comparing this document with the private evidence manifest and removing every forbidden field. Redaction is not anonymity.

## Release and scope

- Release: `v0.2.0-alpha.16`
- Merge SHA: `<40-hex-sha>`
- Packet schema/version: `routerkit.netcraze.hardware-canary.v1` / `1`
- Model family category: `<planned-family-match | other-family-not-supported>`
- Firmware version, if safe to publish: `<version | withheld>`
- Architecture: `<architecture>`
- Hardware session outcome: `<exact allowed verdict>`

## Non-claims

- Hardware validated: `<true | false>`
- Read contract confirmed: `<true | false>`
- Disposable write contract confirmed: `<true | false>`
- Full hardware canary passed: `<true | false>`
- Live production adapter included: `false`
- Beta/production readiness claimed: `false`

## Interface summary

- Read interface category: `<local-cli | local-structured-api | combined | unavailable | withheld>`
- Write interface category: `<local-cli | local-structured-api | combined | unavailable | withheld>`
- Authentication category: `<local-admin | role-limited | unavailable | withheld>`
- Contract limitations: `<public-safe summary>`

Do not publish exact management paths, session material, object IDs, or raw responses.

## Phase matrix

| Phase | Result | Duration category | Public-safe limitation |
| --- | --- | --- | --- |
| P0 operator preflight | `<pass/fail>` | `<minutes>` | `<none/summary>` |
| P1 platform inventory | `<pass/partial/fail>` | `<minutes>` | `<summary>` |
| P2 discovery read contract | `<pass/partial/fail>` | `<minutes>` | `<summary>` |
| P3 policy read contract | `<pass/partial/fail>` | `<minutes>` | `<summary>` |
| P4 compatibility decision | `<go/patch/stop>` | `<minutes>` | `<summary>` |
| P5 disposable connection | `<pass/skip/fail>` | `<minutes>` | `<summary>` |
| P6 disposable policy | `<pass/skip/fail>` | `<minutes>` | `<summary>` |
| P7 optional assignment | `<pass/skip/fail>` | `<minutes>` | `<summary>` |
| P8 full alpha.16 path | `<pass/partial/fail>` | `<minutes>` | `<summary>` |
| P9 rerun/update | `<pass/partial/fail>` | `<minutes>` | `<summary>` |
| P10 failure/rollback | `<pass/partial/fail>` | `<minutes>` | `<summary>` |
| P11 reboot/recovery | `<pass/partial/fail>` | `<minutes>` | `<summary>` |
| P12 invariant audit | `<pass/fail>` | `<minutes>` | `<summary>` |
| P13 cleanup/return | `<pass/fail>` | `<minutes>` | `<summary>` |

## Public-safe counts and invariants

- Existing connection count category: `<0 | 1–5 | 6–15 | >15 | withheld>`
- Existing policy count category: `<0 | 1–5 | 6–15 | >15 | withheld>`
- Disposable connections created/removed: `<count>/<count>`
- Disposable policies created/removed: `<count>/<count>`
- Optional assignments changed/restored: `<count>/<count>`
- Default policy unchanged: `<true | false | unproven>`
- Unrelated state unchanged: `<true | false | unproven>`
- Loopback-only listeners: `<true | false | unproven>`
- Rollback result: `<not_needed | complete | manual_recovery_required>`
- Reboot result: `<pass | partial | fail | not_run>`
- USB recovery result: `<pass | fail | not_run>`
- Cleanup complete: `<true | false>`
- Device returned: `<true | false>`

## Failure matrix

| Layer | Result | Public-safe recovery summary |
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

| Artifact | SHA-256 | Sensitivity after review | Publication decision |
| --- | --- | --- | --- |
| `<public-safe artifact name>` | `<64-hex>` | `public_safe` | `<publish/withhold>` |

## Limitations and next action

`<Exact public-safe limitations. State whether a narrow off-device patch, a rerun, or manual recovery is required.>`

## Forbidden publication fields

Do not include:

- credentials, cookies, session data, or authentication headers;
- startup configuration, router backup, or raw config export;
- subscription/VLESS material, UUIDs, Reality values, or private keys;
- MAC/IP/device names or private hostnames;
- locally identifying object IDs;
- raw API/CLI responses;
- internal paths or unredacted screenshots;
- private evidence filenames or opaque private references.
