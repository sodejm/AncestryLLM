# AncestryLLM documentation

AncestryLLM is a local-first command-line tool for genealogy research. It
combines deterministic RootsMagic and GEDCOM workflows with optional,
explicitly selected LLM providers.

The canonical source is this `docs/` directory. It is published to the
[AncestryLLM documentation site](https://sodejm.github.io/AncestryLLM/) and the
[GitHub Wiki](https://github.com/sodejm/AncestryLLM/wiki); the Wiki remains
available, but neither published view is an independent documentation source.

## Implemented surfaces

The one-shot CLI and the prompt-toolkit/Rich REPL are the implemented and
supported product surfaces. Start with the [CLI reference](CLI.md) for
one-shot commands or the [interactive console guide](CONSOLE.md) for the REPL.
Both use the same command specification, transport-neutral executor, application
DTOs, and genealogy services.

The 0.5.0 scope adds a bounded, offline Electron control shell. It covers
Home, Diagnostics, a sanitized capability summary, and local visual Settings
only; it has no genealogy, files, jobs, chat, providers, cloud accounts, or
updater surface. See the [desktop shell explanation](DESKTOP_SHELL.md) for
scope and the [desktop verification explanation](DESKTOP_VERIFICATION.md) for
the release evidence contract.

All user-selected files are governed by the shared
[bounded file-ingress policy](FILE_INGRESS.md).

## How-to guides

Step-by-step instructions for specific tasks:

- [Interactive console guide](CONSOLE.md) — start and use the REPL
- [Encrypted backup and recovery](ENCRYPTED_BACKUPS.md) — back up and restore a database
- [First-run storage diagnostics](SETUP_DIAGNOSTICS.md) — run a local health check
- [Privacy and consent](PRIVACY_AND_CONSENT.md) — configure privacy controls and consent profiles
- [Provider guide](PROVIDERS.md) — configure LLM provider adapters
- [Release runbook](RELEASING.md) — release a new version (maintainers)
- [Wiki synchronization](WIKI_SYNC.md) — reproduce the publishing step locally (maintainers)
- [Wiki operations and recovery](WIKI_OPERATIONS.md) — dispatch, troubleshoot, and recover the wiki (maintainers)
- [Deployment operations](DEPLOYMENT.md) — manage hosted build controls (maintainers)
- [Security response checklist](SECURITY_RESPONSE.md) — handle vulnerability reports (maintainers)

## Reference

Factual contracts, commands, schemas, settings, and compatibility tables:

- [CLI reference](CLI.md) — all implemented `ancestry` commands and flags
- [Command executor contract](COMMAND_EXECUTOR.md) — `CommandInvocation` and `CommandExecutor` boundary
- [Application-service contracts](APPLICATION_CONTRACTS.md) — framework-independent service boundary
- [Architecture ownership and dependency contracts](ARCHITECTURE_CONTRACTS.md) — enforced boundaries
- [Bounded file ingress](FILE_INGRESS.md) — input validation policy and budgets
- [Versioning and compatibility](VERSIONING.md) — Semantic Versioning contract
- [GEDCOM compatibility and release checks](GEDCOM_COMPATIBILITY.md) — supported GEDCOM versions
- [Built-in module authoring](MODULE_AUTHORING.md) — `ModuleDescriptor` and `CommandSpec` contracts
- [API reference](api/API_REFERENCE.md) — authenticated FastAPI health and capability foundation (0.5.0)

## Explanation

Design context, rationale, and architecture boundaries:

- [REPL architecture and compatibility boundary](REPL_ARCHITECTURE.md)
- [Data-flow threat model and control matrix](THREAT_MODEL.md)
- [Desktop shell](DESKTOP_SHELL.md) — bounded 0.5.0 Electron scope and design
- [Desktop sidecar](DESKTOP_SIDECAR.md) — packaged control-only sidecar architecture
- [Desktop verification](DESKTOP_VERIFICATION.md) — release evidence gate
- [Continuous integration](CI.md) — CI gate structure and ordering
- [Local LLM benchmarks](LOCAL_LLM_BENCHMARKS.md) — benchmark methodology
- [Local-first retrieval evaluation](LOCAL_RETRIEVAL_EVALUATION.md) — future RAG design boundary (planned)

## Documentation links

Documentation links use relative Markdown filenames (for example,
`[Console guide](CONSOLE.md)`). This keeps links valid from this `docs/`
directory in the repository. The Pages build rewrites local links only in its
generated staging directory, from `.md` targets to site paths. Wiki
synchronization rewrites the same local targets to extensionless Wiki page
links. The canonical source remains unchanged.

Use the sidebar to navigate the complete published documentation set. See
[documentation authoring rules](DOC_AUTHORING.md) for the information
architecture, prose standards, and validation rules.
