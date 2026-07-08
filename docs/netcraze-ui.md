# Netcraze/Keenetic Web UI guide

This project intentionally leaves Netcraze/Keenetic Proxy connections and Connection policies as a manual Web UI step.

## Proxy connections

Create one proxy connection per local Xray SOCKS port.

Example:

| Name | Type | Server | Port |
|---|---|---|---:|
| `XRAY-PROFILE-A` | SOCKS5 | `127.0.0.1` | `1082` |
| `XRAY-PROFILE-B` | SOCKS5 | `127.0.0.1` | `1083` |
| `XRAY-PROFILE-C` | SOCKS5 | `127.0.0.1` | `1084` |

Keep authentication disabled unless you explicitly configured SOCKS auth in Xray.

## Connection policies

Create a policy per profile:

| Policy | Connection |
|---|---|
| `CLIENT-PROFILE-A` | only `XRAY-PROFILE-A` |
| `CLIENT-PROFILE-B` | only `XRAY-PROFILE-B` |
| `CLIENT-PROFILE-C` | only `XRAY-PROFILE-C` |

Do not modify the default policy for all devices.

Assign only the intended client, for example a media device, to the selected policy.

## Safe switching

- Normal: selected client -> `CLIENT-PROFILE-A`
- Fallback 1: selected client -> `CLIENT-PROFILE-B`
- Fallback 2: selected client -> `CLIENT-PROFILE-C`
- Direct internet: selected client -> default policy

## Avoid

- Do not add an entire segment.
- Do not add all clients.
- Do not move the default policy to a proxy connection.
- Do not expose local SOCKS ports to LAN/WAN.
