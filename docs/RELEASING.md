# Release runbook

Only a clean, reviewed commit on `main` may become a release. Production
publishing uses GitHub Actions OIDC Trusted Publishing; API tokens and local
uploads are prohibited.

Repository release coordinates are defined in
`.github/release-config.json`. Its stable package version, exact milestone
number and title, and exact tracker number and label are one reviewed release
control. `scripts/verify_release_configuration.py` requires that configuration
to match `pyproject.toml`; readiness and publication use the configured values
instead of inferring a milestone or tracker from a version string.

## One-time repository setup

1. Protect `main` with a ruleset that requires CI, CodeQL, signed commits, and
   every review conversation to be resolved before merge, and that prevents
   force pushes and deletion.
2. Enforce tag immutability with a ruleset for `v*.*.*`: prevent tag update and
   deletion, and restrict creation to the maintainer.
3. Create GitHub environments `testpypi` and `pypi`. For the current
   one-maintainer release, configure `sodejm` as the required reviewer for
   `pypi`; self-approval must remain permitted so the production deployment
   does not deadlock. Requiring a reviewer other than the workflow initiator is
   a future hardening step after a second maintainer exists.
4. Register pending Trusted Publishers on TestPyPI and PyPI for
   `sodejm/AncestryLLM`, workflow `release.yml`, and the matching environment.
   Keep publishing OIDC-only; no API token or token secret is a permitted
   fallback.
5. Enable GitHub immutable releases.
6. Enable automatic deletion of merged pull-request branches.

These hosted controls are not created or changed by the repository workflows.
An authorized maintainer must approve and verify each one in GitHub, PyPI, and
TestPyPI before the first production release.

### Hosted control verification checklist

Before the readiness run, verify and record each control in the tracker issue
named by `.github/release-config.json`, with the verifier, date, and a
settings-page link or redacted screenshot:

- the `main` ruleset requires the named CI and CodeQL checks, signed commits,
  resolved review conversations, and blocks force pushes and deletion;
- the `v*.*.*` tag ruleset restricts creation and blocks update and deletion;
- the `pypi` environment has `sodejm` as the required reviewer, while
  self-approval remains enabled for the current one-maintainer release;
- the TestPyPI and PyPI Trusted Publishers match repository
  `sodejm/AncestryLLM`, workflow `release.yml`, and their exact environments,
  and no API-token publishing secret or fallback is configured; and
- GitHub immutable releases and automatic pull-request branch deletion are
  enabled.

Any missing or mismatched control blocks readiness. After a second maintainer is
available, separately approve and verify the change that disables initiator
self-approval; do not make that change during the one-maintainer release.

## Prepare and approve

1. Complete every issue and pull request in the exact configured release
   milestone without bypassing checks, except for exactly one open,
   non-pull-request issue: the configured tracker, carrying the configured
   `release-tracker` label. Any other open issue or pull request, another issue
   with that label, a missing label on the configured tracker, or multiple
   tracker exceptions blocks readiness and release. Both workflows paginate the
   full milestone result before applying this rule. Close an item only after
   its implementation, documentation, regression tests, dead-code review, and
   required hosted checks are complete.
2. For every candidate release branch and worktree, first confirm a clean
   status with `git status --short`, then audit reachability and unique commits
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
   milestone has no open issues or pull requests except the single configured
   `release-tracker` issue.
6. Run `Release readiness` with the exact `main` commit and semantic version,
   and affirm the branch/worktree lifecycle audit input, backed by the
   documented reachability, unique-commit, cleanup, and preservation record.
7. Review the evidence artifact and confirm every required job succeeded.

At the exact approval points, a maintainer approves the release-preparation PR;
the readiness operator attests the cleanup audit and approves its evidence; the
maintainer approves creation and push of the signed tag; and `sodejm` approves
the `pypi` environment deployment. For this one-maintainer release, that final
approval may be self-approval by the workflow initiator. A separate explicit
approval is required for the GitHub-only fallback described below.

The workflow rechecks the exact configured milestone and tracker, permits only
that `release-tracker` exception, and refuses every other open item. Local
worktrees are machine-specific, so their cleanup is an explicit operator
attestation recorded in the evidence bundle.
The readiness workflow records the exact commit, run URL, and complete gate
inventory in `gates.json`. The tag workflow rechecks milestone closure, requires
that exact approved record, and compares the release distribution hashes with
the readiness build before any artifact is published.

## Tag and publish

Configure Git tag signing before creating the release tag. From a clean checkout
whose `HEAD` is the approved `main` commit. When using SSH signatures, also
configure `gpg.ssh.allowedSignersFile` with the maintainer identity and the
registered signing public key so local verification is meaningful. Confirm the
release commit first with `git log --show-signature -1`, then:

```bash
release_version="$(jq -er '.release' .github/release-config.json)"
release_tag="v${release_version}"
.venv/bin/python scripts/verify_release_configuration.py \
  --config .github/release-config.json \
  --version "${release_version}"
git tag -s "${release_tag}" HEAD -m "AncestryLLM ${release_version}"
git tag -v "${release_tag}"
git push origin "${release_tag}"
```

Push only the release tag. The tag-triggered workflow verifies the signed,
annotated tag; rebuilds and attests the artifacts; prepares a draft GitHub
Release; publishes to TestPyPI with `attestations: false` because TestPyPI does
not provide PyPI's PEP 740 Integrity API; it verifies only the exact TestPyPI
artifact hashes; and pauses for required production approval. Production PyPI
publishing explicitly requests `attestations: true`. The workflow then verifies
the PEP 740 provenance for both the wheel and source distribution, including
exact repository, workflow, environment, filename, and SHA-256 identity, with
the pinned `pypi-attestations==0.0.30` verifier. It preserves the provenance and
verifier output as evidence and fails closed. After production PyPI publishing,
the supported platform/Python wheel-and-sdist install smoke matrix runs before
the immutable GitHub Release. The attached `SHA256SUMS` covers
every release asset except the checksum file itself.

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
