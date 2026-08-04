# AncestryLLM documentation

AncestryLLM is a local-first command-line tool for genealogy research. It
combines deterministic RootsMagic and GEDCOM workflows with optional,
explicitly selected LLM providers.

The canonical source is this `docs/` directory. It is published to the
[AncestryLLM documentation site](https://sodejm.github.io/AncestryLLM/) and the
[GitHub Wiki](https://github.com/sodejm/AncestryLLM/wiki); the Wiki remains
available, but neither published view is an independent documentation source.

## Implemented surfaces

The CLI and interactive REPL are the two implemented product surfaces.
Both use the same command specification, transport-neutral executor, application
DTOs, and genealogy services.

- Start with the [CLI reference](CLI.md) for one-shot commands.
- Start with the [interactive console guide](CONSOLE.md) for the prompt-toolkit
  and Rich REPL.

All user-selected files are governed by the shared
[bounded file-ingress policy](FILE_INGRESS.md), including byte and record
budgets, race detection, output alias rejection, and transactional publication.

**Planned (v0.5.0):** The offline Electron desktop shell on macOS, Windows, and
Ubuntu introduces Home, Diagnostics, Settings, and capability onboarding. It
uses a private control-only sidecar and excludes genealogy, files, jobs, chat,
providers, cloud accounts, and updater surfaces. See the
[desktop shell guide](DESKTOP_SHELL.md) and
[desktop verification guide](DESKTOP_VERIFICATION.md).

## How-to guides

Task-oriented guidance for common goals:

- [Interactive console guide](CONSOLE.md) — start and use the REPL
- [Provider guide](PROVIDERS.md) — configure provider none or a cloud provider
- [Encrypted backup and recovery](ENCRYPTED_BACKUPS.md) — create and restore backups
- [First-run storage diagnostics](SETUP_DIAGNOSTICS.md) — troubleshoot setup
- [Release runbook](RELEASING.md) — prepare and publish a release

## Reference

Factual, accurate information to look up:

- [CLI reference](CLI.md) — commands, options, and exit codes
- [GEDCOM compatibility and release checks](GEDCOM_COMPATIBILITY.md)
- [Versioning and compatibility](VERSIONING.md)
- [Bounded file ingress](FILE_INGRESS.md)
- [Built-in module authoring](MODULE_AUTHORING.md)
- [Continuous integration](CI.md)
- [Architecture ownership and dependency contracts](ARCHITECTURE_CONTRACTS.md)
- [Command executor](COMMAND_EXECUTOR.md)
- [Local LLM benchmarks](LOCAL_LLM_BENCHMARKS.md)
- [Local-first retrieval evaluation](LOCAL_RETRIEVAL_EVALUATION.md)

## Explanation

Concepts, rationale, and design context:

- [Privacy and consent](PRIVACY_AND_CONSENT.md) — local-first boundaries and consent model
- [REPL architecture](REPL_ARCHITECTURE.md) — internal session and dispatch design
- [Application contracts](APPLICATION_CONTRACTS.md) — service DTOs and ports
- [Data-flow threat model and control matrix](THREAT_MODEL.md)
- [Electron and FastAPI desktop ADR](ADR-0025-electron-fastapi-desktop.md)
- [Provider framework evaluation ADR](ADR-0024-provider-framework-evaluation.md)

## Maintainer and publishing guides

- [Wiki synchronization](WIKI_SYNC.md) — reproduce the publishing step locally
- [Wiki operations and recovery](WIKI_OPERATIONS.md) — dispatch, verify, rollback
- [Security response checklist](SECURITY_RESPONSE.md)
- [Documentation authoring guide](DOCS_AUTHORING.md) — Diátaxis map and authoring rules

## Documentation links

Documentation links use relative Markdown filenames (for example,
`[Console guide](CONSOLE.md)`). This keeps links valid from this `docs/`
directory in the repository. The Pages build rewrites local links only in its
generated staging directory, from `.md` targets to site paths. Wiki
synchronization rewrites the same local targets to extensionless Wiki page
links. The canonical source remains unchanged.

Use the sidebar to navigate the complete published documentation set.
