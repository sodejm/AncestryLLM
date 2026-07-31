# Repository Guidance

## Authority, layout, and current state

- `src/ancestryllm/` is the application package; `tests/` uses fictional data.
- `docs/` contains supporting guidance for privacy, providers, GEDCOM, backups,
  security, CLI use, and the release process.
- `ARCHITECTURE.md` is authoritative for implemented and target architecture; supporting
  documents must not contradict it.
- The implemented terminal surfaces are the one-shot CLI and the prompt-toolkit/Rich
  REPL. FastAPI, Electron, and other desktop-host surfaces remain future adapters; they
  must consume the same application-service contracts instead of redefining behavior.
- Use the existing `CommandSpec` and `ModuleDescriptor` contracts; do not add another
  UI-specific command registry. CLI and REPL dispatch through the implemented,
  transport-neutral `CommandInvocation` and `CommandExecutor` boundary.
- Keep application ports, DTOs, artifact references, and error mapping transport-neutral
  and serializable so future adapters can reuse the same service surface.

## Commands and validation

- Use `make setup`, `make test`, `make lint`, `make typecheck`, and `make security`.
- Run the console with `.venv/bin/ancestry`.
- Targeted checks are useful during development, but completion requires the relevant
  canonical gates above. A security scan stopped with exit code 130 is incomplete.

## Engineering and data safety

- Write typed Python, keep adapters thin, return serializable service DTOs, and use stable coded errors.
- Keep core and service code independent of terminal, web, and desktop frameworks.
- Follow the
  [red-green-refactor process](CONTRIBUTING.md#test-driven-development) for
  behavioral changes: observe the focused test fail for the expected reason
  before changing production code. Document why no failing behavior test
  applies to a genuinely non-behavioral change.
- Maintain cross-platform behavior where practical.
- Treat RootsMagic files as immutable, keep Compose `family_trees` mounts read-only, and
  keep GEDCOM handling loss-minimal.
- Provider `none` must remain network-free even when environment keys exist.
- Never commit credentials, `.env`, real genealogy records, databases, backups, reports, logs, or prompt/response payloads.
- Use the OS keyring for secrets; environment injection is for headless/CI use. Do not auto-load `.env`.
- Cloud calls require explicit provider selection and user consent.

## Workflow and completion

- Work on a dedicated branch or worktree; never edit `main` or `master` directly.
- Preserve unrelated changes. Do not push unless explicitly requested.
- Before completion, check behavior, tests, documentation, dead code, and relevant quality/security gates.
- Link and close an issue only when the change fully satisfies its acceptance criteria.
