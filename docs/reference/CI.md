# Continuous integration

AncestryLLM uses progressively broader gates so routine code generation gets
fast feedback while expensive cross-platform and security checks still run
before changes can be released.

## Local development

Run targeted tests while editing. `make bootstrap` installs two hook tiers:

- the commit hook retains the generic file-safety checks and local system
  gitleaks scan, then uses exact-commit `astral-sh/ruff-pre-commit` hooks for
  Ruff check and format-check behavior and exact-commit
  `astral-sh/uv-pre-commit` for `uv lock --check`; the Ruff hooks do not apply
  fixes;
- the pre-push hook runs `make pre-push`, which expands to the canonical test,
  lint, type-check, dependency-audit, and Semgrep gates. It also runs
  `make workflow-audit` when a pushed commit changes `.github/workflows/` or
  `.github/actions/`.

The exact hook commits match the lock-resolved Ruff 0.16.1 and repository uv
0.12.1 versions. CI and Make remain authoritative: `make lint` also applies
the checked-in GFM structural checks to every tracked Markdown file, whether
or not a contributor installed the hooks. `make code-docs-check` is the
separate, canonical declaration-documentation gate for Python, Swift, and the
desktop TypeScript/JavaScript tree.

`make setup` first runs the
[verified uv bootstrap](../security/verified-uv-bootstrap.md), then uses the
repository-local `.tools/uv/uv` binary to install the locked environment
without changing Git hooks. It requires a system-supplied Python 3.12-3.14;
`.python-version` selects 3.12 by default, and `[tool.uv]` disables Python
downloads. Setup runs the exact full-profile command
`uv sync --locked --all-extras --all-groups`. The bootstrap refuses an
unverified `uv` from `PATH`, re-hashes a cached local binary, and emits the sanitized
`.tools/receipts/uv-bootstrap.json` receipt before `uv --version` or another
`uv` command may run. This is the appropriate target for automation and
disposable environments. Local developers authenticate once with
`gh auth login --hostname github.com`; headless shells provide `GH_TOKEN`
through their secret manager. The bootstrap uses that credential only with the
policy-pinned, hash-verified GitHub CLI and never delegates verification to an
executable found on `PATH`.

`make container-policy` validates the production Dockerfile, base Compose
topology, and both overlays without starting Docker. It rejects mutable image
references, unsupported architectures, extra services, unsafe mounts,
published ports, privilege, missing resource/log limits, and any topology that
would activate schema migrations before Issue #351. Use it for fast local
feedback alongside the focused container contract tests.

## Locked environment profiles

The complete `uv.lock` covers every application extra and repository tool
group. Full local setup installs that complete graph, including the release
verifier. Purpose-specific workflow jobs pass `--no-default-groups` and
synchronize only the profile they execute. The lock consistency job installs no
group and calls `make lock-check`, whose canonical command is
`uv lock --check`.

| Work | Locked environment |
|---|---|
| Local full setup | All application extras and every dependency group, including `release-verifier` |
| Python test matrix | `test` plus `all-llm` |
| Quality | `lint` plus `typecheck`; exact ty is installed only for its visible advisory step |
| Declaration documentation | `lint` plus the frozen desktop workspace under exact Node 26.5.0 and pnpm 11.9.0 |
| Dependency audit, SBOM, and workflow audit | `security`; the pinned Semgrep script remains independent |
| Package and release construction | `build`; release construction also installs `security` only for SBOM generation |
| Production PyPI artifact verification | `release-verifier` only |
| Native desktop sidecar packaging | `desktop-build` only |
| Release-project proof | `test` only |

The dedicated documentation-screenshot job installs the exact repository
toolchain, then runs the canonical `make docs-screenshots-check` target under a
pinned virtual display with fixed locale and timezone. It recaptures all
Electron and terminal scenarios and performs exact-byte comparison without
changing the checkout. On failure it retains only the bounded schema-v1
hash-drift report for seven days; screenshots, DOM text, transcripts, fixtures,
environment values, and host details are never uploaded as drift evidence.

No quality, security, or build job installs provider extras. After any allowed
narrow synchronization, workflow jobs call the same canonical Make target used
locally; they do not restate or vary its command arguments. The Python 3.12
release-readiness row first exercises the provider-aware test profile, then
replaces it with `lint` and `typecheck` before static checks; the separate
security-evidence job installs `security`. This prevents a successful gate from
depending on packages left behind by another profile. The exact profiles and
maintenance procedure are documented in [Dependency
maintenance](DEPENDENCY_MAINTENANCE.md).

