# Repository Guidance

## Layout and commands

- `src/ancestryllm/` is the application package; `tests/` uses fictional data.
- `docs/` contains architecture, privacy, provider, GEDCOM, backup, and security guidance.
- Use `make setup`, `make test`, `make lint`, `make typecheck`, and `make security`.
- Run the console with `.venv/bin/ancestry`.

## Engineering and data safety

- Write typed Python, keep adapters thin, return serializable service DTOs, and use stable coded errors.
- Add regression tests for behavior changes. Treat RootsMagic files as immutable and GEDCOM handling as loss-minimal.
- Provider `none` must remain network-free even when environment keys exist.
- Never commit credentials, `.env`, real genealogy records, databases, backups, reports, logs, or prompt/response payloads.
- Use the OS keyring for secrets; environment injection is for headless/CI use. Do not auto-load `.env`.
- Cloud calls require explicit provider selection and user consent.

## Workflow and completion

- Work on a dedicated branch or worktree; never edit `main` or `master` directly.
- Preserve unrelated changes. Do not push unless explicitly requested.
- Before completion, check behavior, tests, documentation, dead code, and relevant quality/security gates.
- Link and close an issue only when the change fully satisfies its acceptance criteria.
