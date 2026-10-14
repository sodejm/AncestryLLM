# AncestryLLM documentation

AncestryLLM is a local-first genealogy tool with one-shot commands, an
interactive console, and a bounded desktop control shell. Choose a task below.
The desktop supports local setup and diagnostics; use the CLI or console for
genealogy workflows.

## Tutorials

Follow a complete workflow:

- [Desktop first run](tutorials/desktop-first-run.md) — reach a network-free
  Home state in the supported 0.6.0 installer and choose the next supported surface
- [Merge fictional GEDCOM records offline](tutorials/offline-gedcom-merge.md)
  — produce a rooted GEDCOM 5.5.5 file and quality report with `provider=none`
  and no network calls.

## How-to guides

Complete a specific task or recover from a problem:

- [Run an offline GEDCOM merge](how-to/run-an-offline-gedcom-merge.md) — merge
  the public fictional fixtures, verify the results, and recover from failure
- [Explore commands in the interactive console](how-to/explore-the-interactive-console.md)
  — inspect the implemented prompt-toolkit/Rich REPL safely
- [Recover with desktop diagnostics](how-to/desktop-diagnostics.md) — interpret
  sanitized startup state and retry the private desktop service
- [Interactive console guide](CONSOLE.md) — start and use the REPL
- [Encrypted backup and recovery](ENCRYPTED_BACKUPS.md) — create and restore backups
- [First-run storage diagnostics](SETUP_DIAGNOSTICS.md) — troubleshoot setup
- [Release runbook](RELEASING.md) — prepare and publish a release

### Desktop source-level guides

These guides cover gated adapters in source. They are outside the supported
0.6.0 installer claim and require target-matched packaged and security evidence
before support can expand. A fictional development preview is not that evidence.

- [Grant desktop file access](how-to/desktop-file-access.md) — understand
  scoped opaque grants and the immutable-input boundary
- [Configure a desktop provider and consent](how-to/desktop-provider-consent.md)
  — test an endpoint, review exact disclosure scope, and revoke consent
- [Monitor and cancel desktop tasks](how-to/desktop-tasks.md) — follow
  backend-owned progress and cancellation safe points
- [Use transient desktop chat](how-to/desktop-chat.md) — work with the bounded,
  unsaved advisory conversation surface

## v0.6 desktop learning path

Start with the local, provider-none Home state, then use
[Desktop shell](explanation/DESKTOP_SHELL.md) to understand the bounded
control surface and its sanitized recovery path. Continue with the
[interactive console guide](CONSOLE.md) for genealogy commands, and use the
[CLI reference](reference/CLI.md) when a one-shot command is more appropriate.
These surfaces share application contracts, but the desktop shell does not
silently grant provider, network, filesystem, or genealogy authority.

![Home shows the local desktop shell, Ready status, and local action cards.](assets/screenshots/electron/ready-home.png)

## Current product surfaces

The CLI, interactive REPL, and released bounded Electron desktop control shell
are the implemented product surfaces. The shell supports Home, Diagnostics,
Settings, and capability onboarding. Desktop-domain capabilities remain
planned or gated; see the [desktop shell guide](explanation/DESKTOP_SHELL.md)
for the supported boundary and source-level contracts.

The CLI and REPL use the same command specification, transport-neutral
executor, application DTOs, and genealogy services. User-selected files follow
the shared [bounded file-ingress policy](reference/FILE_INGRESS.md), including
byte and record budgets, race detection, output alias rejection, and
transactional publication.

The [deployment guide](DEPLOYMENT.md) covers the reviewed macOS arm64
runtime-tool controls. They do not activate an AncestryLLM application
container or a remote runtime; Local Desktop remains the only available
deployment choice.

## Reference

Factual, accurate information to look up:

- [Desktop reference](reference/DESKTOP.md) — routes, states, stable codes,
  platform behavior, accessibility, and recovery
- [Desktop diagnostics contract](reference/DESKTOP_DIAGNOSTICS.md) — local
  schema, event catalog, retention, privacy, and support boundaries