The Python 3.12 quality job and its release-readiness counterpart install exact
Node 26.5.0 and pnpm 11.9.0 before calling `make code-docs-check`. That Make
target owns the Ruff declaration subset, the tracked-file and Swift DocC
classifier, the TypeScript compiler-AST export/security-boundary check, and the
exact-pinned `eslint-plugin-jsdoc` syntax and description rules. The desktop
workspace is installed with the frozen lockfile; the checker itself reads only
the candidate tree and performs no network or provider calls.

The Python 3.12 quality job keeps `make typecheck` as the blocking strict-mypy
gate. A separate `make typecheck-ty` step runs exact `ty 0.0.69` over
`src/ancestryllm` with `continue-on-error: true`; it does not use `|| true`, so
the Actions UI retains ty's actual status. Release readiness and release
evidence continue to require the schema-v1 `mypy` result. The counts, parity
fixtures, Pydantic limitations, timings, and cutover disposition are recorded
in the [ty advisory evaluation](TY_ADVISORY_EVALUATION.md). Its authoritative
58-diagnostic count uses this provider-free quality profile; a documented
all-extras setup resolves five optional provider imports without satisfying the
cutover gate.

Ruff remains lock-resolved at 0.16.1. Quality jobs select GitHub annotation
output through `RUFF_OUTPUT_FORMAT=github` and still invoke the canonical
`make lint` target without restating its flags. The enabled rule families,
reviewed diagnostic batches, provider-import contract, and cold-start evidence
are recorded in the [Ruff rule-expansion evaluation](RUFF_EXPANSION_EVALUATION.md).
Declaration documentation remains a distinct gate so changes to the normal
lint surface cannot silently weaken its cross-language contract.

Production package and release jobs continue to call `make package` with
setuptools as the authoritative backend. `make evaluate-uv-build` is a
maintainer-only, locked-`build`-group comparison: it builds the same clean
commit with setuptools and `uv_build`, records schema-v1 artifact and semantic
evidence, and exits nonzero when the candidate differs. It is deliberately not
a CI or release gate because the 0.6 evaluation is incompatible; see the
[uv_build evaluation](UV_BUILD_EVALUATION.md). A future adoption change must
first satisfy that complete artifact contract in a separate 0.7 decision.

## Headless shell policy

Every workflow that executes a command sets the workflow-level default shell to
noninteractive Bash. Individual steps may override that default only when the
host requires another native shell: the Windows signing, host-inspection,
installer-validation, and cleanup steps use PowerShell because they call
Windows APIs or PowerShell-only signing tools. No CI workflow or Make recipe
uses `zsh` or an interactive shell profile.

The Makefile pins its recipe shell to `/bin/bash`, so local and CI invocations
have the same command semantics regardless of the caller's interactive shell.

## Hosted gate tiers

| Event | Required work |
|---|---|
| Pull request | An early `make lock-check` gate; tests on Python 3.12; one Python 3.12 quality job including `make code-docs-check`; Semgrep; a commit-range secret scan; deterministic documentation-screenshot drift; package build; Ubuntu/Python 3.12 wheel and source-distribution smoke tests; and native Linux amd64/arm64 container-policy and lifecycle rows when container-owned paths change. Dependency audit and SBOM generation run only when `pyproject.toml` or `uv.lock` changes. Workflow auditing runs when a workflow or local composite action changes. |
| Push to `main` | The pull-request coverage plus all nine Ubuntu/macOS/Windows and Python 3.12-3.14 wheel-install combinations, dependency audit, SBOM generation, and workflow auditing. |
| Weekly schedule or manual dispatch | The complete `main` gate set. The secret scanner checks the current `main` candidate tree from a shallow checkout. |
| Release readiness | The exhaustive release-candidate gate, including declaration documentation on the Python 3.12 quality row. Its secret scanner checks the exact frozen candidate tree, and its evidence binds the complete quality, security, compatibility, and artifact results to one exact commit. |
| Release tag | Verifies the exact approved readiness evidence, then deterministically rebuilds the distributions and SBOM and compares distribution hashes. It does not rerun unchanged pytest, lint, type, dependency-audit, or Semgrep work. |

The container matrix uses `ubuntu-24.04` for amd64 and
`ubuntu-24.04-arm` for arm64. Each row builds the gateway and optional worker
for the runner's native architecture, resolves their exact local image
digests, and runs the lifecycle harness against those digests. The harness
checks hardened realized state, readiness, crash visibility, graceful stop,
version/build skew rejection, read-only and disk-full behavior, log redaction,
and a complete Python and Debian package/license inventory. Source policy proves
that the probe-only images have no database initializer or migration entrypoint;
it does not claim an executed migration-path test. Sanitized schema-v1 lifecycle
and inventory JSON are retained as normal CI artifacts. On pull requests, these
native rows run only when container-owned source, tests, policy, configuration,
dependency metadata, or CI changes; the aggregate PR gate accepts the deliberate
skip for unrelated paths. Both native rows must complete when selected. A missing
architecture, interrupted run, or emulated substitute is incomplete rather than
passing.

