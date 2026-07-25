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
3. Finalize the dated changelog and release-evidence disposition.
4. Merge a release-only preparation PR.
5. Confirm the `0.2.0 CLI` milestone has no open issues or pull requests.
6. Run `Release readiness` with the exact `main` commit and semantic version,
   and affirm the branch/worktree cleanup input.
7. Review the evidence artifact and confirm every required job succeeded.

The workflow rechecks milestone closure and refuses to approve a candidate while
any milestone item remains open. Local worktrees are machine-specific, so their
cleanup is an explicit operator attestation recorded in the evidence bundle.
The tag workflow rechecks milestone closure before any artifact is built or
published.

## Tag and publish

Configure Git tag signing before creating the release tag. From a clean checkout
whose `HEAD` is the approved `main` commit:

```bash
git tag -s v0.2.0 HEAD -m "AncestryLLM 0.2.0"
git tag -v v0.2.0
git push origin v0.2.0
```

Push only the release tag. The tag-triggered workflow verifies the signed,
annotated tag; rebuilds and attests the artifacts; prepares a draft GitHub
Release; publishes and verifies TestPyPI; pauses for production approval;
publishes and verifies PyPI across the supported platform/Python matrix; and
only then publishes the immutable GitHub Release.

## Failure and recovery

Retry only transient failed jobs. Never make an upload idempotent by silently
skipping or overwriting an existing file.

If a product or artifact defect makes `0.2.0` unusable, preserve the tag and
release evidence, yank the PyPI version when appropriate, document the reason,
and publish the correction as `0.2.1`. Never force-push or recreate a release
tag.
