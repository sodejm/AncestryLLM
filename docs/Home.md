# AncestryLLM documentation

AncestryLLM is a local-first command-line tool for genealogy research. It
combines deterministic RootsMagic and GEDCOM workflows with optional,
explicitly selected LLM providers.

The canonical source is this `docs/` directory. It is published to the
[AncestryLLM documentation site](https://sodejm.github.io/AncestryLLM/) and the
[GitHub Wiki](https://github.com/sodejm/AncestryLLM/wiki); the Wiki remains
available, but neither published view is an independent documentation source.

## Current product surfaces

The CLI, interactive REPL, and released bounded Electron desktop control shell
are the implemented product surfaces. The shell supports Home, Diagnostics,
Settings, and capability onboarding.

The CLI and REPL use the same command specification, transport-neutral
executor, application DTOs, and genealogy services.

- Start with the [CLI reference](CLI.md) for one-shot commands.
- Start with the [interactive console guide](CONSOLE.md) for the prompt-toolkit
  and Rich REPL.

All user-selected files are governed by the shared
[bounded file-ingress policy](FILE_INGRESS.md), including byte and record
budgets, race detection, output alias rejection, and transactional publication.

The released bounded Electron desktop control shell uses the authenticated
health/capability sidecar. Desktop-domain capabilities—genealogy/domain routes,
files, jobs, providers, cloud accounts, and updater flows—remain planned or
incomplete. The current desktop records document the released control-surface
boundary and the verification needed for later expansion; they are not a
current journey for excluded domain capabilities.

The accepted deployment architecture records future Local Desktop container,
Connect Remote, and advanced Host Remote profiles. None is currently
implemented, shipped, or supported, and the local CLI, REPL, and bounded
desktop shell remain the only product surfaces.

## Tutorials

Learn a complete, safe workflow with fictional data:

- [Merge fictional GEDCOM records offline](tutorials/offline-gedcom-merge.md)
  — produce a rooted GEDCOM 5.5.5 file and quality report with `provider=none`
  and no network calls.

## How-to guides

Task-oriented guidance for common goals:

- [Run an offline GEDCOM merge](how-to/run-an-offline-gedcom-merge.md) — merge
  the public fictional fixtures, verify the results, and recover from failure
- [Explore commands in the interactive console](how-to/explore-the-interactive-console.md)
  — inspect the implemented prompt-toolkit/Rich REPL safely
- [Interactive console guide](CONSOLE.md) — start and use the REPL
- [Encrypted backup and recovery](ENCRYPTED_BACKUPS.md) — create and restore backups
- [First-run storage diagnostics](SETUP_DIAGNOSTICS.md) — troubleshoot setup
- [Release runbook](RELEASING.md) — prepare and publish a release

The established root paths for the last four guides remain published while
release packaging and contract consumers use them. Their inventory records the
later `git mv` cutover that will update those consumers together.

## Reference

Factual, accurate information to look up:

- [CLI reference](CLI.md) — commands, options, and exit codes
- [Provider guide](PROVIDERS.md) — provider policy, profiles, and capabilities
- [GEDCOM compatibility and release checks](GEDCOM_COMPATIBILITY.md)
- [Versioning and compatibility](VERSIONING.md)
- [Bounded file ingress](FILE_INGRESS.md)
- [Continuous integration](CI.md)
- [Architecture ownership and dependency contracts](ARCHITECTURE_CONTRACTS.md)
- [Command executor](COMMAND_EXECUTOR.md)
- [Built-in module authoring](MODULE_AUTHORING.md) — constraints, registration, and tests
- [Application contracts](APPLICATION_CONTRACTS.md) — service DTOs and ports
- [API reference](api/API_REFERENCE.md) — authenticated health and capability control API
- [Local LLM benchmarks](LOCAL_LLM_BENCHMARKS.md)
- [Local-first retrieval evaluation](LOCAL_RETRIEVAL_EVALUATION.md)

## Explanation

Concepts, rationale, and design context:

- [Privacy and consent](PRIVACY_AND_CONSENT.md) — local-first boundaries and consent model
- [REPL architecture](REPL_ARCHITECTURE.md) — internal session and dispatch design
- [Desktop shell (released bounded v0.5.0 control surface)](DESKTOP_SHELL.md) — Home, Diagnostics, Settings, and capability onboarding only

## Supporting records and publishing

- [Wiki synchronization](WIKI_SYNC.md) — reproduce the publishing step locally
- [Wiki operations and recovery](WIKI_OPERATIONS.md) — dispatch, verify, rollback
- [Security response checklist](SECURITY_RESPONSE.md)
- [Verified uv bootstrap](security/verified-uv-bootstrap.md) — executable trust policy, receipts, and reviewed updates
- [Documentation authoring guide](DOCS_AUTHORING.md) — Diátaxis map and authoring rules
- [Release notes (planned v0.6)](release-notes/0.6.0.md) — release preparation, not a current release
- [Desktop verification (released bounded shell and later changes)](DESKTOP_VERIFICATION.md) — exact-head verification, not release approval
- [Desktop deployment (released bounded shell publication)](DEPLOYMENT.md) — installer publication controls, not a hosted application
- [Local-first container and advanced remote deployment ADR](ADR-0026-local-first-container-remote-deployment.md) — accepted future profiles, trust boundaries, and release gates; not current support
- [Electron and FastAPI desktop ADR](ADR-0025-electron-fastapi-desktop.md) — released control-shell boundary and excluded domain scope
- [Provider framework evaluation ADR](ADR-0024-provider-framework-evaluation.md) — recorded provider choice
- [Data-flow threat model and control matrix](THREAT_MODEL.md) — security governance
- [Release evidence index](release-evidence/README.md) — retained verification artifacts

## Documentation links

Documentation links use relative Markdown filenames (for example,
`[Console guide](CONSOLE.md)`). This keeps links valid from this `docs/`
directory in the repository. The Pages build rewrites local links only in its
generated staging directory, from `.md` targets to site paths. Wiki
synchronization rewrites the same local targets to extensionless Wiki page
links. The canonical source remains unchanged.

Use the sidebar to navigate the complete published documentation set.
