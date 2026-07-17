# Limited-window compatibility-patch template

Use this template only after P4 records:

```text
OFF_DEVICE_NARROW_PATCH_REQUIRED
```

Router writes stop before patch work. The patch is developed and reviewed off-device.

Private evidence schema v1 does not accept compatibility-patch execution. A hardware session manifest remains valid only when `execution_source=released_baseline`, both commits equal `c8f697635c93584e85e76a1d734f8fa797a76b51`, and `compatibility_patch=null`. A reviewed patch requires a future schema version with a strict receipt; an arbitrary 40-hex patch commit is not evidence.

## Observed contract gap

- Private evidence reference: `<opaque private reference>`
- Affected phase/check: `<P2/P3 check ID>`
- Sanitized schema difference: `<field/cardinality/capability summary>`
- Why alpha.16 cannot proceed safely: `<summary>`
- Default policy and router state changed before stop: `false`

## Maximum allowed scope

Select one:

- [ ] parser mapping for one observed read-only schema;
- [ ] interface-specific read adapter behind an explicit hardware contract;
- [ ] exact field normalization;
- [ ] capability detection;
- [ ] one disposable write-request serialization behind a disabled-by-default test gate.

The patch must not include:

- broad refactor or unrelated cleanup;
- production write enablement;
- generic shell or command runner;
- browser automation;
- firewall/default-policy behavior;
- secrets or real device identifiers in fixtures;
- direct patching on the router;
- normal `routerkit setup` integration.

## Required synthetic fixture

- Fixture path: `<path>`
- Synthetic reserved identifiers only: [ ]
- Mirrors only the minimum observed semantics: [ ]
- Contains no raw private evidence: [ ]
- Unknown fields and ambiguous states fail closed: [ ]

## Required tests

- [ ] focused parser/adapter/serialization test;
- [ ] malformed and unknown-field tests;
- [ ] default-policy and unrelated-state guards;
- [ ] no-live static guard;
- [ ] mutation proving the guard detects the new forbidden primitive;
- [ ] full unit suite;
- [ ] documentation synchronization where needed.

## Independent delta review

- Reviewer: `<local review reference>`
- Findings by severity: `<none or list>`
- Unresolved findings: `0`
- Receipt base commit: `c8f697635c93584e85e76a1d734f8fa797a76b51`
- Receipt patch commit: `<reviewed execution commit>`
- Review verdict: `READY_FOR_HARDWARE_REENTRY`
- Focused tests passed: `<true>`
- Full tests passed: `<true>`
- Static guard passed: `<true>`
- Explicit user authorization: `<true>`
- Patch stays within the selected class: [ ]
- No vendor command or endpoint invented: [ ]
- No secret material entered the repository: [ ]

## Hardware reentry gate

- [ ] focused and full checks pass;
- [ ] independent review has zero unresolved findings;
- [ ] explicit operator authorization to resume;
- [ ] backup and prior state still valid;
- [ ] at least 30 minutes remain;
- [ ] protected 15-minute cleanup reserve remains;
- [ ] resume at P1/P2/P3 as required, never directly at a write phase.

If any gate fails, use `PARTIAL_NEEDS_OFF_DEVICE_PATCH` and proceed to cleanup.
