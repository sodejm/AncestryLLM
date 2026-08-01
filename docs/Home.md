# AncestryLLM documentation

AncestryLLM is a local-first command-line tool for genealogy research. It
combines deterministic RootsMagic and GEDCOM workflows with optional,
explicitly selected LLM providers.

Start with the [CLI reference](CLI.md) for one-shot commands or the
[interactive console guide](CONSOLE.md). In 0.4.0 these are the only
implemented product surfaces: both use the same command specification,
transport-neutral executor, application DTOs, and genealogy services. The
unshipped 0.5.0 work includes a control-only FastAPI sidecar and Electron
supervisor; later domain adapters must reuse that service surface rather than
define another command or domain layer. The pages below
cover versioning, privacy controls, providers, GEDCOM interoperability,
backups, release operations, and security practices.

All user-selected files are governed by the shared
[bounded file-ingress policy](FILE_INGRESS.md), including byte and record
budgets, race detection, output alias rejection, and transactional publication.

## Documentation links

Documentation links use relative Markdown filenames (for example,
`[Console guide](CONSOLE.md)`). This keeps links valid from this `docs/`
directory in the repository. During synchronization, local `.md` targets are
published as extensionless GitHub Wiki page links so navigation remains in the
Wiki UI.

Use the sidebar to navigate the complete published documentation set.

The accepted later-roadmap local desktop direction, process boundaries, MVP
scope, and secure development gates are defined in the
[Electron and FastAPI desktop ADR](ADR-0025-electron-fastapi-desktop.md). Its
OWASP Top 10:2025 and NIST SP 800-218 control evidence is maintained in the
[threat model](THREAT_MODEL.md), and the implemented control lifecycle is in
[Packaged desktop sidecar](DESKTOP_SIDECAR.md).

Maintainers can reproduce the deterministic publishing step locally with the
[wiki synchronization guide](WIKI_SYNC.md). The
[Wiki operations and recovery runbook](WIKI_OPERATIONS.md) covers dispatch,
verification, troubleshooting, rollback, and reinitialization.
