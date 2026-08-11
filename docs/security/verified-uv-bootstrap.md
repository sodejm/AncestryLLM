# Verified uv bootstrap

AncestryLLM verifies the pinned `uv` release before any repository setup, CI,
build, audit, or release command may execute it. Developer setup and every
applicable workflow consume the same machine-readable policy at
`config/uv-bootstrap-policy.json` through the standard-library-only
`scripts/bootstrap_uv.py` utility.

This is a repository-tooling security control. It does not change ancestry
application APIs, CLI or REPL commands, provider behavior, GEDCOM handling,
storage, FastAPI contracts, or desktop boundaries.

## Developer setup

Use a supported system Python 3.12-3.14. The checked-in `.python-version`
selects 3.12 by default; `[tool.uv]` permits only a system interpreter and
disables Python downloads. GitHub's attestation API also requires
authentication. For interactive development, authenticate once with:

```bash
gh auth login --hostname github.com
```

For a headless shell, provide `GH_TOKEN` from the shell's secret manager or
credential injection mechanism. Do not put a token in a command argument,
tracked file, shell history, or bootstrap receipt. Then run:

```bash
make setup
```

The target runs the verified bootstrap, installs `uv` beneath the ignored
repository-local `.tools/uv/` directory, writes the sanitized receipt at
`.tools/receipts/uv-bootstrap.json`, and only then uses that exact binary. To
perform only the verification and installation phase, run:

```bash
python3 scripts/bootstrap_uv.py bootstrap
```

The `gh auth login` command provisions the standard GitHub CLI credential; it
does not verify or install `uv`. The bootstrap never delegates verification to
the executable on `PATH`: it reads the provisioned credential only after the
policy-pinned GitHub CLI archive passes its own hash and archive checks.

`uv` is supplied by this bootstrap, not by an application extra or PEP 735
dependency group. After verification, `make setup` runs
`uv sync --locked --all-extras --all-groups`, including the release verifier.
Purpose-specific workflows may synchronize smaller locked profiles before
calling the same canonical Make targets, and the production PyPI verification
job installs only `release-verifier`. See [Dependency
maintenance](../DEPENDENCY_MAINTENANCE.md) for the complete group contract.

Do not replace this command with a `curl | sh` installer, `pip install uv`, an
implicit latest release, an alternate index or mirror, or an existing `uv` on
`PATH`. A cached repository-local executable is re-hashed against policy before
reuse. A mismatch fails closed without deleting user data; remove only the
specific ignored `.tools/uv/` cache and run the bootstrap again after resolving
the cause.

## Trust policy

Policy schema v1 binds all executable inputs needed by the bootstrap:

- exactly `uv` 0.12.1 from `astral-sh/uv`, including the reviewed source commit
  and ref, release repository and tag, GitHub Actions OIDC issuer, signer
  workflow identity, and SLSA provenance-v1 predicate;
- exact release archive names, reviewed byte sizes, archive SHA-256 values,
  paths, and extracted executable SHA-256 values for Linux, macOS, and Windows
  on x86-64 and ARM64;
- GitHub CLI 2.97.0 as the pinned provenance verifier, with an exact archive and
  reviewed byte size and SHA-256 for every supported platform;
- `astral-sh/setup-uv` v9.0.0 at its exact reviewed commit; and
- `pypi-attestations` 0.0.30, its trusted PyPI project and source repository,
  and every permitted wheel or sdist filename, URL, and SHA-256. The same
  verifier release and artifact hashes are represented by `uv.lock` in the
  non-default `release-verifier` dependency group. Full local setup includes
  that group, while the production verification job installs it alone.

Unknown schema versions, operating systems, architectures, archive names,
URLs, policy fields, or omitted trust fields are rejected. There is no mirror,
alternate index, implicit latest version, or unverified `PATH` fallback.

## Verification phases

