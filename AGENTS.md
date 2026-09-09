# RouterKit Agent Operating Contract

This file is **normative guidance for AI/automation agents** working with this repository or performing a RouterKit installation on Netcraze/Keenetic-style routers.

The goal is to complete the operator's requested installation safely and efficiently. Do **not** turn a production installation into an open-ended audit, research project, or sequence of confirmations for facts that are already established.

## 1. Priority order

When working on a live installation, use this order of priorities:

1. Complete the operator's declared objective.
2. Preserve production connectivity and an available recovery path.
3. Keep RouterKit integrity/security gates intact.
4. Minimize unnecessary interruptions, duplicate discovery, and repeated audits.
5. Improve the repository only when live evidence exposes a concrete defect or missing compatibility case.

An operator's explicit authorization for an action remains valid for that scope. Do not repeatedly ask for permission for the same already-authorized reboot, package/component install, write, or continuation step.

## 2. One discovery pass, then act

Perform one bounded discovery/preflight pass for the current stage. Reuse confirmed facts until one of these happens:

- the router reboots;
- firmware/components change;
- `/opt` or storage changes;
- RouterKit/Xray is replaced;
- the operator reports drift;
- a command returns evidence that contradicts the stored state.

A PASS is evidence. Do not re-run the same audit merely to increase confidence.

Do not create loops such as:

`preflight -> audit -> audit of audit -> new preflight -> confirmation of previous PASS`

If a fact is already proven and no relevant state changed, continue.

## 3. Happy-path installation flow

For a full installation whose goal includes per-device VPN policies, prefer this sequence:

1. Confirm the ordinary router network is stable.
2. Identify the external USB target unambiguously.
3. Prepare EXT4 storage and Entware at literal `/opt`.
4. Confirm `aarch64`/`arm64` and trusted `/opt`-scoped `opkg`.
5. Run RouterKit bootstrap using the repository-pinned Xray artifact.
6. Acquire the profile source through hidden input, protected file, or dedicated `ROUTERKIT_*` environment variable.
7. Run generation, strict plan, preflight, backup, install, and healthcheck.
8. Enable RouterKit autostart only after healthcheck passes.
9. If per-device routing is part of the requested goal, verify/install the native **Proxy client** system component before declaring the job complete.
10. Use one controlled reboot, when authorized, to prove USB -> `/opt` -> Entware -> Xray -> autostart recovery.
11. Create native loopback SOCKS proxy interfaces and native Internet access policies.
12. Assign only operator-selected clients.
13. Verify production networking and the selected VPN clients.
14. Perform SSH hardening as a follow-up without locking out the operator.

Do not discover required Proxy-client support only after declaring RouterKit finished when per-device policies were part of the original objective.

## 4. Stop conditions are narrow

Stop and ask the operator only when at least one of the following is true:

- a destructive target is ambiguous (for example, the external USB device cannot be distinguished from internal storage);
- a required secret/profile source must be entered by the operator;
- a checksum, pinned artifact identity, architecture gate, or semantic Xray version is genuinely wrong;
- `/opt`, Entware, PPPoE, LAN, or RMM becomes unavailable and the next write could worsen recovery;
- hardware behavior contradicts the known write contract and a safe next command cannot be determined;
- an already-authorized action would exceed the scope the operator granted.

Do **not** stop merely because:

- a known-good value is represented by a different but equivalent status string;
- an informational verifier has a false-negative while independent live evidence proves the invariant;
- the operator moved to another network while RMM still provides the required management transport;
- a stage completed successfully and the next stage is already in the authorized installation goal;
- CI has not yet run after a minimal local compatibility fix whose targeted tests pass, unless the operator explicitly required CI first.

## 5. Never weaken the real security gates

These checks are real safety boundaries and must not be bypassed:

- supported `aarch64`/`arm64` execution path;
- literal `/opt` on the intended external storage;
- trusted `/opt`-scoped `opkg`;
- HTTPS/TLS/destination policy for RouterKit downloads;
- repository-pinned Xray artifact;
- exact archive SHA-256 verification;
- exact semantic Xray release verification;
- safe archive extraction;
- transactional Xray replacement/rollback behavior;
- loopback-only RouterKit SOCKS listeners;
- no `xkeen -start`, TPROXY, REDIRECT, transparent firewall mode, or broad default routing unless a separate project explicitly requires it.

A parser bug is not permission to disable a gate. Fix the parser narrowly, add regression coverage, and keep the invariant.

## 6. Live compatibility bugs: fix narrowly and continue

When live hardware exposes a narrow compatibility difference while the underlying invariant is proven:

1. capture the non-secret evidence;
2. make the smallest strict compatibility change;
3. add a regression test for the new valid form and retain negative cases;
4. run targeted tests;
5. continue the authorized installation after PASS;
6. commit/publish the fix without production secrets or artifacts.

Do not replace strict checks with broad substring tests such as `"installed" in output` or permissive version-prefix matching.

Known valid compatibility evidence from the NC-3812 hardware run on 2026-09-09:

