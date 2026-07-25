# AncestryLLM documentation

AncestryLLM is a local-first command-line tool for genealogy research. It
combines deterministic RootsMagic and GEDCOM workflows with optional,
explicitly selected LLM providers.

Start with the [CLI reference](CLI.md) for one-shot commands or the
[interactive console guide](CONSOLE.md). The pages below cover versioning,
privacy controls, providers, GEDCOM interoperability, backups, release
operations, and security practices.

## Documentation links

Documentation links use relative Markdown filenames (for example,
`[Console guide](CONSOLE.md)`). This keeps links valid from this `docs/`
directory in the repository. During synchronization, local `.md` targets are
published as extensionless GitHub Wiki page links so navigation remains in the
Wiki UI.

Use the sidebar to navigate the complete published documentation set.

The accepted local desktop direction, process boundaries, MVP scope, and secure
development gates are defined in the
[Electron and FastAPI desktop ADR](ADR-0025-electron-fastapi-desktop.md). Its
OWASP Top 10:2025 and NIST SP 800-218 control evidence is maintained in the
[threat model](THREAT_MODEL.md).

Maintainers can reproduce the deterministic publishing step locally with the
[wiki synchronization guide](WIKI_SYNC.md). The
[Wiki operations and recovery runbook](WIKI_OPERATIONS.md) covers dispatch,
verification, troubleshooting, rollback, and reinitialization.
