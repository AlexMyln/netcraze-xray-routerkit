# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
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

### Security
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
