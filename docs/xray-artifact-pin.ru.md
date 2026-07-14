# Проверка закреплённого Xray artifact

Проверено 2026-07-13 для начального scope Linux arm64/aarch64.

| Поле | Проверенное значение |
| --- | --- |
| Официальный repository | `XTLS/Xray-core` |
| Stable release tag | `v26.3.27` |
| Официальный asset | `Xray-linux-arm64-v8a.zip` |
| Поддержанные `uname -m` | `aarch64`, `arm64` |
| SHA-256 | `4d30283ae614e3057f730f67cd088a42be6fdf91f8639d82cb69e48cde80413c` |

Доказательства:

- [официальный release v26.3.27](https://github.com/XTLS/Xray-core/releases/tag/v26.3.27);
- [immutable versioned asset](https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-arm64-v8a.zip);
- [официальный checksum sidecar](https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-arm64-v8a.zip.dgst).

В официальном `.dgst` SHA2-256 равен `4d30283ae614e3057f730f67cd088a42be6fdf91f8639d82cb69e48cde80413c`. Asset независимо скачан во временную директорию и проверен через `shasum -a 256`; вычисленное значение полностью совпало. После assertion временная директория удалена. Архив или распакованный binary в репозиторий не копировались.

## Runtime trust и bounds

`routerkit bootstrap --apply` использует reviewed repository manifest, а не runtime `.dgst` sidecar, как trust input. Принимается только точный `linux-arm64` `download_url`; до 128 MiB stream-загружается через proxy-free HTTPS, а вычисленный SHA-256 однозначно сравнивается с manifest до extraction. Каждый DNS answer и redirect hop проверяется отдельно; signed redirect queries не попадают в result или errors.

После checksum verification bounded Python ZIP handling читает только один root member, нормализующийся в `xray` (не более 96 MiB, 128 archive entries и ratio 200:1). Candidate запускается в sanitized environment и обязан вернуть точно `Xray 26.3.27`. Только затем возможны verified backup и atomic replacement. Retained receipt записывает release, manifest archive hash, installed binary hash/version и optional verified rollback backup; одна version string без matching receipt/hash provenance не включает idempotent fast path.

## Обновление pin

1. Найти текущий non-draft, non-prerelease release только через официальный GitHub releases/API.
2. Выбрать только подтверждённую и протестированную architecture.
3. Скачать versioned asset и `.dgst` из `github.com/XTLS/Xray-core/releases/download/<tag>/` во временную директорию.
4. Взять SHA-256 из официального sidecar и независимо вычислить hash архива.
5. Остановиться при любом несовпадении.
6. Обновить manifest, обе документации, fixtures и tests.
7. Запустить полный offline test/safety suite и удалить временные downloads.
8. Провести изменение через reviewed pull request.

Изменение pin — supply-chain change. Обязательны reviewed PR и повторная независимая проверка. Запрещены `/latest/`, branch archives, сторонние mirrors и checksum с непроверенных страниц.
