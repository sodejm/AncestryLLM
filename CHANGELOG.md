# Changelog

All notable changes to AncestryLLM are recorded here. The project follows
[Semantic Versioning 2.0.0](https://semver.org/) and uses the categories from
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Release automation and evidence gates for future CLI releases.

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

### Security

- Added threat modeling, secret scanning, Semgrep, CodeQL, dependency auditing,
  repository artifact guards, and CycloneDX SBOM generation.

[Unreleased]: https://github.com/sodejm/AncestryLLM/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/sodejm/AncestryLLM/releases/tag/v0.2.0
