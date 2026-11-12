# Local push gate

Install the repository environment and both Git hook stages with
`make bootstrap`. If the environment is already installed, run `make hooks`.
Hook installation is local to the Git checkout, so each contributor must install
it; cloning the configuration alone does not activate hooks.

The hook needs Git, Make, a supported system Python, and
[Trivy](https://trivy.dev/latest/getting-started/installation/) on `PATH`, alongside
the tools required by `make bootstrap` (including gitleaks). On macOS, install
Trivy with `brew install trivy`. Changed desktop files also require the
repository's exact Node.js and pnpm versions from `desktop/package.json`.
Missing tools, scanner/database download errors, findings, and failed commands
block the push. Security tools need network access for reviewed Semgrep rules,
dependency advisories, and Trivy's vulnerability database and checks.

## What runs

The installed `pre-push` stage invokes `make pre-push`, which:

1. Requires committed tracked changes and verifies that the selected push commit
   matches `HEAD`. Checks run in the checkout and verify its tracked state again
   on completion. Untracked local files are preserved.
2. Compares the branch with its merge base at `origin/main` and requires added or
   modified tests alongside Python, repository tooling, workflow, or container
   changes (`tests/test_*.py`), and alongside desktop implementation changes
   (`*.test.ts`, `*.test.tsx`, `*.test.mjs`, or `*.spec.ts`). Deleted tests do not
   satisfy the check. Tests from earlier commits on the branch count on a later
   incremental push. Documentation-only and lockfile-only changes do not require
   new test cases, but still run the gates.
3. Uses `git archive` to scan a temporary copy of the committed tree with Trivy's
   vulnerability, misconfiguration, and secret scanners. HIGH and CRITICAL
   findings exit nonzero. The scan excludes untracked/ignored local files such as
   genealogy records, `.env`, dependencies, caches, and build output. Local Trivy
   config and ignore files cannot silently suppress findings.
4. Runs `make lock-check`, `make lint`, `make typecheck`, `make test`, and
   `make security`. These retain the locked Ruff, strict mypy, full Python suite,
   dependency audit, and hash-verified Semgrep rules.
5. For changed `desktop/` files, runs the frozen desktop install, desktop lint,
   type checks, tests, build, high-severity dependency audit, and secret check.
   Changed workflows/actions run `make workflow-audit`; changed `containers/`
   files run `make container-policy` without starting Docker.

Run `make pre-push` manually after committing to reproduce the same gate.
Fetch `origin/main` before starting or updating a branch so the comparison has a
current base. Push one checked-out branch at a time: pre-commit supplies the
selected update, rather than an inventory of every ref in a multi-ref push.
Tags, other checked-out commits, and deletion-only pushes are not a supported
validation interface; use CI and release gates for those operations.

## Test development and failure recovery

The test-change check is deliberately a structural check. It cannot prove that
assertions cover a behavior, that a test failed before implementation, or that
coverage is adequate. Follow the
[red-green-refactor process](https://github.com/sodejm/AncestryLLM/blob/main/CONTRIBUTING.md#test-driven-development) and
review the tests for the behavior being changed. This conservative check also
requires test changes for implementation-only refactors; keep a meaningful
regression test with the branch rather than adding a dummy assertion.

If a command fails, resolve its findings and rerun its canonical target while
editing, then commit and retry the push. The gate never applies fixes, stages
files, commits, pushes, starts application services, or selects a cloud provider.
Generated ignored build output from canonical checks may remain locally.
Do not skip hooks to hide a failure. Local hooks can be bypassed or uninstalled,
so CI and protected-branch rules remain necessary. Passing this gate does not
establish packaged desktop support, cross-platform release readiness, or SLSA
provenance.

The gate adds developer validation. Its initial scan also required upgrading
the locked urllib3, PyJWT, and virtualenv dependencies and giving the
docs-capture image a non-root default user. Semgrep accepts an additional
verified encoding of the unchanged Trail of Bits rule bundle; its semantic
digest remains pinned. The capture orchestrator retains its explicit non-root
runtime identity and isolation controls. Application architecture, application
trust boundaries, provider consent, and API contracts are unaffected; the
implementation is confined to developer tooling and dependency security fixes.
Scripted release builds and artifact/provenance generation remain unchanged;
this is not a release.
