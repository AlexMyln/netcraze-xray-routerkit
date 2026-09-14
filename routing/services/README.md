# RouterKit routing service packs

Service packs are small, versioned domain-suffix sets used by RouterKit local direct routing.

They are **opt-in**. Adding a service pack means traffic matching those suffixes may leave the selected VLESS path through RouterKit's existing `direct` (`freedom`) outbound.

## Evidence levels

| Service | Status | Basis |
| --- | --- | --- |
| `kinopoisk` | hardware-proven | Samsung TV on NC-3812 / NetcrazeOS 5.1.5, 2026-09-11 |
| `rutube` | community-derived candidate | `v2fly/domain-list-community` |
| `okko` | community-derived candidate | `v2fly/domain-list-community` |
| `wink` | community-derived candidate | `v2fly/domain-list-community` |
| `ivi` | community-derived candidate | `v2fly/domain-list-community` category-entertainment-ru |

`community-derived candidate` means the suffix set is grounded in a maintained public routing list, but RouterKit has **not** yet hardware-proven that the set is complete for login, app bootstrap, DRM, playback, or every client platform.

Do not silently promote candidate packs to hardware-proven. Record a real end-to-end test first.

## Current candidate provenance

The initial candidate sets were copied from `v2fly/domain-list-community` at commit:

```text
5d939545c84e2a534f8e85ba6ffb2b51fa18fb76
```

Source files:

- `data/rutube`
- `data/okko`
- `data/wink`
- `data/category-entertainment-ru` (`ivi.ru`, `ivicdn.tv`)

The RouterKit files intentionally contain only domain suffixes. They do not import upstream tracking/ads tags, broad Russian categories, IP ranges, or unrelated provider domains.

## Batch selection

The CLI accepts repeated `--add-service` flags, so an operator can preview several candidates at once:

```sh
python3 scripts/routerkit-routing.py plan \
  --add-service kinopoisk \
  --add-service rutube \
  --add-service ivi \
  --add-service okko \
  --add-service wink
```

Then apply only after reviewing the expanded domain set:

```sh
python3 scripts/routerkit-routing.py apply \
  --add-service kinopoisk \
  --add-service rutube \
  --add-service ivi \
  --add-service okko \
  --add-service wink \
  --yes
```

A candidate pack that fails on a real client should be expanded from observed service-specific dependencies rather than by adding broad suffixes such as all of `yandex.ru` or an entire `RU_ALL` category.
