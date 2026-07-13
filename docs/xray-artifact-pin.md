# Pinned Xray artifact verification

Verified on 2026-07-13 for the initial Linux arm64/aarch64 scope.

| Field | Verified value |
| --- | --- |
| Official repository | `XTLS/Xray-core` |
| Stable release tag | `v26.3.27` |
| Official asset | `Xray-linux-arm64-v8a.zip` |
| Supported `uname -m` values | `aarch64`, `arm64` |
| SHA-256 | `4d30283ae614e3057f730f67cd088a42be6fdf91f8639d82cb69e48cde80413c` |

Evidence:

- official stable release: [Xray-core v26.3.27](https://github.com/XTLS/Xray-core/releases/tag/v26.3.27);
- immutable official asset: [Xray-linux-arm64-v8a.zip](https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-arm64-v8a.zip);
- immutable official checksum source: [Xray-linux-arm64-v8a.zip.dgst](https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-arm64-v8a.zip.dgst).

The official `.dgst` records SHA2-256 as `4d30283ae614e3057f730f67cd088a42be6fdf91f8639d82cb69e48cde80413c`. The asset was independently downloaded to a temporary directory and hashed with `shasum -a 256`; the computed value was exactly the same. The command asserted equality and the temporary directory was removed. No archive or extracted binary was copied into this repository.

## Updating the pin

1. Resolve the current non-draft, non-prerelease release from the official GitHub releases/API.
2. Select only an architecture whose mapping is confirmed and tested.
3. Download the versioned asset and its versioned `.dgst` from `github.com/XTLS/Xray-core/releases/download/<tag>/` into a temporary directory.
4. Read SHA-256 from the official sidecar and independently hash the archive.
5. Stop if the two values differ.
6. Update the manifest, both verification documents, and fixtures/tests.
7. Run the complete offline test and safety suite and remove all temporary downloads.
8. Submit the pin change as a reviewed pull request.

Changing the pin is a supply-chain change. It requires a reviewed PR and repeated independent verification. Never use `/latest/`, a branch archive, a third-party mirror, or an unverified checksum page.
