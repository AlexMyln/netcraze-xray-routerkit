# Issue #41 implementation note

## Software delivery

Branch scope adds one first-class orchestration command:

```text
python3 scripts/routerkit.py live-install plan|apply|resume|status
```

It composes the repository's existing bootstrap, protected profile-source,
generator, strict planner, backup/install, healthcheck, autostart, endpoint
manifest, local routing reconcile, local ndmc adapter, and external transaction
protocol. No RouterKit-owned semantics are copied into a transport agent, no
browser fallback is introduced, and `setup` remains a compatible independent
command.

The state machine publishes only owner-private
`routerkit.live-install.v1` receipts. It supports one discovery fingerprint per
epoch, explicit component/reboot/native epoch transitions, one initial scope
confirmation, exact failure stop routing, resume from completed stages, and a
distinct severe exit when an existing transactional module reports unproven
rollback.

The native stage supports `local-ndmc` and `external`. External mode uses the
existing `routerkit.netcraze.external-transaction.v1` packet without Entware
SSH. NOOP stays `commands=[]`, `write_required=false`, and
`save_required=false`; mutation requires fresh pre/running/saved RouterKit
verification and never trusts transport success.

The final gates reject a protected DNS path that is bound only to an interface
absent from the selected Proxy policy, accept existing policy-reachable or
`Any`/unbound protected DNS, require selected-device assignment, and require
real selected-client domain DNS plus HTTPS. Missing typed transports return
explicit handoffs instead of false PASS.

## Validation boundary

The PR may be described as **software-complete orchestration** only when its
focused tests, full local suite, and GitHub CI are green. Unit/fixture tests do
not prove the full one-command hardware path.

After merge, issue #41 still requires exactly one brownfield rerun on the
already proven NC-3812 / NetcrazeOS 5.1.5 contract. That run must exercise the
real command/resume handoffs through final client-domain acceptance without
changing the semantic contract. Clean spare-hardware coverage remains issue
#16 and is not a prerequisite for this software PR.
