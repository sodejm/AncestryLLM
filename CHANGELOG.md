# Changelog

All notable changes to AncestryLLM are recorded here. The project follows
[Semantic Versioning 2.0.0](https://semver.org/) and uses the categories from
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.6.0] - 2026-08-14

### Added

- Desktop Tasks destination backed by strict job snapshots, bounded
  sender-owned event subscriptions, cooperative cancellation, coded redacted
  errors, reload resynchronization, and grant-mediated artifact presentation.
- Synchronous transient-chat service and authenticated private API
  with exact named-profile/model selection, fresh policy and consent checks,
  strict resource limits, memory-only content, privacy-minimal audit metadata,
  and no tool, filesystem, database, shell, plugin, genealogy, or autonomous
  authority.
- Verified repository-local `uv` bootstrap, purpose-specific PEP 735 dependency
  groups, system-Python-only environment ownership, and advisory `ty` evidence
  alongside authoritative strict mypy checking.
- Source-level desktop settings, provider-consent, task-lifecycle, transient-chat,
  local-runtime, and container-control foundations with fixed Main-owned
  boundaries and explicit packaged-runtime gates.

### Changed

- Hardened packaged sidecar integrity, startup, shutdown, SQLCipher migration,
  process-tree supervision, and native Windows ARM64 and Linux verification.
- Expanded Ruff 0.16.1 rules in reviewed batches while retaining provider-import,
  Pydantic, GEDCOM, cross-platform, and cancellation regression contracts.
- Completed the Diátaxis documentation cutover, deterministic Pages and Wiki
  publishing contracts, and fictional provider-none screenshot evidence.
- Retained setuptools after the `uv_build` evaluation found artifact drift, and
  retained mypy after the `ty` advisory failed the complete cutover gate.

### Security

- Sanitized and canonicalized release SBOM evidence so it contains one project
  root, a complete deterministic dependency graph, and no local runner path or
  volatile document identity.
- Preserved network-free `provider=none`, explicit provider consent, immutable
  RootsMagic inputs, loss-minimal GEDCOM behavior, and fail-closed release gates.
- Kept application containers, remote hosting, publisher signing, automatic
  updating, and unsupported packaged desktop surfaces unavailable pending their
  separately documented evidence gates.

## [0.5.0] - 2026-08-04

### Added

- Electron desktop shell for macOS 15 arm64, macOS 15 x64, Windows 11 ARM64,
  and Ubuntu 24.04 x64, distributed as manual full installers.
- Authenticated FastAPI health and capability foundation for the loopback
  sidecar, consumed by the Electron host through a private local port.
- Desktop verification sidecar with immutable API-contract, IPC-sender
  validation, and exact-head evidence gates.
- Pre-1.0 binary-signing disclosure embedded in release notes and the release
  workflow for every 0.x desktop release.

### Changed

- Desktop package version tracks the Python distribution version at every
  release; `desktop/package.json` reports `0.5.0`.

## [0.4.0] - 2026-07-31

### Added

- Public RootsMagic `core`, `query`, and `export` boundaries for immutable
  source access, explicit-provider natural-language query orchestration, and
  schema-adaptive GEDCOM mapping/publication.
- A typed, JSON-safe `RootsMagicGedcomDocument` mapping result with an opaque
  source reference and structured loss report so callers can inspect mapped
  content without receiving host paths or writing output files.
- Public GEDCOM identity, quality, synchronization, parser, graph, and
  serialization seams for application-service composition.
- A physically separated, standard-library-only GEDCOM document model,
  physical-line parser, structural validator, and deterministic UTF-8-safe
  line serializer behind the supported parser and serialization façades.

### Changed

- `GedcomService` now depends on the declared public GEDCOM façades instead of
  importing the private engine and incremental synchronizer directly.
- `RootsMagicService` delegates natural-language query policy to
  `RootsMagicQueryService`, keeping source access deterministic and provider
  orchestration outside the immutable reader.
- RootsMagic mapping is separated from validation and atomic publication:
  `map()` performs deterministic in-memory conversion, while `export()` keeps
  source verification private, validates before staging, and owns output/report
  publication.
- GEDCOM compatibility orchestration now delegates document parsing,
  validation, and long-line serialization to the pure document kernel while
  retaining stable diagnostics, cancellation checkpoints, and output behavior.

### Security

- Architecture checks now enforce exact import gateways for private GEDCOM and
  RootsMagic compatibility kernels, including imports from within their owner
  packages.
- Architecture checks prohibit application, infrastructure, adapter, provider,
  publication, third-party, and compatibility-engine dependencies from the
  pure GEDCOM document kernel.
- Existing immutable RootsMagic source checks, explicit provider selection,
  and network-free `provider=none` behavior remain regression-tested across
  the new public boundaries.

## [0.3.0] - 2026-07-29

### Added

- One shared `CommandSpec` and dispatch-key inventory for both the one-shot CLI
  and prompt-toolkit/Rich REPL.
- A transport-neutral `CommandInvocation`/`CommandOutcome` contract and shared
  `CommandExecutor`, with stable error mapping and opaque artifact references.
- Framework-independent application ports, operation DTOs, and genealogy result
  contracts for future adapters to consume.
- A service-owned genealogy aggregate for canonical identity, provenance,
  deterministic change/conflict accounting, and quality findings.
- Exact release configuration and evidence gates for the 0.3.0 milestone and
  tracker.

### Changed

- Migrated CLI and REPL dispatch to the same immutable executor registry while
  preserving command grammar, JSON payloads, coded errors, consent, offline
  behavior, and artifact contracts established by 0.2.0.
- Routed GEDCOM merge, incremental update, and quality assistance exclusively
  through the modular provider service, including operational named profiles,
  shared Ollama clients, bounded scheduling, exact-result single-flight
  caching, and privacy-minimal audit telemetry.
- Reconciled architecture, console, user, contributor, module-authoring,
  versioning, release, and agent guidance around implemented 0.3.0 boundaries.

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

## [0.2.0] - 2026-07-29

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
- RootsMagic GEDCOM export now adapts to supported schema aliases, preserves
  duplicate source material deterministically, distinguishes portable and
  preservation profiles, and publishes the GEDCOM and sanitized loss report
  as one complete-or-rollback pair.
- Staged output publication and encrypted database backups defer cancellation
  across the complete commit-or-rollback boundary, preventing partial output
  bundles, and REPL shutdown drains workers before closing presentation,
  provider, cache, and database resources.

### Security

- Added threat modeling, secret scanning, Semgrep, CodeQL, dependency auditing,
  repository artifact guards, and CycloneDX SBOM generation.
- RootsMagic export now fails closed when shared records could disclose living
  people; accepts verified WAL generations, with matching SHM when present,
  through process-owned
  checkpoint/backup consolidation; rejects malformed, incomplete, busy,
  replaced, symbolic-link, or non-regular sidecars, rollback journals, and
  SHM files without a WAL; and bounds schema-assisted query prompts with the stable
  `ROOTSMAGIC_SCHEMA_PROMPT_TOO_LARGE` error before any provider call.

[Unreleased]: https://github.com/sodejm/AncestryLLM/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/sodejm/AncestryLLM/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/sodejm/AncestryLLM/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/sodejm/AncestryLLM/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/sodejm/AncestryLLM/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/sodejm/AncestryLLM/releases/tag/v0.2.0