The bootstrap downloads the policy-selected GitHub CLI archive into a temporary
directory and compares its SHA-256 in constant time. Each download must report
the reviewed byte size, may stream no more than that size, and must complete
within the bounded acquisition deadline. The remaining deadline is applied to
each single underlying transport read; a mismatch, overrun, underrun, or
deadline failure discards the partial file. Malformed or incomplete HTTP
protocol responses fail with the stable `DOWNLOAD_FAILED` category. Before
extraction the bootstrap
rejects absolute paths, parent traversal, symbolic or hard links, device files,
and any member that would escape the empty temporary extraction root. Only the
verified GitHub CLI may execute.

The utility then downloads the exact policy-selected `uv` release URL, verifies
the archive digest, and asks the verified GitHub CLI to verify the release
attestation against `github.com` within a 60-second subprocess deadline;
ambient `GH_HOST` configuration cannot select another host. A timeout fails as
`ATTESTATION_VERIFICATION_TIMEOUT` before extraction or `uv` execution. The
returned statement must bind the selected asset digest to the exact source
repository, source commit and ref, signer workflow, OIDC issuer, and SLSA
predicate. The extracted `uv` executable receives a second digest check and
exact-version check before an atomic repository-local installation. Extraction
selects only the exact policy-reviewed archive member; an absent or differently
located executable fails closed before any candidate binary executes. In uv
0.12.1, both reviewed Windows ZIP archives contain `uv.exe` at the archive root,
independent of the architecture-specific archive filename.
The install and receipt destinations are resolved through stable parent handles:
POSIX uses directory descriptors and relative filesystem operations, while
Windows holds every ancestor directory open without delete sharing and rejects
reparse points. Temporary-file creation and atomic replacement stay anchored to
the held destination parent, so renaming an ancestor or replacing it with a link
cannot redirect either write. Existing destination links and reparse points are
also rejected.

In GitHub Actions, `.github/actions/setup-verified-uv/action.yml` performs that
preflight with the job-scoped `github.token`, supplies the selected checksum and
exact version to the pinned `setup-uv` commit with Astral mirror downloads
disabled, and re-hashes the action-installed executable before first use. The
calling job grants the verifier `contents: read` and `attestations: read`; jobs
retain only any additional job-specific scope already required by their release
contract.
The token is available only to the `gh attestation verify` subprocess; the
verified GitHub CLI version probe and every `uv` subprocess receive an
environment with GitHub token variables removed. The token is never written to
the receipt or action output. Repository Actions policy permits only
`astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9` for that
external action; the policy, local action, and workflow contracts independently
select the same commit and reject a mutable action reference. Setup-uv caching
is keyed by `uv.lock`, Python version, runner OS, and runner architecture. The
local action uploads the hidden receipt on success or failure and treats a
missing receipt as an error. `make workflow-audit` and the equivalent hosted
gate audit both `.github/workflows/` and `.github/actions/`.

The stock-`pip` wheel and sdist consumer smoke jobs are deliberately unchanged.
They verify supported non-`uv` installation paths and neither build release
artifacts nor exempt repository commands from this trust policy.

## Verification receipt and release evidence

A schema-v1 JSON receipt records only the policy SHA-256 and schema version,
tool and verifier versions, normalized platform/architecture, selected archive
names and SHA-256 values, extracted `uv` binary SHA-256, verified source and
signer identity, UTC timestamp, success status, and a stable failure category.
It excludes tokens, environment values, usernames, hostnames, absolute or
temporary paths, and response bodies.

If the pinned setup action fails after preflight, or if its installed binary
fails the final digest and identity check, the composite action atomically
transitions the canonical success receipt to `SETUP_UV_ACTION_FAILED` or
`INSTALLED_UV_VERIFICATION_FAILED` before upload. An unknown failure category or
a malformed, incomplete, extended, or non-success receipt cannot be
transitioned.

