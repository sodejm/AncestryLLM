# Copilot Instructions

## Shared authority and project state

- Follow [AGENTS.md](../AGENTS.md) for repository-wide requirements and
  [ARCHITECTURE.md](../ARCHITECTURE.md) for implemented and target architecture.
- This repository is local-first and privacy-sensitive. Prefer safe defaults when
  requirements are uncertain.
- The implemented terminal surfaces are the one-shot CLI and prompt-toolkit/Rich REPL.
  FastAPI, Electron, and other desktop-host surfaces remain future adapters; they must
  consume the same application-service contracts instead of redefining behavior.

## Engineering and validation

- Write typed Python, keep adapters thin, return serializable service DTOs, and use
  stable coded errors.
- Keep core and service code independent of terminal, web, and desktop frameworks.
- Use the existing `CommandSpec` and `ModuleDescriptor` contracts instead of adding a
  UI-specific command registry.
- Route CLI and REPL commands through the implemented, transport-neutral
  `CommandInvocation` and `CommandExecutor` boundary.
- Keep application ports, DTOs, artifact references, and error mapping transport-neutral
  and serializable so future adapters can reuse the same service surface.
- Follow the
  [red-green-refactor process](../CONTRIBUTING.md#test-driven-development) for
  behavioral changes: add the smallest deterministic test in [tests](../tests),
  use fictional data, and observe the expected failure before changing production code.
  Document why no failing behavior test applies to a genuinely non-behavioral change.
- Maintain cross-platform behavior where practical.
- Use `make setup`, `make test`, `make lint`, `make typecheck`, and `make security` as
  the canonical commands. Targeted checks may be used during development, but completion
  requires the relevant canonical gates; exit code 130 means a security scan is incomplete.

## Data and security

- Treat genealogy files as private personal data and GEDCOM handling as loss-minimal.
- Never introduce write paths to RootsMagic `.rmtree` files.
- Keep `family_trees` mounted read-only in Compose.
- Provider `none` must remain network-free even when environment keys exist.
- Cloud calls require explicit provider selection and user consent.
- Never hardcode credentials, tokens, or API keys, and never commit `.env`, real
  genealogy data, databases, backups, reports, logs, or prompt/response payloads.
- Use the OS keyring for secrets; environment injection is for headless/CI use. Do not
  auto-load `.env`.
- Preserve existing security hooks and CI checks.
- If configuration changes affect security posture, update [SECURITY.md](../SECURITY.md) and [README.md](../README.md).

## Workflow and PR quality

- Work on a dedicated branch or worktree; never edit `main` or `master` directly.
- Preserve unrelated changes and do not push unless explicitly requested.
- Keep changes focused. Before completion, review behavior, tests, documentation, dead
  code, and relevant quality/security gates.
- Link or close an issue only when its acceptance criteria are fully satisfied.
- A PR should explain what changed and why, identify risks and security impact, and
  include evidence from the relevant canonical validation commands.

Copilot-specific suggestions must not weaken the shared requirements in
[AGENTS.md](../AGENTS.md).
