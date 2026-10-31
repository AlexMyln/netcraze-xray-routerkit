# Subscription refresh acceptance — NC-3812, 2026-10-31

The subscription worker was installed on an existing Hopper SE NC-3812 runtime
on NetcrazeOS build `5.01.C.8.0-2`. Target-side evidence confirmed Linux aarch64,
external EXT4 mounted read-write at literal `/opt`, Entware Python 3.13.9,
the existing Xray 26.3.27 process and three loopback SOCKS listeners.

The existing Xray binary and launcher were retained. Entware Vixie cron 4.1-7
was installed using the trusted `/opt/bin/opkg`. A private, verified checkpoint
was taken before the configuration change. No router reboot was performed.

## Migration and functional checks

The primary profile required a server-port update. Another selected profile
also had a changed server address/SNI. Default enrollment correctly rejected
that larger identity change. An explicit owner-only RouterKit-generated
selection, checked against its preparation checksum and the complete current
subscription node parameters, supplied the intended bindings. No source-order
or fuzzy-match fallback was introduced.

The refresh changed only the VLESS outbound fragment. Target-side Xray config
validation passed, followed by process/executable identity, loopback listener
ownership and verified HTTPS with remote DNS through all three profiles.
Hashes and modes confirmed preservation of inbounds, routing, the launcher
and existing persistent direct-routing overrides. Native Proxy/Policy objects,
client assignments and the ordinary Internet path were not changed.

The installed worker was then run with a minimal cron environment. It returned
`unchanged`, passed its functional checks and retained the same Xray process
epoch. The permanent job uses `0 */2 * * *`; the Entware cron executable,
job read-back and enabled boot script were checked separately.

A self-removing one-shot cron job also invoked the installed worker through
the actual Entware scheduler. Its protected receipt confirmed exit 0,
`SUBSCRIPTION_UNCHANGED` and preservation of the Xray PID. Temporary probe
files and uploaded secret inputs were removed after this acceptance check;
the permanent two-hour job and protected subscription source remain installed.

## Evidence boundary

This records one subscription-refresh operation on an existing installation.
It does not establish clean-install, native transaction/rollback, real-client
application or new post-reboot acceptance. The recurring job still depends on
the original HTTPS source and stable unique source node names; missing or
ambiguous selected nodes retain the current configuration and report failure.

Production sources, generated configs, keys, router identifiers, tunnel
addresses and raw logs are excluded from this record. The protected install
receipt and single pre-change checkpoint remain on the target.
