# Hopper SE NC-3812: NetcrazeOS 5.1.8 compatibility

Evidence date: **2026-10-17**, Europe/Samara. Русская версия: [nc3812-netcrazeos-5.1.8.ru.md](nc3812-netcrazeos-5.1.8.ru.md).

## Software support and evidence level

RouterKit accepts the exact `nc3812-netcrazeos-5.1.8` software contract in the
native CLI, external transaction planner/verifier, and `live-install` intent,
receipt, and evidence validators. The existing `nc3812-netcrazeos-5.1.5`
contract remains supported and remains the default for backward compatibility.
Select 5.1.8 explicitly after establishing the target's model and firmware.
There is no automatic firmware detection, version-range expansion, or fallback
to the 5.1.5 contract for another firmware. Other models and unlisted versions
remain unsupported by these live adapters.

**5.1.8 has read-only native compatibility evidence, not full hardware write or
runtime acceptance.** Adding the software contract does not turn unknown
runtime facts into PASS, authorize router changes, or close the #16 hardware
matrix or #41 brownfield acceptance rerun. The 2026-09-09 installation/reboot
evidence for 5.1.5 remains specific to that firmware.

## Observed on the existing installation

The audit inspected public RouterKit main `084a42f` and one live Hopper SE.
It made no router configuration, service, firmware, assignment, or reboot
changes. Full configuration snapshots were kept in owner-only temporary files
for offline parsing and removed afterwards.

| Property | Observation | Scope |
| --- | --- | --- |
| Target | Hopper SE NC-3812, stable 5.1.8, build `5.01.C.8.0-2`, `aarch64` | Live version response |
| Network/control | RMM responds; PPPoE, LAN and three native Proxy interfaces are up/connected | Current state, not client traffic acceptance |
| Storage | External USB EXT4 is mounted and used by OPKG; the configured init path is `/opt/etc/init.d/rc.unslung` | Literal `/opt` mount and Entware package integrity were not inspected through a Linux shell |
| Xray | A running `xray` process uses `run -confdir /opt/etc/xray/configs` | Executable/hash, pinned semantic release and listener ownership were not verified |
| Native proxies | Three enabled, unauthenticated SOCKS5 upstreams at `127.0.0.1:1082`–`1084` | Proxy configuration; not proof of actual Xray listener binds |
| Native policies | Each of three policies permits only its corresponding Proxy; live IPv4/IPv6 default routes use it | Includes explicit negative permits for other global interfaces |
| Persistence | RouterKit-parsed Proxy/Policy/assignment semantics match in running and saved configuration | Readback only; no new save or reboot |
| Planning | The existing parser and semantic-reuse planner recognize all three Proxy/Policy pairs without a write | Expected endpoint manifest derived from observed native config; installed runtime manifest was not read |
| DNS/client | Unbound DoH is configured; the sole explicitly assigned VPN client was offline | DNS/HTTPS through that client was not tested |

The initial audit's 81 focused software tests passed. These tests did not
exercise router writes. The committed
[native fixture](../../tests/fixtures/netcraze/nc3812-5.1.8-native.txt) retains
only the observed Proxy/Policy syntax and a synthetic locally administered
client MAC. It is not a raw configuration export or runtime evidence file.
Regression tests cover LF/CRLF parsing, semantic reuse, NOOP verification,
both supported contracts, and rejection of unknown/cross-contract inputs.

## Selecting the contract

An offline orchestration plan can select 5.1.8 without contacting a router:

```sh
python3 scripts/routerkit.py live-install plan \
  --transport external \
  --hardware-contract nc3812-netcrazeos-5.1.8 \
  --selected-device-mac 02:00:00:00:00:01 \
  --profile-slot 1
```

The MAC above is a synthetic example. The standalone native and external tools
use `--contract nc3812-netcrazeos-5.1.8`. Native apply still requires explicit
`--contract`; the external planner retains 5.1.5 as its omitted-option default.
External verification reads the packet's contract unless `--contract` is
supplied, in which case it must match. Receipts cannot switch firmware contract
on resume, and evidence/transactions for one contract cannot satisfy another
contract's receipt. Start a new scope with fresh evidence after a firmware
change.

## Remaining acceptance

Before claiming full 5.1.8 runtime or installation acceptance, obtain fresh
target-side evidence for literal external EXT4 `/opt`, Entware, pinned Xray
identity/version, actual loopback-only listener ownership, the installed
endpoint manifest, autostart, and recovery after an authorized reboot. Native
writes/save/rollback require their own authorized live verification; software
tests cannot provide that evidence. Final client acceptance still requires the
selected assignment plus real domain DNS and HTTPS through the selected policy.

The runtime-adoption, protected-DNS, read-before-write, fresh readback, verified
save, and rollback gates are unchanged. This compatibility addition was made
offline; it performs no action on Hopper SE.
