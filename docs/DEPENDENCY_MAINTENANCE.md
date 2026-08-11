# Dependency maintenance

AncestryLLM records application features as optional extras and repository
tooling as purpose-specific PEP 735 dependency groups. The complete resolution
for every extra and group is committed in `uv.lock`. Local setup synchronizes
the complete graph; purpose-specific workflow jobs synchronize only the
environment they need before calling the canonical Make command. This keeps
quality, security, build, desktop, and release-verification jobs from relying
on undeclared cross-profile tools.

The repository-local, verified `uv` 0.12.1 bootstrap is the only supported way
to create or update these environments. It requires a system-supplied Python
3.12-3.14; `.python-version` selects 3.12 by default, and `[tool.uv]` prohibits
Python downloads. `uv` is not a project dependency and must not be installed
through a dependency group, `pip`, an alternate index, or an unverified
executable on `PATH`.

## Environment contracts

The dependency groups are:

| Group | Purpose | Canonical consumers |
|---|---|---|
| `lint` | Ruff, pre-commit, GFM Markdown validation, and repository checks | `make lint`, `make markdown-check`, `make hooks`, and CI quality jobs |
| `typecheck` | Strict mypy, third-party type information, and exact ty advisory evaluation | `make typecheck`, `make typecheck-ty`, and CI quality jobs |
| `test` | Pytest and coverage | `make test`, Python test matrices, and release-project proof jobs |
| `security` | Dependency audit, SBOM, and workflow audit tools | `make security`, `make sbom`, `make workflow-audit`, and matching workflow jobs |
| `build` | Distribution construction, artifact validation, and isolated backend evaluation | `make package`, `make evaluate-uv-build`, and package/release build jobs |
| `release-verifier` | Exact PyPI attestation verifier | Release artifact verification only |

`make setup` runs `uv sync --locked --all-extras --all-groups`, including the
`release-verifier` group, so a local source checkout has the complete locked
graph. Canonical gates use exact `uv run --locked --group ...` commands. A
purpose-specific workflow may first synchronize a smaller profile with
`--no-default-groups`, but it then invokes the same Make target without changing
the actual command or flags.

`ty==0.0.69` is deliberately exact because the 0.6 work is a reproducible
advisory evaluation, not a floating checker migration. `make typecheck-ty`
preserves its real exit status in a dedicated nonblocking CI step. Strict mypy,
`types-python-dateutil`, and `pydantic.mypy` remain authoritative until every
conditional cutover gate passes. See the
[ty advisory evaluation](TY_ADVISORY_EVALUATION.md) for the current diagnostic
triage, the quality-profile and all-extras counts, and the failed cutover
conditions.

`uv_build>=0.12.0,<0.13` belongs to `build` only as the locked candidate for
the isolated 0.6 backend evaluation. The production `[build-system]` remains
`setuptools.build_meta`, and `make package` plus every release workflow retain
the established setuptools normalization and validation path. From a clean
checkout, `make evaluate-uv-build` compares both backends under one source
epoch and writes a schema-v1 report; its nonzero incompatible result must not
be masked. The checked [uv_build evaluation](UV_BUILD_EVALUATION.md) records
why adoption is rejected/deferred for the candidate configuration.

Provider SDKs remain user-facing optional extras: `ollama`, `openai`,
`anthropic`, `gemini`, `openrouter`, and the aggregate `all-llm`. The Python
test matrix combines `test` with `all-llm` for provider coverage. Quality,
security, and build jobs do not install provider extras because their commands
do not import or execute those SDKs. Installing an extra never selects a
provider or grants cloud consent; `provider=none` remains network-free.

`desktop-build` remains a user-facing build extra because PyInstaller is part
of the desktop sidecar artifact rather than an ordinary repository tooling
environment. Desktop native build jobs install that extra alone. `click` is no
longer a direct dependency after source-import and entry-point audits found no
repository consumer; it may still appear transitively in `uv.lock`. The
verified bootstrap supplies `uv`, so the lock intentionally contains no direct
`uv` environment dependency.

## Complete dependency-audit export

`make dependency-audit` executes `scripts/run_dependency_audit.py` from the
locked `security` group. The runner generates its input with the exact command
`uv export --locked --all-extras --all-groups`, then compares normalized
package-name/version pairs with every applicable package in `uv.lock` before
allowing `pip-audit` to run. A missing provider extra, desktop-build package,
dependency-group tool, release verifier, or transitive dependency fails closed
instead of silently narrowing the audit.

The closed-schema allowlist at
`config/dependency-audit-exclusions.json` documents the only formal export
exception: the editable source project itself. Unknown fields, duplicate or
unused entries, unpinned export records, and lock/export drift are stable coded
errors. `pip-audit` still runs from the locked `security` group with hashes and
strict dependency processing; this completeness check does not replace
Semgrep, zizmor, CycloneDX, gitleaks, TruffleHog, or CodeQL.

## Update procedure

1. Edit the narrow direct dependency in `pyproject.toml`. Keep application
   extras separate from repository tooling groups and preserve exact pins for
   trust-sensitive verifiers.
2. Run `make lock`. This verifies and executes repository-local `uv` 0.12.1
   before regenerating the complete lock for all extras and groups.
3. Run `make lock-check`, then inspect both the direct-dependency diff and every
   changed package record in `uv.lock`. An unrelated version change, missing
   artifact hash, alternate source, or unexplained transitive re-resolution is
   a failure, not routine lockfile noise.
4. Run the focused dependency-group and workflow contract tests, followed by
   each affected canonical Make target from a clean environment. Purpose-
   specific workflow profiles must succeed without tools inherited from
   another group.
5. Run the full applicable quality and security gates and update contributor,
   CI, security, release, and user installation documentation when the changed
   dependency affects those surfaces.

The lock-check workflow installs no group and runs `make lock-check`, whose
canonical command is `uv lock --check`.
Stock-`pip` wheel and source-distribution smoke jobs remain intentionally
outside these repository-tool profiles: they verify supported non-`uv`
consumer installation and do not build or authorize release artifacts.

## Review checklist

- Every former development dependency is present in exactly one appropriate
  group, retained as a named optional extra, or has a documented removal audit.
- Provider extras and `desktop-build` retain their public installation meaning.
- Workflow jobs use the group profile declared for their canonical Make target,
  pass `--no-default-groups`, and invoke that target without command drift.
- Semgrep continues through the independently pinned script even though the
  surrounding security tools come from the `security` group.
- The dependency-audit export covers all extras and groups, and its normalized
  package pairs match `uv.lock` except for the reviewed, used source-project
  entry in `config/dependency-audit-exclusions.json`.
- Production release verification installs only `release-verifier`; release
  construction does not inherit it. Full local setup intentionally includes
  every group.
- The complete lock retains artifact hashes for every supported platform and
  all extras and groups without unexplained package-version movement.
- Application APIs, CLI commands, service DTOs, provider policy, GEDCOM
  behavior, storage, FastAPI, and Electron boundaries remain unchanged by a
  tooling-only dependency update.
