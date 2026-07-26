# Release runbook

Only a clean, reviewed commit on `main` may become a release. Production
publishing uses GitHub Actions OIDC Trusted Publishing; API tokens and local
uploads are prohibited.

## One-time repository setup

1. Protect `main` with required CI, CodeQL, signed commits, resolved
   conversations, and force-push/deletion prevention.
2. Protect tags matching `v*.*.*` from update or deletion and restrict their
   creation to the maintainer.
3. Create GitHub environments `testpypi` and `pypi`. Require maintainer approval
   for `pypi`.
4. Register pending Trusted Publishers on TestPyPI and PyPI for
   `sodejm/AncestryLLM`, workflow `release.yml`, and the matching environment.
5. Enable immutable GitHub Releases.
6. Enable automatic deletion of merged pull-request branches.

## Prepare and approve

1. Complete every issue and pull request in the release milestone without
   bypassing checks. Close an item only after its implementation,
   documentation, regression tests, dead-code review, and required hosted checks
   are complete.
2. For every merged release branch, verify it has no commits outside `main`.
   Remove its attached worktree, delete the local branch with `git branch -d`,
   and confirm the remote branch was deleted. Never use `-D` to make this check
   pass. Preserve unrelated, dirty, active, or unmerged branches and worktrees.
3. Finalize the dated changelog, curated
   `docs/release-notes/<version>.md`, and versioned
   findings/interoperability records under
   `docs/release-evidence/<version>/`. Every finding needs an owner and expiry;
   every importer needs a dated evidence link, and only fictional-data manual
   imports may be marked verified.
4. Merge a release-only preparation PR.
5. Confirm the `0.2.0 CLI` milestone has no open issues or pull requests.
6. Run `Release readiness` with the exact `main` commit and semantic version,
   and affirm the branch/worktree cleanup input.
7. Review the evidence artifact and confirm every required job succeeded.

The workflow rechecks milestone closure and refuses to approve a candidate while
any milestone item remains open. Local worktrees are machine-specific, so their
cleanup is an explicit operator attestation recorded in the evidence bundle.
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
git tag -s v0.2.0 HEAD -m "AncestryLLM 0.2.0"
git tag -v v0.2.0
git push origin v0.2.0
```

Push only the release tag. The tag-triggered workflow verifies the signed,
annotated tag; rebuilds and attests the artifacts; prepares a draft GitHub
Release; publishes and verifies TestPyPI; pauses for production approval;
publishes and verifies PyPI across the supported platform/Python matrix; and
only then publishes the immutable GitHub Release. The attached `SHA256SUMS`
covers every release asset except the checksum file itself.

## Failure and recovery

Retry only transient failed jobs. Never make an upload idempotent by silently
skipping or overwriting an existing file.

If initial draft creation stops after a partial asset upload, verify that the
release is still a draft and that the tag is unchanged, delete only that
unpublished draft, and rerun the workflow. Draft reuse is accepted only when
its title, body, complete asset inventory, checksum manifest, and every asset
hash already match the workflow build exactly.

If PyPI remains unavailable after an approved retry, stop the failed workflow.
The maintainer must explicitly approve any GitHub-only publication and may do
so only when the failure is external to the product and artifacts, every
non-index release gate passed, and the prepared draft still contains the exact
workflow-built assets. Download the `release-distributions` artifact from that
run and the draft assets into separate directories, then require:

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

If a product or artifact defect makes `0.2.0` unusable, preserve the tag and
release evidence, yank the PyPI version when appropriate, document the reason,
and publish the correction as `0.2.1`. Never force-push or recreate a release
tag.
