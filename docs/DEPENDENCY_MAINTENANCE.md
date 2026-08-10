# Dependency maintenance

AncestryLLM records application features as optional extras and repository
tooling as purpose-specific PEP 735 dependency groups. The complete resolution
for every extra and group is committed in `uv.lock`, but each command installs
only the environment it needs. This keeps quality, security, build, desktop,
and release-verification jobs from relying on undeclared cross-profile tools.

The repository-local, verified `uv` 0.12.1 bootstrap is the only supported way
to create or update these environments. `uv` is not a project dependency and
must not be installed through a dependency group, `pip`, an alternate index,
or an unverified executable on `PATH`.

## Environment contracts

The dependency groups are:

| Group | Purpose | Canonical consumers |
|---|---|---|
| `lint` | Ruff, pre-commit, and repository checks | `make lint`, `make hooks`, and CI quality jobs |
| `typecheck` | Strict mypy and third-party type information | `make typecheck` and CI quality jobs |
| `test` | Pytest and coverage | `make test`, Python test matrices, and release-project proof jobs |
| `security` | Dependency audit, SBOM, and workflow audit tools | `make security`, `make sbom`, `make workflow-audit`, and matching workflow jobs |
| `build` | Distribution construction and artifact validation | `make package` and package/release build jobs |
| `release-verifier` | Exact PyPI attestation verifier | Release artifact verification only |

`make setup` installs every user-facing optional extra plus `lint`,
`typecheck`, `test`, `security`, and `build`. It deliberately excludes
`release-verifier`, whose platform-specific dependencies are needed only after
production publication. Each canonical gate re-synchronizes to its smaller
declared profile with `--no-default-groups`, so running a gate does not depend
on tools left behind by `make setup` or another gate.

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
   each affected canonical Make target from a clean environment. A command
   succeeds only when its declared group supplies every tool it executes.
5. Run the full applicable quality and security gates and update contributor,
   CI, security, release, and user installation documentation when the changed
   dependency affects those surfaces.

The lock-check workflow installs no group and runs only `uv lock --check`.
Stock-`pip` wheel and source-distribution smoke jobs remain intentionally
outside these repository-tool profiles: they verify supported non-`uv`
consumer installation and do not build or authorize release artifacts.

## Review checklist

- Every former development dependency is present in exactly one appropriate
  group, retained as a named optional extra, or has a documented removal audit.
- Provider extras and `desktop-build` retain their public installation meaning.
- Workflow jobs use the same group profile as their canonical Make target and
  pass `--no-default-groups`.
- Semgrep continues through the independently pinned script even though the
  surrounding security tools come from the `security` group.
- Release verification installs only `release-verifier`; ordinary development
  and release construction do not inherit it.
- The complete lock retains artifact hashes for every supported platform and
  all extras and groups without unexplained package-version movement.
- Application APIs, CLI commands, service DTOs, provider policy, GEDCOM
  behavior, storage, FastAPI, and Electron boundaries remain unchanged by a
  tooling-only dependency update.
