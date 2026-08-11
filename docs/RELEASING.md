# Release runbook

Only a clean, reviewed commit on `main` may become a release. Production
publishing uses GitHub Actions OIDC Trusted Publishing; API tokens and local
uploads are prohibited.

Repository release coordinates are defined in
`.github/release-config.json`. Its stable package version and GitHub Project 2
release fields are one reviewed release control. The Project fields are the
owner, Project number and title, `Release iteration`, `Priority`, `Status`, and
`Validation`; no successor tracker issue is required. The release workflow
requires the configured version, `pyproject.toml`, `desktop/package.json`, and
the packaged sidecar build identity to match exactly before either pre-tag
packaging or tagged publication can proceed. Readiness and publication use the
configured Project values instead of inferring release state from an issue
number or version string.

The published v0.4.0 release continues to use its preserved milestone/tracker
evidence. Schema 2 is the v0.5.0-and-later control plane: its selected Project
iteration, currently `v0.5.0 — Foundation`, is authoritative for future
release readiness.

## Future deployment-profile release gate

[ADR-0026](ADR-0026-local-first-container-remote-deployment.md) is an accepted
target, not a current availability claim. A profile remains unavailable until
its row below and the common conditions pass. A gate or subset assigned to one
profile does not block an independent profile whose own row is complete.

| Profile | Required threat-model evidence |
|---|---|
| Local Desktop containers | `G0`, `G5`, and the Local Desktop container-acquisition, native-image, lifecycle, rollback, uninstall, and support-lifetime parts of `G7`. Remote edge, identity, and host-operations evidence from `G6` is not applicable. |
| Connect Remote | `G0`; the enrolled-client, endpoint, TLS, session, authorization, and client-side portions of `G6`; and the desktop-client acquisition, integrity, upgrade, rollback, and support-lifetime parts of `G7`. Local Docker/runtime evidence from `G5` and Host Remote operations are not applicable. |
| Host Remote | `G0`, the hosting edge, identity, authorization, enrollment, custody, external-scan, backup, and recovery parts of `G6`, and the host image/runtime distribution, startup, shutdown, upgrade, rollback, uninstall, runbook, capacity, and support-lifetime parts of `G7`. Local Desktop supervisor evidence is not applicable. |

Every required gate or subset needs linked implementation evidence, no
untriaged Critical or High finding, and approval by a reviewer other than the
implementer. The common conditions are:

1. Every claimed OS, architecture, Docker Engine API, Compose version, and
   runtime is tested natively. Emulation is labeled and does not establish
   native support. Colima/Lima is the open-source macOS default; Docker Desktop
   is optional and separately licensed.
2. ADR-0026's startup, shutdown, CPU, memory, PID, storage, inode, log,
   connection, worker, job, request-size, image-size, listener, and zero-network
   budgets pass on the release candidate for that profile.
3. OCI digests, checksums, dependency licenses, SBOM, and provenance are
   verified; images and artifacts contain no credentials, genealogy data, or
   Docker authority; and any later artifact-signing boundary is documented.
4. Operator documentation covers profile intent, the renderer and Docker trust
   boundaries, listeners, TLS, authentication, secret and SQLCipher-key
   custody, restore drills, upgrades, rollback, uninstall, capacity,
   monitoring, incident response, and recovery. Host Remote remains a
   single-household, self-supported profile with no project-operated SLA.

Failure of any condition blocks the affected profile without blocking the
existing local CLI, REPL, or released bounded desktop shell.

### Private Project read token

Project 2 is private. Configure the repository Actions secret
`ANCESTRYLLM_PROJECT_READ_TOKEN` with a classic personal access token for an
account that can view the Project and has only the `read:project` scope needed
for this query. The release workflows retain `github.token` for repository and
Actions operations, but supply the named secret only to the Project GraphQL
read. A missing or unusable secret fails the Project gate closed.

`Release Project gate proof` runs for every push to protected `main`, checks
that the checkout and `origin/main` are the triggering commit, validates the
configured release coordinates, and performs the authenticated Project query.
It verifies pagination and target-iteration field schema without claiming that
the in-development iteration is ready to release; a deterministic regression
then proves the strict verifier rejects an open P0 item. `Release readiness`
and the tag workflow continue to use the strict live gate. The proof has no
pull-request or manual trigger, so a fork or Dependabot pull request cannot receive the secret.
While the triggering commit remains the tip of `main`, use GitHub's rerun
mechanism to retry its immutable run. If `main` advances, the earlier candidate
and its proof are superseded; require a successful proof from the newer tip
instead. The exact-main run for each candidate is the hosted proof; do not
create the secret in a pull request or place the token in repository files.