- Entware may report an installed package as `Status: install user installed`; `Status: install ok installed` remains valid too.
- Official Xray version output may append the standard banner/build metadata to the exact pinned semantic version.
- Netcraze Hopper SE NC-3812 on NetcrazeOS 5.1.5 is a proven `aarch64` RouterKit installation target with EXT4 USB -> `/opt`, Entware, Xray, and successful post-reboot recovery.

Treat these as established evidence, not fresh research questions on every future installation of the same contract.

## 7. Profile sources and shortlinks

Secrets must never be printed into chat, logs, argv, reports, Git commits, or public evidence.

Preferred inputs:

- hidden interactive input;
- owner-only protected file;
- dedicated `ROUTERKIT_*` environment variable.

HTTPS shortlinks are legitimate profile sources. If a shortlink resolves to an HTML landing page instead of raw/Base64 VLESS data:

- do not ask the operator to manually reconstruct a VLESS URI if the normal subscription action can supply the real source;
- use a safe browser/subscription flow to obtain the actual subscription source without exposing it;
- feed the resulting source back into RouterKit's protected resolver/parser;
- preserve RouterKit HTTPS/TLS/SSRF protections.

Do not use `curl -k`, disabled TLS verification, or secret-bearing command-line arguments as shortcuts.

## 8. Reboots are a tool, not an automatic blocker

If a required native system component needs reboot and the operator has authorized completing the full installation, a controlled reboot is part of the job, not a reason to stop.

Before reboot, record only the minimal recovery state needed. After reboot, prove at least:

- RMM/management returns;
- PPPoE/default route/DNS are healthy;
- LAN/Wi-Fi remain healthy;
- external EXT4 storage is present;
- `/opt` is mounted from the intended USB device;
- Entware is available;
- Xray is running;
- expected RouterKit listeners are present on `127.0.0.1` only.

If the operator explicitly forbids reboot, honor that restriction. Do not invent a reboot prohibition that the operator did not request.

## 9. Verification: prove the invariant, not the tool's mood

A verifier is evidence, not reality itself.

If an internal verifier reports FAIL but independent bounded checks prove the exact intended invariant, classify the service state from the evidence and separately record/fix the verifier defect.

For Xray runtime, acceptable independent evidence includes:

- the expected Xray PID/executable identity;
- listener ownership by that Xray process;
- listeners bound only to expected loopback ports;
- successful HTTPS traffic through the expected SOCKS endpoints;
- successful recovery after reboot when reboot proof is required.

Do not tear down or reinstall a working service solely because a diagnostic helper has a known implementation limit.

Current known limitation from the 2026-09-09 NC-3812 run: the autostart verifier may false-fail when Xray owns more file descriptors than its descriptor scan limit. Track/fix the verifier, but do not call a proven running Xray instance down based on that failure alone.

## 10. Native Netcraze/Keenetic client routing

RouterKit's local SOCKS endpoints are not by themselves client routing.

For per-device use, prefer native router objects:

`Xray loopback SOCKS -> native Proxy interface -> native Internet access policy -> selected registered client`

Rules:

- create one native Proxy interface per RouterKit SOCKS endpoint;
- create one native policy per desired profile;
- use only the corresponding Proxy interface as that VPN policy's Internet path;
- assign only explicitly selected clients;
- do not assign an entire segment or all clients unless the operator explicitly requests it;
- keep direct PPPoE as the normal path for unselected clients;
- do not claim `Default policy unchanged` if the operator explicitly authorized adding proxy fallbacks to Default; report the actual resulting state precisely.

Never substitute iptables marks, TPROXY, REDIRECT, or hand-edited configuration files for a known native policy mechanism merely because it is faster.

## 11. Backups and rollback

Take the relevant pre-change backup/checkpoint once when available. Verify it when practical.

Do not repeatedly block the same installation on a backup the operator explicitly waived or on a post-cutover downloadable artifact when the router's saved startup configuration and recovery path are already accepted for the current scope. Record the limitation accurately and continue within the operator's risk decision.

Never restore a whole startup configuration from a different hardware model.

## 12. Remote management

If RMM remains online, local LAN reachability from the operator's laptop is not automatically required.

Use RMM for bounded remote checks/writes when it provides the needed transport. Do not require the operator to return physically merely because the laptop is no longer on the router's LAN.

For SSH hardening:

- install only the operator's public key;
- never read/copy the private key;
- verify a real key login before disabling password authentication;
- if key login cannot be tested remotely, leave password authentication intact and record the follow-up.

## 13. Reporting discipline

During an authorized multi-stage installation, continue through successful stages without sending a full report after every step.

Interrupt the operator only for a real stop condition or required selection/input.

Final reporting should be factual and compact:

- what was installed/changed;
- what was verified live;
- what clients/policies changed;
- what remained direct/default;
- any real unresolved risks or follow-ups;
- any RouterKit defects discovered and their fix/test status.

Do not label an otherwise functioning installation `PARTIAL` solely because a secondary verifier has a known false-negative. Separate **service state** from **tooling defect state**.

## 14. Repository-improvement rule

After a live installation exposes repeatable friction, turn the lesson into one of:

- code + regression test;
- this agent contract;
- concise operator documentation;
- a tracked issue for a real unresolved defect.

The objective is that the next agent follows a shorter proven path instead of rediscovering the same facts through new audits.
