# RouterKit local direct-routing overrides

RouterKit can keep local split-routing rules independently of the selected VPN provider. Selected domain suffixes use the existing Xray `direct` (`freedom`) outbound while all other traffic for each SOCKS profile continues through that profile's VLESS outbound.

```text
client -> native Netcraze policy -> RouterKit SOCKS -> Xray
                                         |-- selected service -> direct -> normal WAN
                                         `-- everything else -> selected VLESS
```

This feature is **off by default**. RouterKit never sends Russian sites outside the VPN automatically.

## Service packs

Versioned service packs live in `routing/services/`.

The first hardware-proven preset is `kinopoisk`. A direct bypass for a Samsung TV was validated on Netcraze Hopper SE NC-3812 / NetcrazeOS 5.1.5 on 2026-09-11 while the TV's remaining traffic continued through the selected RouterKit VPN profile.

Current `kinopoisk` domains:

```text
kinopoisk.ru
kinopoisk-ru.clstorage.net
ott.yandex.ru
ott.yandex.net
strm.yandex.ru
yastatic.net
yndx.net
yccdn.ru
```

This set is proven sufficient for that live scenario. It is not a claim that every entry is individually minimal.

## Post-install use

Status:

```sh
python3 scripts/routerkit-routing.py status
```

Preview:

```sh
python3 scripts/routerkit-routing.py plan --add-service kinopoisk
```

Apply:

```sh
python3 scripts/routerkit-routing.py apply --add-service kinopoisk --yes
```

Adding an already-enabled service pack again is a no-op.

Remove it:

```sh
python3 scripts/routerkit-routing.py apply --remove-service kinopoisk --yes
```

Custom suffixes are also supported:

```sh
python3 scripts/routerkit-routing.py plan --add-domain example.ru
python3 scripts/routerkit-routing.py apply --add-domain example.ru --yes
```

Use domain suffixes, not URLs, wildcards, or IP addresses.

## Persistent state

Desired state is kept locally at:

```text
/opt/etc/routerkit/routing-overrides.json
```

The owner-only file contains no VPN keys or subscription secrets.

A normal `install-xray-direct.sh` first installs freshly generated Xray fragments and then runs an internal `reconcile` when persistent routing state exists. This prevents later setup/regeneration from silently discarding selected local direct overrides.

## Apply safety

`apply`:

1. reads the active `03_inbounds.json`, `04_outbounds.json`, and `05_routing.json`;
2. requires exactly one existing `direct` outbound using `protocol=freedom`;
3. keeps the RouterKit direct rule before per-profile VLESS catch-all rules;
4. does not touch Netcraze Proxy/Policy objects, DNS, PPPoE, Wi-Fi, or firewall state;
5. creates a rollback backup;
6. validates the future Xray config with `xray run -test -confdir` in a staging directory;
7. atomically publishes `05_routing.json`;
8. validates the active config again;
9. restarts only `S23xray-direct` with a complete Entware `PATH`;
10. verifies Xray/listener ownership through the normal RouterKit verifier and performs HTTPS probes through every local SOCKS profile;
11. persists desired routing state only after the live checks pass.

If a post-publication step fails, RouterKit attempts to restore the old `05_routing.json` and restart the previous Xray state.

## Foreign-rule protection

RouterKit manages only its own first direct-domain rule. If an unknown direct rule is already first, the command fails closed instead of overwriting it.

If persistent state does not exist yet but the first rule exactly equals a known service pack, RouterKit may recognize that rule as existing state. This lets a previously validated live `kinopoisk` rule be adopted without creating a duplicate.

## DNS

Local direct routing does not replace the requirement for a working DNS path. With TCP-only native Proxy policies, DNS upstreams must also be available to those policies. On the hardware-proven NC-3812 path, system DoH upstreams had to be unbound (`Any`) rather than pinned to one WAN interface so policy-specific DNS proxies could use them.
