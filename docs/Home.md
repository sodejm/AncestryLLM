# AncestryLLM documentation

AncestryLLM is a local-first platform for genealogy research tools. It combines
deterministic RootsMagic and GEDCOM workflows with optional, explicitly selected
LLM providers.

Start with the [Console and CLI guide](CONSOLE.md) for interactive and one-shot
command usage. The pages below cover the application's architecture, privacy
controls, providers, GEDCOM interoperability, backups, and security practices.

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
