# Postmortem: NC-3812 live RouterKit installation — 2026-09-09

This is a **sanitized engineering postmortem** of the first full RouterKit installation completed on a Netcraze Hopper SE NC-3812 in a real production network.

No customer secrets, subscription material, MAC-address inventory, startup configuration, or private hostnames are included.

## 1. Outcome

Service outcome:

```text
INSTALLATION_RESULT=PASS
VPN_SERVICE=PASS
CLIENT_ROUTING=PASS
AFTER_REBOOT=PASS
```

Tooling outcome:

```text
OPKG_COMPATIBILITY_BUG=FIXED
XRAY_VERSION_BANNER_BUG=FIXED
AUTOSTART_VERIFIER_FD_LIMIT=OPEN_ISSUE
```

The router remained healthy after the installation and controlled reboot:

- PPPoE/default route healthy;
- DNS healthy;
- LAN/Wi-Fi healthy;
- RMM management healthy;
- external EXT4 USB recovered;
- `/opt` recovered from USB;
- Entware recovered;
- Xray automatically restarted;
- three loopback SOCKS listeners recovered;
- native Netcraze Proxy/policy routing worked for selected clients.

## 2. Hardware/software evidence

Observed live target:

```text
Model: Netcraze Hopper SE NC-3812
NetcrazeOS: 5.1.5 / 5.01.C.5.0-0
Architecture: aarch64
Storage: external EXT4 USB
Entware mount: /dev/sda1 -> /opt
Xray: 26.3.27, repository-pinned artifact
```

The live run establishes this model/firmware combination as a real working RouterKit target for the tested path. It should not be rediscovered from zero on every future installation unless new evidence contradicts the recorded contract.

## 3. What worked well

### RouterKit security/integrity gates

The important gates did their job and were retained throughout:

- architecture validation;
- literal external `/opt` path;
- trusted `/opt`-scoped `opkg`;
- HTTPS/TLS/destination controls;
- repository-pinned Xray archive;
- SHA-256 verification;
- safe extraction;
- semantic Xray release validation;
- transactional replacement/rollback model;
- loopback-only SOCKS listeners.

The two compatibility fixes made during the run narrowed parsers without weakening the underlying invariants.

### RMM continuation

After the operator left the site, RMM remained sufficient for the remaining bounded checks and writes. Local LAN reachability from the operator laptop was not required for the rest of the RouterKit work.

### Controlled reboot

One required Proxy-client component installation provided a useful opportunity to perform the real after-reboot proof. The single reboot validated the actual production chain rather than a synthetic restart-only assumption:

```text
USB -> /opt -> Entware -> Xray -> autostart -> listeners
```

## 4. Friction that should not recur

### 4.1 OPKG status parser was too literal

The bootstrap code accepted only:

```text
Status: install ok installed
```

The live Entware feed returned the also-valid form:

```text
Status: install user installed
```

The first implementation treated this representation difference as a missing-package condition even though the required packages were installed.

Resolution:

- accept exactly the two confirmed installed forms;
- still require successful `opkg status` return code;
- keep negative/fail-closed behavior;
- add regression coverage.

Lesson: a parser compatibility difference is not a reason to bypass the package gate, and it is not a reason to stop the whole maintenance window after the invariant is proven.

### 4.2 Xray version gate was too literal

The bootstrap validator expected the first version line to equal only:

```text
Xray 26.3.27
```

The official Xray binary emitted the exact semantic release plus the standard banner/build metadata.

Resolution:

- keep the exact pinned semantic release requirement;
- accept the official banner/build suffix only;
- reject arbitrary suffixes and other semantic releases;
- keep the exact pinned archive SHA-256 gate;
- add regression tests.

Lesson: human-readable build metadata must not be confused with artifact/version drift when both the pinned archive hash and semantic release are proven.

### 4.3 Shortlink resolution reached an HTML landing page