## Version 1 security dependency governance

The release-project proof, release-readiness, and release workflows use the
same checked-in GraphQL query and schema-v1 policy to validate Version 1
security sequencing. The policy names the exact Project and repository, the
required issue owner and release iteration, every required native GitHub
`blocked by` relationship, the permitted iteration order, and #131 as the
release-evidence consumer. Workflow-local variants of the query are rejected
by contract tests.

The verifier fails closed with stable coded errors when Project pagination or a
required field cannot be verified, an issue is missing, ownership or iteration
is wrong, an edge is missing or contradicted by its reverse, the graph has a
cycle, a prerequisite is scheduled after its dependent, or a dependent is
closed while a prerequisite remains open. Its schema-v1 JSON report contains
only issue numbers, reviewed Project coordinates, dependency pairs, the
canonical policy digest, and status. It excludes titles, bodies, comments,
tokens, environment values, user paths, and response bodies.

Release evidence accepts the `version-1-security-dependencies` result only when
the report matches the current policy digest and exact policy-derived issue,
dependency, Project, repository, and #131 consumer sets. A stale, substituted,
partial, or structurally unknown report cannot satisfy the gate. This control
still relies on authorized GitHub maintainers plus GitHub's Project, issue,
dependency, API, and token behavior; API unavailability or insufficient access
leaves the gate incomplete rather than passing.

## Job timeout governance

Every job in the required CI, CodeQL, dependency-review, desktop, release-project
proof, release-readiness, and release workflows has a reviewed literal
`timeout-minutes` value. A closed workflow contract records the complete governed
job set and rejects a missing job, an unreviewed job, an expression-based timeout,
or a timeout placed after executable steps. Matrix limits apply independently to
each matrix row.

The ceilings deliberately leave substantial margin above recent successful hosted
runtimes while bounding a stalled runner:

| Work class | Reviewed ceiling | Recent successful baseline |
|---|---:|---:|
| Coordination, classification, evidence, and proof | 1-15 minutes | Usually seconds |
| Python tests, audits, CodeQL, builds, and consumer installation | 20-30 minutes | Python jobs under 3 minutes; stock-`pip` platform installation about 1 minute or less |
| Native desktop package rows | 45 minutes | Under 9 minutes |
| Release installer validation and native release builds | 60 and 90 minutes | Intentionally wider because release signing, packaging, and platform services have greater variance |

These observations are review baselines, not service-level guarantees. The wider
limits accommodate ordinary GitHub-hosted Python, Node, cache, network, and
platform variance. Queue delay before a runner starts and a broader GitHub Actions
outage are outside a job timeout; missing or incomplete hosted evidence remains a
failure to satisfy the gate rather than a pass.

The deterministic proof is an explicitly requested manual CI mode:

```bash
gh workflow run ci.yml --ref main -f timeout_proof=true
```

The expected workflow conclusion is failure. GitHub reports the timed-out exercise
job itself as `cancelled`: it first uploads a schema-v1 armed record, then runs a
fictional five-minute sleep under a one-minute job timeout. An always-run evidence
job requires that exact `cancelled` result, uploads a sanitized confirmed record,
emits a stable failure code, and deliberately fails so the proof cannot be mistaken
for a successful required check. A `failure`, `success`, `skipped`, or other result
fails verification without producing confirmed evidence. The records contain only
fixed fixture identifiers, durations, schema/status fields, and the job result; they
contain no secrets, environment values, usernames, hostnames, private or temporary
paths, response bodies, genealogy data, or application payloads.

This governance changes repository workflow availability and evidence only. It
does not change the application API, CLI registry, DTOs, provider behavior,
GEDCOM representation, storage, FastAPI, or Electron boundaries, so
`ARCHITECTURE.md` requires no change. The security benefit is bounded runner and
quota retention plus timely fail-closed evidence; it does not mitigate a GitHub
queue or platform outage.

