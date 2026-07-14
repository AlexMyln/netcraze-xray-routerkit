# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Security
- Bootstrap now coordinates SIGINT through the same verified replacement-recovery boundary, closing the atomic-replacement cancellation gap.
- Bootstrap now treats signal-time rollback as a recovery critical section and never suppresses an unproven binary rollback.
- Bootstrap artifact acquisition is HTTPS-only, proxy-free, destination-validated, bounded, checksum-gated, and safely extracted.
- Unified setup now consumes dedicated `ROUTERKIT_*` source variables and removes them from resolver workers and all later generator, plan, and apply subprocess environments.
- Setup now coordinates child shutdown and private profile cleanup for catchable SIGTERM/SIGHUP termination; uncatchable process or host termination remains a documented residual risk.

### Added
- Transactional standalone bootstrap apply for fixed Entware prerequisites and the manifest-pinned Xray artifact.
- Verified existing-binary backup, atomic replacement, post-install validation, rollback, and provenance receipt.
- Default setup integration for hidden/local/HTTPS profile sources and primary/fallback selection.
- Private setup-owned profile workspace with post-generator cleanup.
- Explicit secure existing-profiles reuse and legacy-wizard compatibility modes.
- Secret-free abstract setup dry-run rendering.
- Bounded HTTPS subscription and redirect-based shortlink resolution.
- Per-hop DNS/address validation with pinned-IP TLS connections.
- Secret-safe network acquisition shared by profile-source and generator paths.
- Offline DNS, TLS, redirect, timeout, and SSRF-focused tests.
- Python 3.8 and primary-runtime CI coverage for the explicit destination-address policy.
- Offline secret-safe profile-source parser for raw, newline, Base64, and JSON VLESS payloads.
- Interactive primary/fallback node selection with private profiles output.
- Reusable parser shared by the profile-source tool and Xray config generator.
- Offline profile-source fixtures and security-focused tests.
- Hardened local source-file validation, generic URI-scheme rejection, and atomic no-clobber private output publication.
- Read-only bootstrap environment planner with text and JSON output.
- Strict pinned-Xray artifact manifest and validation.
- Bootstrap execution-model ADR and artifact-pin verification documentation.
- Offline synthetic bootstrap fixtures and unit tests.
- Initial `setup` orchestrator for wizard, generation, strict planning, explicit apply confirmation, and the hardened install pipeline.
- Setup dry-run rendering and failure-propagation tests.
- Wizard-only mode for orchestration without duplicate generator prompts.
- Hardened `install --apply` pipeline with strict plan, preflight, backup, install, and healthcheck steps.
- Apply summaries and rollback hints for failed install/healthcheck flows.
- CLI tests for apply pipeline ordering, dry-run behavior, failure handling, and skip flags.
- Safe `install` subcommand in the unified CLI.
- Plan-only install mode by default.
- Explicit `--apply` gate for install operations.
- Unified `scripts/routerkit.py` CLI entrypoint for wizard, generate, plan, preflight, healthcheck, and backup commands.
- CLI tests for command construction and dry-run behavior.
- Dry-run install plan script for previewing routerkit install operations.
- Unit tests for install plan generation and secret suppression.
- Unit tests for the config generator and local profiles wizard.
- CI test discovery for the Python test suite.
- Guided installer foundation: preflight script and interactive local profiles wizard.
- Guided installer documentation in English and Russian.

### Changed
- `routerkit bootstrap` remains read-only by default; writes require explicit `--apply` and confirmation.
- `routerkit setup` no longer silently reuses a current-directory `profiles.json`.
- Profile-source cancellation messaging no longer makes an unconditional no-write claim.
- Clarified HTTPS resolver browser-redirect, cleanup, address-policy, compatibility-test, and local-file security boundaries after independent security review.
- HTTPS source values now normalize only outer whitespace at the single-URL boundary; protected LF/CRLF files work while raw/offline payloads remain unchanged.
- Bootstrap plans retain explicit command-to-Entware-package mappings, including `sha256sum -> coreutils-sha256sum`; standalone apply now installs only missing fixed prerequisites, while the initial arm64/aarch64 package names still require hardware validation.

### Security
- HTTPS resolution now uses fixed reviewed special-purpose CIDR tables plus standard-library defense-in-depth checks, rejects IPv4-mapped/NAT64/Teredo/6to4/ORCHID forms conservatively, and preserves ordinary cancellation while attempting bounded best-effort resource cleanup.
- Unified setup now suppresses generator stdout and stderr so subscription-derived or credential-derived details do not appear in its transcript.

## [0.1.2] - 2026-07-09

### Added
- GitHub issue templates for bug reports and feature requests.
- Public changelog.

### Changed
- Documentation polish for a cleaner public repository presentation.

### Security
- No real subscription URLs, VLESS links, router configs, generated Xray configs, or backup archives are included.

## [0.1.1] - 2026-07-09

### Added
- Russian README.
- Russian from-zero installation guide.
- Russian Web UI guide.
- Russian troubleshooting guide.
- Russian end-user instruction.
- Russian announcement draft.
- Guided one-click installer roadmap.

### Changed
- Polished Russian documentation wording.
- Removed internal Codex publishing prompt from public docs.
- Replaced provider-like examples with generic profile names.

### Security
- Re-ran strict secret and critical-data audit.
- Confirmed no real subscription URLs, VLESS links, generated configs, router backups, or startup-config files are tracked.

## [0.1.0] - 2026-07-08

### Added
- Initial public starter kit.
- Xray direct-run init script for Entware.
- Multi-profile local SOCKS config generator.
- Healthcheck script.
- Backup script.
- Netcraze/Keenetic Web UI guide.
- Restore and troubleshooting docs.
- CI syntax checks and repository secret guard.

### Security
- Secret-safe examples only.
- `.gitignore` rules for generated configs, backups, profiles, archives, and local secrets.
