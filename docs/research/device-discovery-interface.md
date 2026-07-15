# Device Discovery Interface Research

Access date: 2026-07-15.

## Verdict

RouterKit now treats the software core as ready and the hardware contract as pending:

`SOFTWARE_CORE_READY_HARDWARE_CONTRACT_PENDING`

The official KeeneticOS CLI reference documents several read-only sources that may contribute to local-device discovery, but it does not by itself prove the exact Netcraze Hopper 4G+ NC-2312 firmware `5.00.C.12.0-0` output shape or the complete join contract needed for safe selection. RouterKit therefore implements fixture-first normalization, rendering, redaction, and selection, while the executable vendor adapter remains disabled with `contract_unverified`.

## Evidence

| Evidence | Publisher | URL | Claim | Confidence |
| --- | --- | --- | --- | --- |
| Command Reference Guide, Hero KN-1011, OS 4.0 | Keenetic Limited | https://docs.help.keenetic.com/cli/4.0/en/cli_manual_kn-1011.pdf | The CLI guide defines command metadata, including `Change settings`, and includes an HTTP API section for `/rci`. | official_documented |
| `show ip dhcp bindings` | Keenetic Limited | https://docs.help.keenetic.com/cli/4.0/en/cli_manual_kn-1011.pdf | Section 3.146.52, guide p. 537, is marked `Change settings No` and shows DHCP lease fields including IP, MAC, expiry, and hostname. | official_documented |
| `show associations` | Keenetic Limited | https://docs.help.keenetic.com/cli/4.0/en/cli_manual_kn-1011.pdf | Section 3.146.4, guide pp. 475-476, is marked `Change settings No` and shows Wi-Fi station association fields including MAC, AP/interface, authentication, uptime, and radio metrics. | official_documented |
| `show ip hotspot summary` | Keenetic Limited | https://docs.help.keenetic.com/cli/4.0/en/cli_manual_kn-1011.pdf | Section 3.146.57, guide pp. 542-543, is marked `Change settings No` and summarizes registered hosts with active state and names for traffic counters. | official_documented |
| REST Core Interface | Keenetic Limited | https://docs.help.keenetic.com/cli/4.0/en/cli_manual_kn-1011.pdf | The guide documents `/rci` as an HTTP API base for accessing settings with HTTP methods. | official_documented |
| Keenetic User Manual pages | Keenetic GmbH | https://support.keenetic.com/eu/titan/kn-1811/en/31111-keenetic-mobile-application.html | The official manual index includes Web Interface, Status, Traffic monitor, and Wi-Fi monitor pages, confirming user-visible status surfaces but not a stable local client API schema. | official_documented |
| KeeneticOS overview | Keenetic GmbH | https://keenetic.com/en/keenetic-os | KeeneticOS is the modular OS for Keenetic products and includes network monitoring and device-oriented management features. | official_documented |

## Proven Interface Facts

- The official CLI reference exists for KeeneticOS 4.0 and separates commands by whether they change settings.
- `show ip dhcp bindings`, `show associations`, and `show ip hotspot summary` are documented as read-only candidate commands with `Change settings No`.
- Documented fields cover parts of #21: IP/MAC/hostname from DHCP, Wi-Fi association MAC/interface/radio state, and hotspot host active/name summaries.
- `/rci` is documented as the REST Core Interface base, but the local authentication and endpoint-to-command behavior still needs target-hardware confirmation.

## Inferred Behavior

- A production adapter will likely need to join several sources rather than rely on one command.
- DHCP leases alone are not enough for safe assignment because IPs can be reused and offline/stale leases may remain.
- Wi-Fi association data can prove online wireless presence but may not provide friendly names or policy state.
- Hotspot data may expose registered-host state and existing policy information, but the exact fields and coverage need hardware confirmation.

## Unresolved Details

- Exact output schema on Netcraze Hopper 4G+ NC-2312 firmware `5.00.C.12.0-0`.
- Whether `/rci/show/...` exposes the same data in machine-readable JSON for the target firmware.
- Authentication model for local `/rci` or CLI automation on the target device.
- Which source provides existing policy assignment without unrelated configuration.
- Whether Ethernet/FDB data and policy bindings need separate commands.
- Whether command output contains unrelated sensitive configuration.

## Implementation Decision

This PR implements:

- fixture-first core models and adapter states;
- protected local fixture input only;
- deterministic normalization, trusted-ID selection gating, sorting, JSON/text rendering, public-evidence redaction, and fail-closed selection;
- `routerkit devices` and `scripts/routerkit-devices.py`;
- `routerkit setup --discover-devices` for explicit read-only selection after strict planning.

It does not implement:

- live router command execution;
- active LAN scanning;
- policy writes or device assignment;
- proxy connection changes;
- default-policy changes.
