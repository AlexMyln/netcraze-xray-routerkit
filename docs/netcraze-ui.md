# Netcraze/Keenetic native Proxy and per-device policies

RouterKit creates local SOCKS endpoints, but those endpoints **do not route client devices by themselves**.

Use the native router chain:

```text
Xray loopback SOCKS
-> native Proxy interface
-> native Internet-access policy
-> selected registered client
```

See also [`live-install-runbook.md`](live-install-runbook.md) for the bounded production flow.

## 1. Native Proxy client component

If the declared goal includes per-device VPN routing, verify the router's native **Proxy client** system component early in the installation.

If installing that component requires a reboot and the operator authorized completing the installation, install it before the final policy stage and use one controlled reboot for the RouterKit after-reboot proof as well.

Do not discover required Proxy-client support only after declaring the RouterKit runtime finished.

## 2. Proxy connections/interfaces

Create one native Proxy interface per local Xray SOCKS endpoint.

Example:

| Name | Type | Server | Port |
|---|---|---|---:|
| `XRAY-PROFILE-A` | SOCKS5 | `127.0.0.1` | `1082` |
| `XRAY-PROFILE-B` | SOCKS5 | `127.0.0.1` | `1083` |
| `XRAY-PROFILE-C` | SOCKS5 | `127.0.0.1` | `1084` |

Keep authentication disabled unless SOCKS authentication was explicitly configured in Xray.

Some NetcrazeOS versions may not expose the required objects in the Web UI. In that case use the firmware's **native CLI or structured management interface**, not a firewall/iptables workaround.

Discover free `ProxyN` identifiers from live state instead of guessing them.

## 3. Internet-access policies

Create one native policy per profile:

| Policy | Connection |
|---|---|
| `CLIENT-PROFILE-A` | only `XRAY-PROFILE-A` |
| `CLIENT-PROFILE-B` | only `XRAY-PROFILE-B` |
| `CLIENT-PROFILE-C` | only `XRAY-PROFILE-C` |

Each VPN policy should use only its corresponding Proxy interface as the VPN path.

Assign only operator-selected registered clients.

## 4. Default/direct Internet behavior

The normal safe behavior is:

- unselected clients keep direct PPPoE/the ordinary Internet path;
- no whole segment or Home network is assigned automatically;
- Default/Main is not moved wholesale to a Proxy path.

If the operator **explicitly** authorizes adding Proxy interfaces as fallbacks to Default, that is a separate allowed choice. Report the resulting priority structure precisely and do not claim `DEFAULT_POLICY_UNCHANGED=TRUE` afterward.

The direct uplink should remain the primary normal path unless the operator explicitly requests another design.

## 5. Safe switching for one client

Typical switching is:

- selected client -> `CLIENT-PROFILE-A`;
- selected client -> `CLIENT-PROFILE-B`;
- selected client -> `CLIENT-PROFILE-C`;
- back to direct Internet -> Default/Main policy.

This changes one registered device without changing unrelated clients.

## 6. Avoid

- Do not add an entire segment unless the operator explicitly requests it.
- Do not assign all clients automatically.
- Do not expose ports `1082`/`1083`/`1084` to LAN/WAN.
- Do not call `xkeen -start`.
- Do not create TPROXY/REDIRECT/transparent firewall mode.
- Do not substitute iptables marking or hand-edited configuration for a known native policy mechanism.
- Do not modify Default/Main silently.

## 7. Verification

First verify RouterKit/Xray:

```sh
sh scripts/healthcheck.sh
```

Then verify native router state:

- every required Proxy interface is `UP`/ready;
- every policy contains the expected Proxy path;
- only selected devices have VPN-policy assignments;
- unselected devices retain the ordinary direct path;
- PPPoE/default route/DNS/LAN/Wi-Fi/RMM remain healthy.

For each assigned client, inspect active policy sessions/counters and real response traffic when the router exposes that evidence.

If PID/executable identity, loopback listener ownership, and real SOCKS/HTTPS traffic independently prove a healthy RouterKit runtime while one diagnostic verifier has a known false-negative, track the tooling defect separately rather than declaring the VPN service down.