## Binary-signing version boundary

AncestryLLM will **not sign project-produced release artifacts or release tags
until the first full version release, v1.0.0**. Every stable `0.x` installer
must therefore use `binarySigningMode: "unsigned"`, and every stable `0.x`
release tag must use `releaseTagMode: "unsigned-annotated"`. Developer ID,
notarization, Authenticode, detached GPG, ad hoc signing, and Git tag-signing
credentials must not be used for an official pre-1.0 release.

Starting with v1.0.0, full trusted platform signing and a signed annotated
release tag are mandatory; unsigned or self-signed release binaries and an
unsigned release tag are rejected. This release-output boundary does not relax
repository identity controls such as signed commits on the protected branch.

Every pre-1.0 release must disclose its `binarySigningMode` and
`releaseTagMode` in release evidence and release notes, warn that the operating
system may show an unknown-publisher or equivalent prompt, and retain all
checksum, SBOM, provenance, exact-head, installation, and installed-runtime
gates. Deferring signing is not a waiver of those gates. The macOS desktop
verification workflow may apply an ephemeral ad hoc signature solely to launch
an unpublished fuse-mutated test bundle on a hosted runner; that bundle must
never be distributed, imported into a release, or accepted as release-signing
evidence.

## v0.5.0 supported offline shell

v0.5.0 is a supported offline three-OS Electron shell. Its installer matrix is
macOS 15 arm64, macOS 15 x64, Windows 11 ARM64, and Ubuntu 24.04 x64. The
matching-architecture DMGs cover the supported macOS 15/26 range. Its release
scope is Home, Diagnostics, Settings, capability onboarding, and a private
loopback sidecar, distributed as manual full installers under the pre-1.0
binary-signing policy above. It excludes genealogy jobs, chat providers, cloud
accounts, updater behavior, and background release channels.

## One-time repository setup

1. Protect `main` with a ruleset that requires CI, CodeQL, signed commits, and
   every review conversation to be resolved before merge, and that prevents
   force pushes and deletion.
2. Enforce tag immutability with a ruleset for `v*.*.*`: prevent tag update and
   deletion, and restrict creation to the maintainer.
3. Create GitHub environments `desktop-prerelease`, `testpypi`, and `pypi`.
   The `desktop-prerelease` environment is used only for unsigned official
   `0.x` installer builds and contains no signing credentials. In preparation
   for releasing v1.0.0, but not for any `0.x` release, create
   `desktop-signing`, protect it with required maintainer approval, allow it
   only from the protected `main` branch, and store every
   installer-signing secret there. Only a reviewed v1.0.0-or-later manually
   dispatched pre-tag job may receive those credentials. Tag-triggered import
   and publication jobs must never receive them. For the current
   one-maintainer release, configure `sodejm` as the required reviewer for
   `pypi`; self-approval must remain permitted so the production deployment
   does not deadlock. Requiring a reviewer other than the workflow initiator is
   a future hardening step after a second maintainer exists.
4. Register pending Trusted Publishers on TestPyPI and PyPI for
   `sodejm/AncestryLLM`, workflow `release.yml`, and the matching environment.
   Keep publishing OIDC-only; no API token or token secret is a permitted
   fallback.