The operator had a working HTTPS shortlink rather than a direct VLESS URI. RouterKit's protected HTTPS resolver reached a valid response, but the body was an HTML subscription page rather than raw/Base64 VLESS.

The successful path was:

```text
HTTPS shortlink
-> protected/browser subscription action
-> actual subscription source
-> protected RouterKit resolver
-> Base64 VLESS payload
-> normal node selection/setup
```

Lesson: do not require an operator to manually reconstruct or expose a direct `vless://` link when the service's normal subscription action can provide the actual source safely.

### 4.4 Native Proxy client was checked too late

RouterKit/Xray was already healthy when the installation reached the per-device routing stage and discovered that the Netcraze **Proxy client** system component was not installed. Installing it required a router reboot.

This was avoidable scheduling friction.

New rule: if per-device VPN is part of the declared goal, verify/install the Proxy client component during the initial bounded preflight or before declaring the RouterKit runtime stage complete.

### 4.5 Too many audit/approval opportunities

The installation exposed a general process failure mode: already-proven states were at risk of being repeatedly re-audited, and normal next steps were at risk of being treated as new approval boundaries.

The production rule is now:

- one bounded discovery pass per state epoch;
- a PASS remains evidence until relevant state changes;
- an operator authorization remains valid for its declared scope;
- successful stages continue without a full report between each one;
- stop only on narrow real blockers.

This rule is now normative in root `AGENTS.md` and operationalized in `docs/live-install-runbook.md`.

### 4.6 Autostart verifier false-negative above 256 FDs

After the successful reboot, Xray was independently proven running and owned the expected loopback listeners, but `routerkit autostart --verify` false-failed because its socket-owner scan stops after 256 file descriptors. The live Xray process had substantially more descriptors.

This is a **tooling verifier defect**, not a service failure.

Tracked separately in issue #37. The fix must retain bounded/resource-safe `/proc` inspection while supporting realistic Xray FD counts and preserving exact process/listener ownership checks.

Lesson: classify service state from exact independent evidence when a diagnostic helper has a known implementation limit; track the helper bug separately.

## 5. Native client routing result

Three RouterKit SOCKS endpoints were connected to three native Netcraze Proxy interfaces and policies. Selected devices were assigned to the requested VPN policy; unselected devices remained on the ordinary direct Internet path.

The live run also proved an important reporting rule: if the operator explicitly changes Default by adding proxy fallbacks, the final report must describe that change precisely rather than claiming `DEFAULT_POLICY_UNCHANGED=TRUE`.

## 6. Changes made from this run

Completed:

- root `AGENTS.md` with hard agent operating rules;
- OPKG installed-status compatibility fix + regression tests;
- Xray official version-banner compatibility fix + regression tests;
- live-install production runbook (EN/RU);
- this sanitized postmortem;
- issue #37 for the autostart FD-scan false-negative.

Process changes:

- Proxy client moved into the early full-install checklist;
- controlled reboot treated as an execution step when authorized, not an automatic blocker;
- shortlink/HTML subscription handling included in the operational path;
- RMM explicitly accepted as management transport after the operator leaves the LAN;
- service result separated from tooling-defect result.

## 7. Remaining work

This successful production installation is valuable hardware evidence, but it does **not** complete every item in the wider hardware-validation matrix.

Still open:

- fix issue #37 and add >256-FD regression coverage;
- prove a real SSH key login before disabling password authentication on the installed router;
- verify a downloadable post-cutover router backup when practical;
- complete the broader idempotency/failure-injection/rollback matrix in issue #16;
- finish the live Netcraze write adapter/automation work in issue #15;
- fold the proven live sequence into the one-command setup epic #5 so future operators do not need to manually stitch together RouterKit and native policy stages.

## 8. Required future behavior

For the same proven NC-3812/NetcrazeOS path, future agents should start from the known live contract and execute the bounded happy path.

They should **not** repeat the 2026-09-09 discovery work merely because the old source-review documentation called the model untested before this installation.

New contradictory evidence should reopen investigation. Familiar successful evidence should shorten the next run.