If policy loading or validation, platform selection, policy hashing, or clock
validation fails before those reviewed identity fields are available, the
bootstrap emits a minimal schema-v1 failure envelope containing only
`schema_version`, `status`, and `failure_category`. It never fills unavailable
fields with unverified values. This envelope preserves stable failure evidence
for CI upload but is intentionally insufficient for release authorization.

CI retains the receipt as a normal artifact. Release-readiness and release jobs
require it through the `bootstrap-verification` evidence gate and include its
digest and verified identity in the release manifest. A failed, malformed,
timestamp-invalid, or policy-mismatched receipt cannot authorize release
evidence.

## Failure and recovery

Failures use stable coded categories such as `POLICY_SCHEMA_UNSUPPORTED`,
`PLATFORM_UNSUPPORTED`, `ARCHITECTURE_UNSUPPORTED`,
`DOWNLOAD_SIZE_MISMATCH`, `DOWNLOAD_DEADLINE_EXCEEDED`,
`ARCHIVE_MEMBER_UNSAFE`, `TEMPORARY_WORKSPACE_FAILED`, `INSTALL_PATH_UNSAFE`,
`INSTALL_WRITE_FAILED`, `RECEIPT_PATH_UNSAFE`, `RECEIPT_WRITE_FAILED`,
`VERIFIER_ARCHIVE_DIGEST_MISMATCH`, `UV_ARCHIVE_DIGEST_MISMATCH`,
`VERIFIER_AUTHENTICATION_FAILED`, `ATTESTATION_VERIFICATION_TIMEOUT`,
`ATTESTATION_IDENTITY_MISMATCH`, and `UV_VERSION_MISMATCH`. Treat every failure
as a trust-chain failure until its cause is understood. Do not bypass it with a
global installation or by editing the receipt.

For an interrupted download or a cache mismatch, leave user files untouched,
remove only the repository-local ignored `.tools/uv/` cache, and retry. For a
local `INSTALL_WRITE_FAILED`, correct the repository-local directory's space or
permissions and retry; the failed temporary install is discarded. For a
policy, provenance, or identity mismatch, stop and review the upstream release
and policy history before changing any trusted value. In Actions,
`VERIFIER_AUTHENTICATION_FAILED` means the job-scoped token was unavailable or
rejected; restore the standard token and required read permissions rather than
supplying a personal access token. Locally, renew the standard credential with
`gh auth login --hostname github.com`, or provide `GH_TOKEN` through a secret
manager for a headless shell. Preserve only sanitized receipts when
reporting a failure; do not attach download responses, temporary directories,
environment dumps, or credentials.

## Reviewed policy updates

Update the policy only in a focused, reviewed pull request. The same change must
review and update all of the following where applicable:

1. the `uv` version, release tag and repository, source commit and ref, signer
   workflow, OIDC issuer, and predicate type;
2. every supported platform's exact archive name, reviewed byte size, archive
   digest, extracted binary path, and binary digest;
3. the GitHub CLI verifier version and every supported archive name, path, and
   reviewed byte size and digest;
4. the setup-uv action version and immutable commit;
5. the Python verifier's exact project and source identity, permitted artifact
   names, canonical PyPI URLs, and digests, together with the matching
   `pyproject.toml` pin and `uv.lock` entries; and
6. bootstrap, workflow-contract, release-evidence, archive-safety, corrupted
   cache, receipt-sanitization, and policy-rejection tests.

Obtain hashes and provenance from the reviewed upstream release, compare all
supported platform assets rather than only the maintainer's host, inspect every
archive member listing directly rather than deriving a member path from its
archive filename, and preserve the existing verified chain while preparing the
update. Never use the candidate unverified executable to establish trust in
itself. Run the focused suites and all applicable canonical setup, test, lint,
type-check, security, package, workflow-audit, documentation, and
release-evidence gates. Native hosted results for every supported platform
remain required before merge or release; local or emulated evidence is not a
substitute.