Every workflow job that uses `uv` calls the repository-local
`setup-verified-uv` composite action. The action performs the same policy
preflight with the ephemeral job-scoped GitHub token, passes the policy-selected
checksum to the exact pinned `setup-uv` commit with Astral mirror downloads
disabled, re-hashes the installed binary before execution, and retains the
schema-v1 receipt. Each calling job grants the verifier `contents: read` and
`attestations: read`; jobs retain only any additional job-specific scope already
required by their release contract. The token is not included in receipts or
action outputs.
Only the bounded `gh attestation verify` subprocess receives the token; version
probes and all `uv` subprocesses receive an environment with GitHub token
variables removed. An attestation verifier that exceeds 60 seconds fails with a
stable coded error before `uv` can execute.
The repository Actions allowlist permits only
`astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9` for that
external action. The policy, local action, and workflow contracts independently
require the same commit. The receipt upload includes the ignored `.tools`
directory and fails if the expected file is absent. The cache key includes
`uv.lock`, Python version, runner operating system, and runner architecture.
Release-readiness and release evidence include the receipt's policy digest and
verified identity through the required `bootstrap-verification` gate.

The explicitly enumerated wheel and source-distribution consumer smoke jobs
continue to use stock `pip`. Those jobs test what supported non-`uv` consumers
receive; they do not build release artifacts or provide an exception to the
verified bootstrap for repository commands.

The `PR gate` job aggregates repository-owned pull-request jobs behind one
stable check name. A conditionally skipped workflow audit is accepted; every
other aggregate dependency must succeed. GitHub's Dependency Review and CodeQL
checks remain independent required security controls.

TruffleHog retains its provider detectors and fails on verified or unknown
results. Pull-request and protected-push runs scan the event's commit range;
scheduled and manually dispatched CI scans the current `main` candidate tree;
release readiness scans the exact frozen candidate tree. This keeps candidate
evidence scoped to the source that can actually ship. GitHub secret scanning
and push protection remain the repository controls for immutable Git history
and incoming pushes; the candidate-tree scans do not replace those controls.

Workflow-level path filters are intentionally not used for the required CI
workflow. A filtered-out workflow may never create its required check. Path
classification happens inside the workflow so the aggregate gate always
reports a conclusion.

The desktop workflow applies the same pattern through the stable `Desktop
gate`. Its source-security job and six native unpublished-package rows emit
exact-head machine-readable evidence. They do not establish production binary
signing. Project-produced 0.x release installers must be unsigned; full trusted
binary signing is deferred until and required starting with v1.0.0. Release validation still
requires the exact installer bytes to install and execute on the supported OS
matrix. See the [desktop verification guide](../DESKTOP_VERIFICATION.md).

## Semgrep rule policy

The Semgrep gate scans the whole repository with one generated, local config.
Every upstream input is pinned by exact bytes and semantic content, and rules
with identical IDs or matching logic are deduplicated before Semgrep runs. A
changed archive, missing reviewed rule, conflicting rule ID, or unreviewed
redirect fails closed.

The reviewed third-party additions deliberately cover only current repository
surfaces:

- ten generic command-line and transport-hardening rules from
  [Trail of Bits](https://github.com/trailofbits/semgrep-rules);
- twelve Python and JavaScript/TypeScript dynamic-execution and high-signal
  obfuscation rules used by [Apiiro PRevent](https://github.com/apiiro/PRevent),
  sourced from its separate
  [malicious-code ruleset](https://github.com/apiiro/malicious-code-ruleset);
- two GitHub Actions workflow-command rules from
  [elttam](https://github.com/elttam/semgrep-rules).

The [0xdea rules](https://github.com/0xdea/semgrep-rules) are not loaded because
their maintained rules target C and C++, which this repository does not contain.
Trail of Bits rules for absent frameworks and container tooling, overlapping
secret rules, Apiiro rules that produced false positives or parser failures,
and elttam's manual-audit and absent-framework rules are also excluded. Revisit
this allowlist when the repository adds a language or framework; do not add a
whole upstream pack without a clean scan and an overlap review.

## Ruleset migration for the pull-request matrix

The pull-request install-matrix reduction must be introduced in two phases.
The feature branch records those phases as separate commits.

### Phase A: establish the aggregate gate

Deploy the first commit while retaining all nine existing install-smoke matrix
contexts. Confirm a pull request reports a successful `PR gate` alongside every
check currently required by the `main` ruleset.

After that successful hosted observation, update the ruleset to require the
stable `PR gate` rather than individual repository-owned CI job and matrix
names. Keep the independent hosted security checks (currently Dependency
Review and CodeQL's `Analyze (Python)`), signed commits, linear history, review,
and conversation-resolution protections.

### Phase B: reduce the pull-request matrix

Only after the ruleset requires `PR gate`, deploy the second commit. Pull
requests will then run the Ubuntu/Python 3.12 wheel-install cell plus the
separate source-distribution smoke test. Pushes to `main`, schedules, and manual
dispatches continue to run all nine wheel-install combinations.

Do not merge both commits at once while the ruleset still requires individual
matrix contexts. The eight intentionally absent pull-request contexts would not
be produced, so the old ruleset could leave the pull request waiting
indefinitely.
