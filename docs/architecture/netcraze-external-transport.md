# Netcraze external transaction transport

This document records the #40/#41 architecture boundary after the production
NC-3812 evidence showed that the official Netcraze MCP/RMM transport can read
and execute native router commands but cannot run an arbitrary Entware shell.

## Ownership boundary

RouterKit owns all deterministic semantics:

```text
protected fresh running-config snapshot
-> existing RouterKit live parser
-> existing semantic-compatibility planner
-> immutable external transaction packet
-> exact native command execution by the external transport
-> protected fresh running-config snapshot
-> existing semantic-compatibility verifier
```

The transport owns only delivery. The official Netcraze MCP/RMM is therefore
a supported transport for native Proxy/Policy work even when it provides no
SSH or arbitrary Linux shell. The local Entware `ndmc` transport remains the
supported direct execution path in `scripts/routerkit-netcraze-live.py`.

Browser/Web UI configuration remains prohibited as an automatic fallback.

## Protected snapshot contract

`scripts/routerkit-netcraze-external.py` accepts a running-config snapshot only
from a bounded UTF-8 regular file. On POSIX the file must be owned by the
current user, have no group/world permission bits, and have neither symlink nor
hard-link aliases. RouterKit checks path/descriptor identity before and after
the read. Raw snapshot contents are never printed or included in the packet.

The endpoint manifest retains its existing private-file contract.

## External transaction v1

The packet schema is `routerkit.netcraze.external-transaction.v1`; its checked-in
shape is [`routerkit-netcraze-external-transaction.v1.schema.json`](../../hardware/routerkit-netcraze-external-transaction.v1.schema.json).
It contains:

- the supported `nc3812-netcrazeos-5.1.5` hardware contract;
- manifest, pre-state, expected post-state, and Default-guard fingerprints;
- deterministic slot -> native Proxy -> native Policy bindings;
- `create`/`reuse` action per Proxy and Policy;
- optional `none`/`reuse`/`assign`/`move` assignment action;
- exact ordered running-state commands and exact reverse rollback commands;
- explicit `backup_required`, `write_required`, `save_required`, and two-phase verification gates;
- one transaction fingerprint covering every other packet field;
- no raw running config, profile source, authentication material, or transport
  success claim.

The packet file is created once with owner-only permissions and is never
overwritten. For a device assignment it necessarily contains the selected MAC
in the exact native command; treat the packet as private operational data.

Transaction integrity is not a substitute for fresh state. Before any write,
`verify --phase pre` must match the packet's full parser-visible pre-state
fingerprint. After command execution, `verify --phase running` must match the
expected post-state, prove unique semantic Proxy/Policy matches, verify the
assignment, and prove the Default guard unchanged. MCP/HTTP/CLI success text is
ignored.

## Two-phase mutation and save

For a mutating packet:

1. acquire a fresh running-config snapshot and build the packet;
2. acquire and verify the native saved/startup rollback checkpoint required by
   `backup_required`;
3. run `verify --phase pre` against the state used for the write;
4. execute only `commands`, exactly in order;
5. acquire a fresh running-config snapshot;
6. run `verify --phase running`;
7. execute the exact `save_command` only when the verifier returns
   `save_authorized=true`;
8. when the transport exposes saved/startup configuration in the same native
   syntax, acquire it freshly and run `verify --phase saved`;
9. perform the separate functional service checks required by the runbook.

An external agent must not add commands, change their order, infer replacement
Proxy/Policy semantics, or use a successful transport response as proof.

## NOOP contract and current NC-3812

When every Proxy and Policy is an exact semantic reuse and assignment is
`none` or `reuse`, the packet contains:

```text
write_required=false
save_required=false
backup_required=false
commands=[]
```

The fresh post-state is still verified. No running-config backup is created
solely for the NOOP, no native write runs, and `system configuration save` is
not issued. The existing human-readable production descriptions such as
`XRAY-NL` / `VPN-NL` remain valid exact semantic reuse through
`routerkit_netcraze_live_compat.py`; renaming is neither planned nor required.

## #40 and #41 status

#40's live adapter is now split into RouterKit-owned semantics plus replaceable
local `ndmc` or external MCP/RMM command delivery. The remaining hardware proof
can run without SSH and must end in RouterKit verification.

#41 orchestration must compose this packet protocol rather than assume that
RouterKit itself can launch on-router Entware processes through every
management transport. A one-command orchestrator may drive the phases, but it
must preserve the exact packet commands and both verification gates.
