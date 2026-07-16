# Read-Only Device Discovery Probe Packet

Status: `SOFTWARE_CORE_READY_HARDWARE_CONTRACT_PENDING`.

Do not run this packet against a production router unless the operator has a spare or disposable hardware window and accepts that raw outputs are local-sensitive. Do not publish raw output.

## Current Probe State

`scripts/probe-device-discovery-readonly.sh` intentionally executes no Netcraze/Keenetic discovery command by default. It only prints the contract-pending status. This avoids encoding guessed commands as executable defaults.

## Hardware Window Goals

Confirm, on Netcraze Hopper 4G+ NC-2312 firmware `5.00.C.12.0-0`:

- whether the documented read-only CLI commands are available;
- whether `/rci` can expose equivalent structured output;
- exact output fields and encoding;
- authentication model;
- source consistency between DHCP leases, Wi-Fi associations, hotspot hosts, Ethernet/FDB data, and policy bindings;
- correspondence with the Web UI;
- whether existing policy assignment can be read without unrelated configuration.

## Candidate Sources To Confirm

The official KeeneticOS CLI reference documents these as read-only candidates:

- `show ip dhcp bindings`;
- `show associations`;
- `show ip hotspot summary`;
- `show ip arp`;
- `/rci` REST Core Interface.

They remain candidates, not executable RouterKit defaults, until verified on the target hardware.

The fixture-first software intentionally contains no generic subprocess runner for these candidates. After this probe decides CLI versus `/rci` versus another structured interface, the production adapter needs an interface-specific execution boundary reviewed in that later change.

## Safety Rules

- no configuration mode;
- no write endpoints;
- no reboot;
- no service action;
- no active scan;
- no firewall, TPROXY, REDIRECT, or `xkeen -start`;
- stop on first unexpected result;
- write output only to a private `0700` directory with `0600` files;
- redact or hash stable local identifiers before sharing evidence;
- remove private probe output after review.

## Sanitized Evidence To Keep

Keep only command availability, schema summaries, field lists, source consistency notes, firmware/model metadata, and pass/fail status. Do not keep raw device names, addresses, MACs, policy names, backups, credentials, or inventories in the repository.
