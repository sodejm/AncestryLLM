# Changelog

All notable changes to AncestryLLM are recorded here. The project follows
[Semantic Versioning 2.0.0](https://semver.org/) and uses the categories from
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Release automation and evidence gates for future CLI releases.

### Changed

- Routed GEDCOM merge, incremental update, and quality assistance exclusively
  through the modular provider service, including operational named profiles,
  shared Ollama clients, bounded scheduling, exact-result single-flight
  caching, and privacy-minimal audit telemetry.

### Security

- Removed legacy direct GEDCOM/OCR provider paths and environment-based
  selection. Provider `none` remains socket-free with credentials, SDKs, and
  profiles configured; cloud consent is bound to the exact profile/endpoint.
- Classified non-loopback Ollama endpoints as remote, bounded single-flight
  admissions, made retry backoff cancellation-aware, prevented duplicate
  retained cache payloads, and rejected non-finite or low-confidence automatic
  identity merges.
- Required possibly-living-person consent for every remote identity prompt
  because bounded relative context can include people whose living status is
  unknown.

## [0.2.0] - Unreleased

### Added

- Local-first one-shot CLI and prompt-toolkit/Rich interactive console.
- Immutable RootsMagic query and loss-minimizing GEDCOM export workflows.
- GEDCOM merge, subtree, quality, incremental update, and rebase commands.
- Explicit modular LLM providers with consent, timeout, validation, and
  network-free `provider=none` behavior.
- Encrypted SQLCipher research workspace, OS-keyring secrets, diagnostics, and
  encrypted backups.
- Stable coded errors, JSON output, secure history, multiline input, bounded
  background jobs, and sanitized progress reporting.
- Cooperative background-job cancellation, including Ctrl-C foreground
  cancellation, `jobs cancel JOB_ID`, cancellation state in job snapshots, and
  the stable `JOB_CANCELLED` and `REPL_EXIT_DECISION_REQUIRED` codes.

### Changed

- Interactive exit now requires an explicit `wait`, `cancel`, or `stay`
  decision while jobs are active; EOF waits rather than cancelling work.
- Staged output publication and encrypted database backups defer cancellation
  across the complete commit-or-rollback boundary, preventing partial output
  bundles, and REPL shutdown drains workers before closing presentation,
  provider, cache, and database resources.

### Security

- Added threat modeling, secret scanning, Semgrep, CodeQL, dependency auditing,
  repository artifact guards, and CycloneDX SBOM generation.

[Unreleased]: https://github.com/sodejm/AncestryLLM/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/sodejm/AncestryLLM/releases/tag/v0.2.0
