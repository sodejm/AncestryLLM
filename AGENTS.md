# Repository Guidelines

## Structure

- `src/ancestryllm/` is the installable application package.
- `tests/` contains pytest tests and fictional fixtures.
- `docs/` contains architecture, privacy, provider, console, GEDCOM, backup,
  security-response, and threat-model guidance.
- `family_trees/` is local-only; never commit its contents.

## Commands

Use `make setup`, `make test`, `make lint`, `make typecheck`, and `make security`.
Run the app with `.venv/bin/ancestry`; no arguments open the console.

## Engineering and data safety

Write typed Python, keep adapters thin, return serializable service DTOs, and use
stable coded errors. Add regression tests for every behavior change. Treat
RootsMagic files as immutable and GEDCOM as loss-minimal. Provider `none` must
remain network-free even when environment keys exist.

Never commit credentials, `.env`, real genealogy records, databases, backups,
reports, logs, or prompt/response payloads. Secrets go through the OS-keyring
service; environment injection is headless/CI fallback only. Do not auto-load
`.env`. Cloud calls require explicit provider selection and consent.