- [CLI reference](reference/CLI.md) — commands, options, and exit codes
- [Provider guide](reference/PROVIDERS.md) — provider policy, profiles, and capabilities
- [GEDCOM compatibility and release checks](reference/GEDCOM_COMPATIBILITY.md)
- [Versioning and compatibility](reference/VERSIONING.md)
- [Bounded file ingress](reference/FILE_INGRESS.md)
- [Continuous integration](reference/CI.md)
- [ty advisory evaluation](reference/TY_ADVISORY_EVALUATION.md) — 0.6 checker evidence and cutover disposition
- [Ruff rule-expansion evaluation](reference/RUFF_EXPANSION_EVALUATION.md) — reviewed 0.6 static-analysis batches and regression evidence
- [uv_build evaluation](reference/UV_BUILD_EVALUATION.md) — reproducible backend comparison and fail-closed adoption disposition
- [Architecture ownership and dependency contracts](reference/ARCHITECTURE_CONTRACTS.md)
- [Command executor](reference/COMMAND_EXECUTOR.md)
- [Built-in module authoring](reference/MODULE_AUTHORING.md) — constraints, registration, and tests
- [Application contracts](reference/APPLICATION_CONTRACTS.md) — service DTOs and ports
- [API reference](reference/api/API_REFERENCE.md) — authenticated health and capability control API
- [Local LLM benchmarks](reference/LOCAL_LLM_BENCHMARKS.md)
- [Local-first retrieval evaluation](reference/LOCAL_RETRIEVAL_EVALUATION.md)

## Explanation

Concepts, rationale, and design context:

- [Privacy and consent](explanation/PRIVACY_AND_CONSENT.md) — local-first boundaries and consent model
- [REPL architecture](explanation/REPL_ARCHITECTURE.md) — internal session and dispatch design
- [Desktop shell (released bounded v0.6.0 plus marked source-level gates)](explanation/DESKTOP_SHELL.md) — supported Home, Diagnostics, Settings, and onboarding plus source-level gated Tasks presentation and transient-chat contracts

## Supporting records and publishing

- [Published documentation](https://sodejm.github.io/AncestryLLM/) — browse the canonical guides on GitHub Pages
- [Wiki synchronization](WIKI_SYNC.md) — reproduce the publishing step locally
- [Wiki operations and recovery](WIKI_OPERATIONS.md) — dispatch, verify, rollback
- [Security response checklist](SECURITY_RESPONSE.md)
- [Verified uv bootstrap](security/verified-uv-bootstrap.md) — executable trust policy, receipts, and reviewed updates
- [Documentation authoring guide](DOCS_AUTHORING.md) — Diátaxis map and authoring rules
- [Release notes for v0.6.0](release-notes/0.6.0.md) — release scope, limitations, installation, and verification
- [Desktop verification (released bounded shell and later changes)](DESKTOP_VERIFICATION.md) — exact-head verification, not release approval
- [Desktop deployment (released bounded shell publication)](DEPLOYMENT.md) — installer publication and app-owned macOS arm64 runtime-tool controls, not a hosted application
- [CORE-42 extraction benchmark and dependency report](release-evidence/issue-170-core-extraction-benchmark-and-dependency-report.md) — measured input for standalone package extraction decisions
- [GEDCOM and RootsMagic extraction decision ADR](ADR-0027-core-package-extraction-decision.md) — separate keep-internal decisions and revisit triggers
- [Local-first container and advanced remote deployment ADR](ADR-0026-local-first-container-remote-deployment.md) — implemented profile and macOS arm64 runtime-tool boundaries plus future application-runtime gates
- [Electron and FastAPI desktop ADR](ADR-0025-electron-fastapi-desktop.md) — released control-shell boundary and excluded domain scope
- [Provider framework evaluation ADR](ADR-0024-provider-framework-evaluation.md) — recorded provider choice
- [Data-flow threat model and control matrix](THREAT_MODEL.md) — security governance
- [Release evidence index](release-evidence/README.md) — retained verification artifacts
