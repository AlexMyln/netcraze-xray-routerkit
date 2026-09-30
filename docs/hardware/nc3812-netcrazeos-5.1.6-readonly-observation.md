# NC-3812 / NetcrazeOS 5.1.6: preliminary read-only observation

Observation date: **2026-09-30** (Europe/Samara, UTC+04).
Status: **preliminary, bounded read-only device evidence**.

## Source and version boundary

This is a sanitized summary of the operator-supplied
`RouterKit-5.1.6-readonly-report.md`, not an independent rerun or a raw-response
fixture. The report identifies one Netcraze Hopper SE **NC-3812**, display
version **5.1.6**, exact observed build **`5.01.C.6.0-1`**, channel **stable**.
The exact build was read from the device, not inferred from the display version.

The report used clean branch `main` at
[`6e416de00a201e65b05ef7358b8f4d369f092665`](https://github.com/AlexMyln/netcraze-xray-routerkit/commit/6e416de00a201e65b05ef7358b8f4d369f092665)
with Python 3.14.7. The separate repository review used
[`084a42fbce13997948b9586ac8c2a9b684a03742`](https://github.com/AlexMyln/netcraze-xray-routerkit/commit/084a42fbce13997948b9586ac8c2a9b684a03742).
[Comparing these commits](https://github.com/AlexMyln/netcraze-xray-routerkit/compare/6e416de00a201e65b05ef7358b8f4d369f092665...084a42fbce13997948b9586ac8c2a9b684a03742)
shows the latter is five commits ahead, with the former as the merge base.
Those changes add external native transactions and live-install orchestration,
including the runtime-execution-locus safety fix. Results for the older
checkout do not verify those later paths.

## Method

The report inventoried 84 available Netcraze MCP tools; it does not claim that
all 84 were executed. It used typed reads and individual `cli_show` commands
whose syntax was first checked with `kb_command`. No native configuration
writes, `cli`/`cli_batch`, browser, SSH, full configuration snapshots, active
HTTP/DNS probes, service changes or firmware actions were used.

## Observations supported by the report

| Observation | Reported evidence | Boundary |
| --- | --- | --- |
| Exact model/build and live management | `list_devices`, `get_device_info`, `show version`, `device_is_available` | One device observation; no model-family generalization |
| WAN and LAN available | `get_internet_status`: PPPoE0 and Bridge0 up/connected/link up; main default route via PPPoE0 | No comparable pre-update snapshot; unchanged state is not proven |
| Native proxy component and three Proxy interfaces present | Installed-component list and status; Proxy0/1/2 up/connected/link up with loopback remote endpoints | SOCKS protocol and exact ports were not exposed |
| Proxy-only policy default routes | `list_ip_policies`: Policy0/1/2 each has its only IPv4 and IPv6 default routes through the corresponding Proxy0/1/2 | Non-default service routes also exist; actual client traffic was not tested |
| Current client assignments | 18 registered records: one assigned to Policy0, none to Policy1/2, 17 without an explicit policy | The assigned record was inactive; intended assignments and Default policy contents were not independently proven |
| HTTPS DNS configured | `show dns-proxy`: two HTTPS DNS servers for System and each of the three policies | Configuration is not successful DNS or proof of a policy-reachable resolver path |
| USB present | `get_usb_status`; storage/opkg components installed | Does not establish filesystem, `/opt`, Entware or Xray |

The owner's statement that service is working remains a user observation,
separate from the checks above. No private device identifier, client identity,
address, credential or complete configuration is included here.

## Offline checks reported for `6e416de`

- Platform compatibility validator: exit 0.
- The initial five-module unittest command failed to import
  `tests.test_routerkit_netcraze_external` and
  `tests.test_routerkit_live_install`, which did not exist in that checkout.
  Both modules were added in the comparison above; these import errors are a
  checkout/test-list mismatch, not evidence of a device compatibility defect.
- Rerunning the three existing modules (`test_routerkit_netcraze_live_entrypoint`,
  `test_routerkit_netcraze_live`, `test_routerkit_netcraze_live_compat`) passed
  **14 tests**. Expected negative contract cases are not live failures.
- No tracked source changes were reported after the tests. These are offline
  software checks, not tests of those code paths against the device.

## Compatibility conclusion and unchanged gates

No incompatible response or command was identified within the reported reads.
This supports a preliminary read-only compatibility observation only. The
report contains no full `show running-config`, so it does not exercise the
RouterKit live parser or prove exact semantic reuse of the existing objects.
There is no observed defect requiring a code adaptation from this evidence.

The supported hardware contract remains **`nc3812-netcrazeos-5.1.5`** in the
live adapter, external transaction path and live-install schemas. Do not label
the observed 5.1.6 device with the 5.1.5 contract to bypass the gate. The dated
[public-source compatibility manifest](../../hardware/routerkit-platform-compatibility.v1.json)
is unchanged; this report is not a vendor release source or hardware-canary
completion. It does not close the brownfield live-install or clean-hardware
validation requirements.

## Evidence still needed for stronger claims

1. Pin the intended RouterKit commit and run its applicable offline suites;
   keep their result separate from live firmware evidence.
2. With separately authorized protected configuration reading, establish exact
   SOCKS5 loopback ports, Proxy/Policy semantics, selected assignments and the
   Default guard. Exercise the actual RouterKit parser and verifier off-device
   without mislabeling the hardware contract or issuing writes.
3. Through a suitable authorized target-side read path, prove architecture,
   external EXT4 `/opt`, trusted Entware, pinned Xray identity, running process,
   listener ownership/bindings and autostart. MCP status alone is insufficient.
4. With authorization for active checks and an active selected client, verify
   domain DNS and HTTPS through the intended policy, DNS reachability from that
   policy, and the intended direct path for unselected clients.
5. Before any 5.1.6 write-contract promotion, separately review its exact
   contract and complete the required authorized write/readback/save,
   rollback, idempotence and reboot/recovery checks. Existing healthy service
   is not a reason to reinstall or run these disruptive checks automatically.

See [platform compatibility](platform-compatibility.md) and
[external native transport](../architecture/netcraze-external-transport.md).
