# Contributing

Use a system-supplied Python 3.12-3.14, create a focused branch under the branch
contract below, and run `make setup`. The checked-in `.python-version` selects
3.12 by default, and repository policy prevents `uv` from downloading another
interpreter. The setup target verifies the pinned `uv` release before its first
execution and installs it under the ignored repository-local `.tools/`
directory; do not substitute an unverified `uv` from `PATH`. See the
[verified uv bootstrap guide](docs/security/verified-uv-bootstrap.md) for the
required local GitHub authentication, trust policy, failure recovery, and
reviewed update procedure. `make setup` executes
`uv sync --locked --all-extras --all-groups`; purpose-specific CI jobs may
synchronize a narrower declared profile before invoking the same canonical Make
target. See [dependency maintenance](docs/DEPENDENCY_MAINTENANCE.md) for the
group-to-command contract and lockfile review procedure. Define commands
through the shared `CommandSpec`, route both terminal adapters through
`CommandInvocation` and `CommandExecutor`, and put domain logic in services,
not presentation or dispatch adapters. Core and application contracts must
remain independent of Click, prompt-toolkit, Rich, FastAPI/Pydantic, Electron,
provider SDKs, and host-filesystem objects. New providers implement the common
contract and mocked timeout/malformed-output/consent/offline tests. New modules
must be explicit built-ins with one-shot and console parity; follow the
[module-authoring contract](docs/MODULE_AUTHORING.md) rather than adding a
second command registry.

## GitHub Flow branch strategy

