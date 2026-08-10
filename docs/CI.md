# Continuous integration

AncestryLLM uses progressively broader gates so routine code generation gets
fast feedback while expensive cross-platform and security checks still run
before changes can be released.

## Local development

Run targeted tests while editing. `make bootstrap` installs two hook tiers:

- the commit hook scans staged content for secrets, private keys, malformed
  files, merge markers, oversized files, and basic whitespace/line-ending
  problems;
- the pre-push hook runs `make pre-push`, which expands to the canonical test,
  lint, type-check, dependency-audit, and Semgrep gates. It also runs
  `make workflow-audit` when a pushed commit changes `.github/workflows/` or
  `.github/actions/`.

`make setup` first runs the
[verified uv bootstrap](security/verified-uv-bootstrap.md), then uses the
repository-local `.tools/uv/uv` binary to install the locked environment
without changing Git hooks. The bootstrap refuses an unverified `uv` from
`PATH`, re-hashes a cached local binary, and emits the sanitized
`.tools/receipts/uv-bootstrap.json` receipt before `uv --version` or another
`uv` command may run. This is the appropriate target for automation and
disposable environments. Local developers authenticate once with
`gh auth login --hostname github.com`; headless shells provide `GH_TOKEN`
through their secret manager. The bootstrap uses that credential only with the
policy-pinned, hash-verified GitHub CLI and never delegates verification to an
executable found on `PATH`.

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
| Pull request | An early `uv lock --check` gate; tests on Python 3.12; one Python 3.12 quality job; Semgrep; a commit-range secret scan; package build; Ubuntu/Python 3.12 wheel and source-distribution smoke tests. Dependency audit and SBOM generation run only when `pyproject.toml` or `uv.lock` changes. Workflow auditing runs when a workflow or local composite action changes. |
| Push to `main` | The pull-request coverage plus all nine Ubuntu/macOS/Windows and Python 3.12-3.14 wheel-install combinations, dependency audit, SBOM generation, and workflow auditing. |
| Weekly schedule or manual dispatch | The complete `main` gate set. The secret scanner checks the current `main` candidate tree from a shallow checkout. |
| Release readiness | The exhaustive release-candidate gate. Its secret scanner checks the exact frozen candidate tree, and its evidence binds the complete quality, security, compatibility, and artifact results to one exact commit. |
| Release tag | Verifies the exact approved readiness evidence, then deterministically rebuilds the distributions and SBOM and compares distribution hashes. It does not rerun unchanged pytest, lint, type, dependency-audit, or Semgrep work. |

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
matrix. See the [desktop verification guide](DESKTOP_VERIFICATION.md).

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