5. Before releasing v1.0.0, configure the release-signing Actions secrets.
   They are not required or permitted for any `0.x` release. Use
   `APPLE_CERTIFICATE_BASE64`, `APPLE_CERTIFICATE_PASSWORD`,
   `APPLE_API_KEY_BASE64`, `APPLE_API_KEY_ID`, and `APPLE_API_ISSUER` for the
   Developer ID identity and Apple notary API key;
   `WINDOWS_CERTIFICATE_BASE64` and `WINDOWS_CERTIFICATE_PASSWORD` for the
   Authenticode identity; and `LINUX_GPG_PRIVATE_KEY_BASE64` and
   `LINUX_GPG_PASSPHRASE` for the detached Debian-package signature. Configure
   repository Actions variables `APPLE_TEAM_ID`,
   `WINDOWS_SIGNING_CERTIFICATE_THUMBPRINT`,
   `LINUX_GPG_SIGNING_FINGERPRINT`, and `LINUX_GPG_PUBLIC_KEY_BASE64` with the
   approved public signer identities and Linux public key. Use the complete
   certificate thumbprint and complete Linux signing-key or signing-subkey
   fingerprint. For v1.0.0 and later, builder and validation jobs both fail
   unless the observed signer matches the approved public identity; Linux
   validation imports only the public key into a fresh keyring. Grant each
   private credential only the purpose named here, rotate it outside the
   workflow, and never put its decoded value in an artifact or repository file.
   Follow the repeatable macOS setup and verification procedure in
   [`DEPLOYMENT.md`](DEPLOYMENT.md#reconfigure-desktop-signing-from-macos);
   do not construct ad hoc upload commands containing private values.
6. Confirm the repository can use GitHub's hosted `windows-11-arm` runner.
   Desktop verification asserts Windows 11 and an ARM64 host before using
   native ARM64 Python and Node.js to build and validate the shipped Windows
   ARM64 application. The locked desktop profile contains the base runtime and
   sidecar packager only. Every third-party Python dependency must have a
   prebuilt wheel; a missing wheel fails installation rather than starting a
   source compiler or external library toolchain. Only the local AncestryLLM
   application code is built. The installer builder records ARM64 for both host
   and artifact architecture in the release receipt. No self-hosted runner
   registration or lifecycle is required.
7. Enable GitHub immutable releases.
8. Enable automatic deletion of merged pull-request branches.

These hosted controls are not created or changed by the repository workflows.
An authorized maintainer must approve and verify each one in GitHub, PyPI, and
TestPyPI before the first production release.

### Hosted control verification checklist

Before the readiness run, verify and record each control in the selected
GitHub Project 2 release evidence, with the verifier, date, and a settings-page
link or redacted screenshot:

- the `main` ruleset requires the named CI and CodeQL checks, signed commits,
  resolved review conversations, and blocks force pushes and deletion;
- the `v*.*.*` tag ruleset restricts creation and blocks update and deletion;
- the `pypi` environment has `sodejm` as the required reviewer, while
  self-approval remains enabled for the current one-maintainer release;
- for a v1.0.0-or-later release, the `desktop-signing` environment requires
  maintainer approval, is limited to the protected `main` branch, contains the
  installer-signing secrets, and is not used by any tag-triggered job; for a
  `0.x` release, `desktop-prerelease` contains no signing credentials;
- the TestPyPI and PyPI Trusted Publishers match repository
  `sodejm/AncestryLLM`, workflow `release.yml`, and their exact environments,
  and no API-token publishing secret or fallback is configured; and
- for v1.0.0 or later, the nine private release-signing secrets and four public
  signer-identity variables are configured, access-restricted, and current;
  the GitHub-hosted `windows-11-arm` validation row is required for every
  version; and
- GitHub immutable releases and automatic pull-request branch deletion are
  enabled.

Any missing or mismatched control blocks readiness. After a second maintainer is
available, separately approve and verify the change that disables initiator
self-approval; do not make that change during the one-maintainer release.

## Prepare and approve

1. Complete every P0 GitHub Issue in the exact configured GitHub Project 2
   `Release iteration`. A selected P0 issue must be closed with Project
   `Status: Done` and `Validation: Verified`. The workflow reads canonical
   Issue content by repository and issue number, rather than a Project item's
   cached display title. Every item that explicitly names the configured
   `Release iteration` must provide all four configured Project fields; legacy
   items outside that iteration may be incomplete. It paginates the Project
   item connection and follows native `blockedBy` dependencies: an open
   dependency blocks release, while a closed historical or externally tracked
   dependency outside the selected iteration is accepted. A dependency in the
   selected iteration must be `Done` and `Verified`. A truncated dependency
   response, missing Project access, malformed target-iteration field, or
   duplicate target-iteration item also blocks release. Close an item only
   after its implementation, documentation, regression tests, dead-code
   review, and required hosted checks are complete.
2. For every candidate `feature/*`, `bugfix/*`, or `hotfix/*` branch and
   worktree, first confirm a clean status with `git status --short`, then audit
   reachability and unique commits
   with `git rev-list --left-right --count main...<branch>` and
   `git log main..<branch>`. Only remove the worktree and use normal
   `git branch -d <branch>` when it is clean and its work is fully reachable
   from `main`; then confirm the remote branch was deleted. Never use `-D` to
   make this check pass. Preserve and record any dirty or active worktree,
   graph-unique commits, unmerged branch, or abnormal deletion failure for
   explicit follow-up. A squash merge can leave graph-unique branch commits
   even when its tree exactly matches `main`; record that tree comparison and
   preserve the local branch instead of treating identical content as
   reachability or forcing deletion. The lifecycle attestation confirms the
   audit and every cleanup that was safe, not deletion of preserved history.
3. Finalize the dated changelog, curated
   `docs/release-notes/<version>.md`, and versioned
   findings/interoperability records under
   `docs/release-evidence/<version>/`. Every finding needs an owner and expiry;
   every importer needs a dated evidence link, and only fictional-data manual
   imports may be marked verified.
4. Approve and merge a release-only preparation PR through the protected
   `main` ruleset, after required checks pass and conversations are resolved.
5. Run the release-configuration verifier and confirm the exact configured
   GitHub Project 2 P0 gate: every selected issue is `Done` and `Verified`, and
   each dependency in the selected iteration is closed and verified. Closed
   historical dependencies outside that iteration do not need to be added to
   the release Project.
6. Run `Release readiness` with the exact `main` commit and semantic version,
   and affirm the branch/worktree lifecycle audit input, backed by the
   documented reachability, unique-commit, cleanup, and preservation record.
7. Confirm the successful exact-head `Desktop sidecar` aggregate is for the
   same commit and contains all six unpublished native-package rows. This is
   an input to the installer gate, not release-installer evidence by itself.
8. Review the evidence artifact and confirm every required job succeeded.

Every readiness, build, and release job that executes `uv` first uses the
[verified uv bootstrap](security/verified-uv-bootstrap.md). Confirm the
`bootstrap-verification` gate in `gates.json` is `verified`, and that the
release manifest records the expected policy digest, `uv` release asset,
GitHub CLI verifier archive, source repository and commit/ref, signer workflow,
OIDC issuer, and SLSA predicate. A missing, failed, timestamp-invalid, or
identity-mismatched schema-v1 receipt blocks release. The stock-`pip` wheel and
sdist consumer smoke tests remain separate because they validate supported
installation paths; they cannot build or authorize a release.

At the exact approval points, a maintainer approves the release-preparation PR;
the readiness operator attests the cleanup audit and approves its evidence; the
maintainer approves creation and push of the annotated release tag; and `sodejm` approves
the `pypi` environment deployment. For this one-maintainer release, that final
approval may be self-approval by the workflow initiator. A separate explicit
approval is required for the GitHub-only fallback described below.

The workflow rechecks the exact configured GitHub Project 2 release gate and
refuses any incomplete selected P0 item or target-iteration dependency. Closed
historical dependencies outside that iteration are accepted. Local worktrees
are machine-specific, so their cleanup is an explicit operator attestation
recorded in the evidence bundle.

P0 is reserved for work that must complete before publication. An umbrella,
roadmap, or tracker designed to close after the release must be P1 or outside
the selected iteration; the verifier has no issue-number exception. This keeps
the P0 gate fail-closed while retaining post-release follow-up in Project 2.
The readiness workflow is the authoritative product-quality and security gate.
It records the exact commit, run URL, and complete gate inventory in
`gates.json`. The tag workflow rechecks the Project-native release gate,
requires that exact approved record, and imports the successful pre-tag
`desktop-release-distributions` artifact for the tag commit. It verifies the
GitHub Actions artifact digest plus the artifact's internal manifest and
checksums before any asset is published. It never rebuilds approved installers
after the tag is pushed and does not repeat pytest, lint, type checking,
dependency audit, or Semgrep after accepting the exact successful readiness
and installer evidence.

The tag workflow is the only installer publisher. The installers are built and
validated by a manually dispatched pre-tag run, but cannot be
published until the v0.4.0 release is complete and the v0.5.0 tag gates pass.
Before the final release distribution can be assembled or any release asset can be published, it
requires all four installer rows. The `Required native verification` column is
version-aware: `0.x` requires installation and installed-runtime execution but
does not require a trusted signature; v1.0.0 and later additionally require the
listed trusted-signing checks.

| Release row | Installer | Required native verification |
|---|---|---|
| macOS 15 arm64 | DMG | install/launch for `0.x`; at v1.0.0+, approved Apple Team ID, Developer ID signature, hardened runtime, minimal entitlements, Gatekeeper, notarization, and stapling |
| macOS 15 x64 | DMG | install/launch for `0.x`; at v1.0.0+, approved Apple Team ID, Developer ID signature, hardened runtime, minimal entitlements, Gatekeeper, notarization, and stapling |
| Windows 11 ARM64 | NSIS EXE | native build/install/launch on GitHub-hosted `windows-11-arm`; at v1.0.0+, approved certificate thumbprint and valid Authenticode signature |
| Ubuntu 24.04 x64 | DEB | install/launch on clean Ubuntu 24.04; at v1.0.0+, adjacent `.deb.asc` detached GPG signature from the approved public-key fingerprint |

Every row builds and smoke-tests the matching native sidecar, installs or
mounts the complete installer, launches the installed application with no
system Python, Node.js, or pnpm available on `PATH`, and emits an exact-head
receipt and CycloneDX SBOM. The aggregator rejects a missing row, failed gate,
wrong commit or version, duplicate asset name, malformed SBOM, symlink, or
digest mismatch. Native validation receipts also bind the canonical actual OS
derived from the host probe; aggregation and tag import require the exact six
intended-and-actual OS rows. Only after aggregation does the workflow regenerate the
complete `release-evidence.md`, create the one `SHA256SUMS` file, and attest
`dist/*`, so the evidence manifest, checksums, and provenance cover the Python
wheel and sdist together with every desktop installer, any required detached signature,
combined SBOM, desktop manifest, and exact-head evidence document.

## Tag and publish

From a clean checkout whose `HEAD` is the approved `main` commit, derive the
version-dependent release-tag mode and create an annotated tag. Pre-1.0 tags
must be unsigned; v1.0.0-and-later tags must be signed. Confirm the release
commit first with `git log --show-signature -1`, then:

```bash
release_version="$(jq -er '.release' .github/release-config.json)"
release_tag="v${release_version}"
desktop_release_run="<successful pre-tag release workflow run ID>"
desktop_release_artifact="<desktop-release-distributions artifact ID>"
desktop_release_digest="sha256:<GitHub Actions artifact SHA-256>"
release_tag_mode="$(.venv/bin/python scripts/release_signing_policy.py \
  --version "${release_version}" --tag-mode)"
.venv/bin/python scripts/verify_release_configuration.py \
  --config .github/release-config.json \
  --version "${release_version}"
tag_args=(
  -m "AncestryLLM ${release_version}"
  -m "Desktop-Release-Run-ID: ${desktop_release_run}"
  -m "Desktop-Release-Artifact-ID: ${desktop_release_artifact}"
  -m "Desktop-Release-Artifact-Digest: ${desktop_release_digest}"
)
if [[ "${release_tag_mode}" == "unsigned-annotated" ]]; then
  git tag --no-sign -a "${tag_args[@]}" "${release_tag}" HEAD
else
  git tag -s "${tag_args[@]}" "${release_tag}" HEAD
  git tag -v "${release_tag}"
fi
git push origin "${release_tag}"
```

Push only the release tag. The tag-triggered workflow verifies the required
annotated-tag mode, exact readiness evidence, and exact pre-tag installer artifact;
the three `Desktop-Release-*` tag-message fields are mandatory and bind the
approval to one successful manual release-workflow run, one artifact ID, and
GitHub's exact SHA-256 artifact digest. Obtain them from that run's summary and
independently confirm them in the Actions artifact metadata before tagging.
The summary normalizes the upload-artifact output to the required
`sha256:<64 lowercase hex>` form; do not remove the `sha256:` prefix.
Release construction installs the locked `build` group and the `security`
group needed for SBOM generation, with no provider extras. Stock-`pip` wheel
and source-distribution smoke jobs remain unchanged because they validate the
published consumer experience rather than authorize a build.
Setuptools remains the production backend. The locked `uv_build` candidate and
`make evaluate-uv-build` exist only for the fail-closed 0.6 comparison recorded
in the [uv_build evaluation](UV_BUILD_EVALUATION.md); its incompatible result
does not authorize a backend change or weaken any release check.
Release construction uses SHA-pinned `actions/setup-python` with Python 3.12,
then the verified repository contract requires exactly `uv` 0.12.1, selects
only that system interpreter, and disables Python downloads. The workflow calls
the same `make package` and `make sbom` interfaces used locally after its narrow
locked synchronization.
The workflow then attests the combined artifacts; prepares a draft GitHub
Release; publishes to TestPyPI with `attestations: false` because TestPyPI does
not provide PyPI's PEP 740 Integrity API; it verifies only the exact TestPyPI
artifact hashes; and pauses for required production approval. Production PyPI
publishing explicitly requests `attestations: true`. The workflow then verifies
the PEP 740 provenance for both the wheel and source distribution, including
exact repository, workflow, environment, filename, and SHA-256 identity, with
the pinned `pypi-attestations==0.0.30` verifier. It preserves the provenance and
verifier output as evidence and fails closed. The workflow installs this tool
from the locked, non-default `release-verifier` dependency group so ordinary
release construction does not inherit its platform-specific build
dependencies. Full local `make setup` intentionally synchronizes all groups;
the production verification job remains isolated to `release-verifier` alone.
After production PyPI publishing, the supported platform/Python wheel-and-sdist
install smoke matrix runs before
the immutable GitHub Release. The attached `SHA256SUMS` covers
every release asset except the checksum file itself. No other workflow or
manual upload may publish an installer.

### Documentation publication gate

The release workflow enforces documentation publication for the exact release
commit before the immutable GitHub Release is published. The
`verify-docs-publication` job runs as a required predecessor to
`publish-github-release` and performs two checks:

**GitHub Pages (always required)**
The `Deploy documentation site` (`jekyll-gh-pages.yml`) workflow must have a
successful completed run for the release commit SHA. This workflow runs
automatically on every push to `main`; if the release commit landed on `main`
and Pages deployment succeeded, the gate passes automatically. If the gate
fails, confirm that `jekyll-gh-pages.yml` completed successfully on `main` for
that commit, wait for any in-progress deployment to finish, and re-run the
release workflow.

**Wiki synchronization (required when `docs/**` changed)**
If the release commit modified any file under `docs/`, the `Sync Wiki`
(`sync-wiki.yml`) workflow must also have a successful completed run for that
commit. If `docs/` was not changed in the release commit, Wiki sync is treated
as not required and the gate passes without checking it. If the gate fails
because Wiki sync is missing or failed, confirm that `sync-wiki.yml` completed
successfully on `main` for that commit and re-run the release workflow.

Both checks use exact commit SHA matching and query only `status=success` runs;
a workflow that is in progress or failed does not satisfy the gate.

### Verify a downloaded installer

Download the target-matched full installer and the release's `SHA256SUMS` from
the same immutable GitHub Release. Verify the checksum before opening the
installer. For a `0.x` release, require `binarySigningMode: "unsigned"` and
`releaseTagMode: "unsigned-annotated"`, and expect an operating-system
unknown-publisher or equivalent prompt; do not infer a trusted identity from
an unsigned binary. For v1.0.0 and
later, inspect the Developer ID signature and notarization/stapling on macOS,
require a valid Authenticode signature on Windows, and verify Ubuntu's adjacent
`.deb.asc` with the documented release key. Do not install when a required
identity, digest, or signature check fails.

### Manual upgrade and rollback

Quit AncestryLLM, download and verify the new target-matched full installer,
then install it over the existing application and relaunch. The installer
replaces application files but retains the OS-managed AncestryLLM data and
configuration directories. Confirm the displayed version and healthy
Diagnostics after relaunch. Recovery or rollback uses the same process with a
previous full installer whose checksum and version-required signature still verify.

v0.5.0 has no updater feed, no background update, no staged rollout, and no
automatic rollback. Do not publish `latest*.yml`, blockmaps, or another update
channel, and do not represent manual reinstall behavior as an updater.

## Failure and recovery

Retry only transient failed jobs. Never make an upload idempotent by silently
skipping or overwriting an existing file.

If initial draft creation stops after a partial asset upload, verify that the
release is still a draft and that the tag is unchanged, delete only that
unpublished draft, and rerun the workflow. Draft reuse is accepted only when
its title, body, complete asset inventory, checksum manifest, and every asset
hash already match the workflow build exactly.

If PyPI remains unavailable after an approved retry, stop the failed workflow.
Only when the maintainer explicitly approves a GitHub-only release may the
draft be published, and only when the failure is external to the product and
artifacts, every non-index release gate passed, and the prepared draft still
contains the exact workflow-built assets. Download the `release-distributions`
artifact from that run and the draft assets into separate directories, then
require:

```bash
python scripts/verify_release_assets.py \
  --expected /path/to/release-distributions \
  --actual /path/to/draft-download
```

Before publishing the immutable draft, edit its body to state
`PyPI: unavailable`, link the failed workflow run, and remove every PyPI or
TestPyPI verification claim. Record that explicit approval in the tracking
issue. Do not use an API-token fallback, upload replacement files, or claim
that the GitHub-only release is available from a Python package index.

If a product or artifact defect makes a published version unusable, preserve
its tag and release evidence, yank the PyPI version when appropriate, document
the reason, and publish the next patch version. Never force-push or recreate a
release tag. Historical 0.2.0 notes and evidence remain immutable when preparing
later releases.