AncestryLLM follows the
[GitHub Flow branch categories described by AWS Prescriptive Guidance](https://docs.aws.amazon.com/prescriptive-guidance/latest/choosing-git-branch-approach/branches-in-a-git-hub-flow-strategy.html).
`main` is the protected, deployable trunk. Do not commit or push directly to
`main`; create a short-lived branch from current `origin/main`, keep it focused
on one work item, and merge it back through a reviewed, green pull request.
This repository does not use long-lived `develop` or `release/*` branches.

Every human-created work branch must use the prefix that matches its purpose:

- `feature/*` — planned capabilities, enhancements, documentation, refactors,
  dependency updates, CI changes, and other non-defect work;
- `bugfix/*` — ordinary defects that do not require an emergency production
  response; or
- `hotfix/*` — high-impact critical defects that must reach production with
  minimal delay.

After the prefix, use a lowercase kebab-case name in the form
`<work-item>-<short-description>`, where `<work-item>` is the issue or story
number when one exists. Examples are `feature/231-signed-installer`,
`bugfix/230-renderer-timeout`, and `hotfix/312-keyring-crash`. Do not create
human work branches with unclassified or alternative prefixes such as
`codex/*`, `chore/*`, `docs/*`, `fix/*`, `issue/*`, or `release/*`. GitHub-owned
Dependabot branches are the sole naming exception because GitHub controls their
`dependabot/*` refs; they still target `main` through the same protected
pull-request gates.

Before opening the pull request, incorporate the latest `origin/main` without
rewriting shared history and rerun the relevant gates. Delete the merged remote
branch and remove its clean local worktree when its commits are safely
reachable; preserve branches with unmerged or graph-unique work for explicit
follow-up.

## Test-driven development

Every behavioral change starts from a testable acceptance criterion and follows
the red-green-refactor loop:

1. **Red:** Add the smallest deterministic, offline test that expresses one
   required behavior. Run it before changing production code and confirm that
   it fails for the expected reason.
2. **Green:** Make the minimum production change needed to pass the new test
   without weakening existing assertions or safety controls.
3. **Refactor:** Improve the implementation and test clarity while keeping the
   focused test green.
4. **Verify:** Run the surrounding test slice, then the relevant canonical
   repository gates.

Bug fixes begin with a regression test that reproduces the defect. Changes to
public commands, service contracts, serialized DTOs, coded errors, providers,
storage, or genealogy behavior include contract or integration coverage at the
appropriate boundary. Tests use fictional data and keep `provider=none`
network-free.

Documentation-only, comment-only, metadata-only, and other genuinely
non-behavioral changes may not have a meaningful failing behavior test. Explain
that exception in the issue and pull request, and name the focused validation
used instead. Do not use an exception for configuration, workflow, or
dependency changes that alter observable behavior. Pull requests must be green;
record the initial expected failure as evidence rather than committing a
deliberately failing test.

Before a pull request run
`make test lint typecheck security sbom package workflow-audit`. Describe
scope, privacy impact, threat-model changes, migration impact, and exact test
evidence. Do not commit real GEDCOM, RootsMagic, database, backup, report, log,
prompt/response, secrets, or person details; use clearly fictional fixtures.

Changes to the OCI or Compose topology also run `make container-policy` and
the focused container contract tests. Native lifecycle evidence must be
produced on a matching Linux architecture: CI builds and runs both gateway and
worker images independently on hosted Linux amd64 and arm64 runners without
QEMU. An emulated build is useful for development but is not native-platform
release evidence. The checked-in topology is validation-only until its
separate authentication and encrypted-persistence dependencies ship; do not
add application routes, provider access, writable genealogy data, published
ports, host paths, secrets, or schema migrations while that boundary remains.

Releases follow [the release runbook](docs/RELEASING.md). Never publish from a
workstation, use a long-lived package-index token, move a published tag, or
bypass required checks.

Repository tooling belongs in a purpose-specific PEP 735 dependency group, not
in an application extra. Provider SDKs remain optional extras and must not be
added to quality, security, or package environments without a demonstrated
runtime import. Canonical Make targets execute their tools through exact
`uv run --locked` commands. CI may narrow synchronization first, but it calls
the same Make target without changing the command or its flags. The verified
bootstrap supplies `uv`; do not add `uv` to a dependency group, install it with
`pip`, use `uvx` or `uv run --with`, or enable Python downloads.

During the 0.6 advisory period, `make typecheck` remains the authoritative
strict-mypy gate with the Pydantic plugin. `make typecheck-ty` runs exact
`ty 0.0.69` across the same complete source tree and preserves its real status;
CI exposes that result in a separate nonblocking step. See the
[ty advisory evaluation](docs/TY_ADVISORY_EVALUATION.md) before interpreting
its diagnostics or proposing the separately gated 0.7 cutover.

The checked-in VS Code profile recommends `charliermarsh.ruff` for Python
linting and formatting and `ms-python.mypy-type-checker` for the authoritative
type check. It reads project configuration from `pyproject.toml`, disables
unsafe automatic fixes, and does not automatically load `.env`. Other editors
must preserve the same Ruff, strict-mypy, and secret-loading boundaries.

GEDCOM changes must preserve citations, custom/vendor structures, pointers,
families, conflicts, and conservative removal invariants. RootsMagic fixtures
must be synthetic and source files must remain hash-identical after tests.

## Secure desktop development

The current 0.5.0 foundation includes authenticated FastAPI health and
capability routes plus a bounded Electron shell, renderer, preload bridge, and
unpublished package-verification path. Unreleased Issue #103 adds opaque native
file-grant mediation, but it does not yet add domain API routes, parser workers,
or end-user file workflows. Later adapters must consume the existing
application-service surface without redefining command or genealogy behavior.

Desktop work is governed by
[`docs/ADR-0025-electron-fastapi-desktop.md`](docs/ADR-0025-electron-fastapi-desktop.md)
and [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md). Treat the renderer,
loopback clients, imported files, model output, plugins, packages, and updates
as untrusted. Preserve the network-free `provider=none` contract.

Before implementation, map the change to its threat/control and abuse-case IDs,
OWASP Top 10:2025 category, applicable versioned OWASP ASVS 5.0.0 requirements,
and NIST SP 800-218 (`PO`, `PS`, `PW`, or `RV`) outcomes. Add and observe
positive, boundary, and negative regression tests failing before implementing
the behavior. A scanner result alone does not close a control.

Use the dedicated `feature/*`, `bugfix/*`, or `hotfix/*` issue branch/worktree
and remain within its exclusive path ownership. Do not edit shared lockfiles,
generated contracts, workflows, architecture/security documents, or another
issue's subtree opportunistically. Wait for every hard dependency to merge
before branching from current `origin/main`.

Renderer code has no Node types/imports, direct filesystem/network/keyring/
provider/database access, generic IPC, raw HTML, remote assets, or secrets in
Vite environment values. Privileged IPC, sidecar routes, file grants, secrets,
events, plugins, and update paths must use strict versioned DTOs, size limits,
deny-by-default behavior, and the negative tests named by the threat ledger.

Renderer file selection must use the opaque grant broker, never user-supplied
or returned paths. Grant DTOs remain path-free; Electron main owns native
dialogs, the path map, lifecycle revocation, and trusted internal resolution. A
future Python adapter must reopen and revalidate an internally resolved path
through the shared bounded file-ingress policy. No renderer may redeem a grant
or call a direct filesystem API.

Desktop pull requests run all applicable Python and desktop format, lint,
strict type, unit, contract, integration, and packaged tests plus Semgrep,
CodeQL, secret scanning, dependency audit, lockfile review, and SBOM
generation. Record findings as fixed, evidence-backed false positives, or
permitted time-bounded residual risks. Never waive a finding silently; expired
exceptions and untriaged Critical/High findings fail the gate.

## Documentation, Pages, and Wiki publishing

The Markdown files under `docs/` are the authoritative source for documentation
published to the [AncestryLLM documentation site](https://sodejm.github.io/AncestryLLM/)
and GitHub Wiki. Make documentation changes in `docs/` on the appropriate
`feature/*`, `bugfix/*`, or `hotfix/*` branch and submit them through the normal
pull-request workflow. Pages and the Wiki are
generated publishing targets, not separate documentation sources.

All version-controlled Markdown files under `docs/` are in publishing scope,
including the home and navigation sources. The Pages build creates an isolated
staging copy and Wiki synchronization generates the managed Wiki pages; neither
output must be copied back into the repository or included in a pull request.
Removing a source page from `docs/` means its managed Wiki page will also be
removed by synchronization.

Do not edit a managed GitHub Wiki page directly. A direct edit is allowed only
when a documented recovery procedure explicitly requires it; reproduce any
lasting correction in `docs/` immediately so the next synchronization does not
discard it.
