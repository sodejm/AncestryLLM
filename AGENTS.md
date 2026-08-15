# Repository Guidance

## Authority, layout, and current state

- `src/ancestryllm/` is the application package; `tests/` uses fictional data.
- `docs/` contains supporting guidance for privacy, providers, GEDCOM, backups,
  security, CLI use, and the release process.
- `ARCHITECTURE.md` is authoritative for implemented and target architecture; supporting
  documents must not contradict it.
- The implemented product surfaces are the one-shot CLI, the prompt-toolkit/Rich
  REPL, the authenticated FastAPI control adapter, and the bounded Electron 0.6
  desktop control shell. Home, Diagnostics, Settings, and onboarding form the
  supported packaged core; named provider, Tasks, and Chat surfaces are packaged
  source-level gates and must not be represented as supported until their
  target-matched evidence passes. Genealogy/domain routers and other desktop-host
  surfaces remain future adapters. They must consume the same application-service
  contracts instead of redefining behavior.
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

- Follow the [GitHub Flow branch contract](CONTRIBUTING.md#github-flow-branch-strategy):
  branch from current `origin/main` using the appropriate `feature/*`, `bugfix/*`,
  or `hotfix/*` prefix, and never edit `main` or `master` directly.
- Preserve unrelated changes. Do not push unless explicitly requested.
- Keep the local Git environment tidy: before starting new work, reuse a suitable
  existing worktree or create one only when isolation is needed; do not leave
  disposable worktrees behind after work is closed.
- Periodically audit local branches and worktrees against `origin/main`, their
  upstreams, and open PRs. Remove only branches whose work is merged or
  patch-equivalent and which are not checked out by a worktree; preserve all
  unmerged, dirty, active, or ambiguous work and report it for a user decision.
- Before removing any worktree, confirm it is clean and no active task, issue, or
  PR still depends on it. Prefer ordinary `git branch -d` and `git worktree
  remove`; do not force-delete a branch merely because its upstream disappeared.
  Report patch-equivalent but non-ancestry branches for an explicit user-approved
  cleanup plan instead.
- Documentation is mandatory for every net-new feature. Before its PR merges,
  update the relevant user, developer, API, operational, and release documentation
  as applicable, and explicitly record why any normally expected documentation
  surface is unaffected.
- Before a net-new feature PR merges, evaluate its architecture and threat-model
  impact. Update `ARCHITECTURE.md` and the applicable threat-model documentation
  when the feature changes either; otherwise, explicitly record why each is
  unaffected.
- Whenever Codex creates a GitHub issue, determine the appropriate release iteration
  from the Feature Release project's scope, priority, and dependency order, then add
  the issue to that iteration on the release calendar. If project access prevents the
  assignment, report the issue and intended iteration explicitly.
- After submitting a pull request (PR), wait up to 5 minutes for checks to complete
  and for any code review comments to appear. If review comments appear, resolve each
  one by either validating/fixing the issue or adding a clear justification.
- Before completion, check behavior, tests, documentation, dead code, and relevant quality/security gates.
- Link and close an issue only when the change fully satisfies its acceptance criteria.
