# Wiki synchronization operations and recovery

The Markdown files under `docs/` are the canonical source for the AncestryLLM
GitHub Wiki. The `Sync Wiki` workflow publishes that source to the separate
`sodejm/AncestryLLM.wiki.git` repository. Direct edits to managed Wiki pages are
temporary: the next successful synchronization replaces or removes them to
match `docs/`.

For the managed-file rules and local synchronization command, see the
[wiki synchronization design](WIKI_SYNC.md).

## Automatic publication

A push to `main` that changes a path under `docs/` starts the workflow
automatically. The normal publication path is therefore:

1. Make documentation changes on a dedicated branch.
2. Run the validation commands described below.
3. Merge the reviewed pull request into `main`.
4. Verify the resulting workflow run and Wiki commit before closing the issue.

Changes only to the workflow or synchronization scripts do not match the
`docs/**` path filter. Use a manual dispatch after those changes reach `main`.

## Manual dispatch

Use the Actions page, select **Sync Wiki**, select **Run workflow**, and choose
the `main` branch. The equivalent GitHub CLI command is:

```console
gh workflow run sync-wiki.yml --repo sodejm/AncestryLLM --ref main
```

Always dispatch from `main` for an operational publication. A branch dispatch
tests that branch's workflow and documentation, which can publish content that
has not been reviewed or merged.

Find and watch the newest manual run:

```console
run_id="$(gh run list \
  --repo sodejm/AncestryLLM \
  --workflow sync-wiki.yml \
  --branch main \
  --event workflow_dispatch \
  --limit 1 \
  --json databaseId \
  --jq '.[0].databaseId')"
gh run watch "$run_id" --repo sodejm/AncestryLLM --compact --exit-status
```

## Verify a publication

Record the source SHA, workflow run URL, and resulting Wiki commit SHA in the
issue or release evidence. Do not copy credentials or complete raw logs into an
issue.

1. Resolve the current `main` SHA and its matching run:

   ```console
   source_sha="$(gh api repos/sodejm/AncestryLLM/commits/main --jq .sha)"
   run_id="$(gh run list \
     --repo sodejm/AncestryLLM \
     --workflow sync-wiki.yml \
     --branch main \
     --commit "$source_sha" \
     --limit 1 \
     --json databaseId \
     --jq '.[0].databaseId')"
   ```

2. Confirm the selected run completed successfully at exactly that SHA:

   ```console
   gh run view "$run_id" \
     --repo sodejm/AncestryLLM \
     --json conclusion,event,headSha,jobs,url
   ```

3. Clone the Wiki without embedding credentials in its URL, inspect the newest
   commit, and rerun the deterministic mirror locally:

   ```console
   verification_root="$(mktemp -d)"
   wiki_checkout="$verification_root/AncestryLLM.wiki"
   git clone --depth=2 \
     https://github.com/sodejm/AncestryLLM.wiki.git \
     "$wiki_checkout"
   git -C "$wiki_checkout" show --no-patch \
     --format='%H%n%an <%ae>%n%s' HEAD
   python scripts/sync_wiki_docs.py \
     --source docs \
     --destination "$wiki_checkout"
   git -C "$wiki_checkout" status --porcelain
   ```

   The commit author must be `github-actions[bot]`, its subject must be
   `docs: synchronize from <source_sha>`, the synchronizer must report that the
   destination is already synchronized, and `git status --porcelain` must print
   nothing.

4. Open the [published Wiki](https://github.com/sodejm/AncestryLLM/wiki), follow
   the sidebar link to each changed page, and confirm headings, internal links,
   and formatting render correctly. Repository-source links retain `.md`; the
   published Wiki links are intentionally extensionless.

For a deletion check, inspect the new Wiki commit with
`git -C "$wiki_checkout" show --name-status HEAD` and confirm that the removed
page is reported with status `D`. Also confirm that
`git -C "$wiki_checkout" ls-tree -r --name-only HEAD` no longer lists it.

## Verify a no-op run

Capture the Wiki branch tip, manually dispatch unchanged `main`, wait for a
successful run, and compare the tip again:

```console
before="$(git ls-remote \
  https://github.com/sodejm/AncestryLLM.wiki.git \
  refs/heads/master | awk '{print $1}')"
gh workflow run sync-wiki.yml --repo sodejm/AncestryLLM --ref main
# List manual runs, then copy the databaseId from the newly created row.
gh run list --repo sodejm/AncestryLLM --workflow sync-wiki.yml \
  --branch main --event workflow_dispatch --limit 5 \
  --json databaseId,createdAt,status,headSha,url
run_id="<new-databaseId>"
gh run watch "$run_id" --repo sodejm/AncestryLLM --compact --exit-status
after="$(git ls-remote \
  https://github.com/sodejm/AncestryLLM.wiki.git \
  refs/heads/master | awk '{print $1}')"
test "$before" = "$after"
```

The run must succeed, its commit step must report that there are no changes,
and `before` must equal `after`. A different tip means the run was not a no-op
or another writer changed the Wiki during verification; inspect the Wiki log
before drawing a conclusion.

## Authentication and permission failures

Typical symptoms are `Repository not found` during the Wiki clone or HTTP 403
during the push.

1. Confirm the Wiki feature is enabled:

   ```console
   gh api repos/sodejm/AncestryLLM --jq '{has_wiki,visibility}'
   ```

2. Confirm the Wiki repository is initialized and has the expected branch:

   ```console
   git ls-remote \
     https://github.com/sodejm/AncestryLLM.wiki.git \
     refs/heads/master
   ```

3. In the failed run, confirm the job received `Contents: write` under
   **GITHUB_TOKEN Permissions**. The workflow declares only `contents: write`;
   repository, organization, or enterprise Actions policy can still restrict
   that permission.
4. Confirm Actions are enabled for the repository and that the workflow is
   active. For a policy restriction, have a repository administrator restore
   the required job-token write permission, then rerun the failed job or
   dispatch `main` again.
5. Never place a token in a clone URL, command transcript, issue, or artifact.
   Do not replace the job-scoped token with a long-lived personal token merely
   to bypass a policy failure.

If `gh auth status` fails locally, repair the maintainer's GitHub CLI session
before using the diagnostic commands. That local login is separate from the
job-scoped token used by Actions.

## Concurrency and non-fast-forward failures

The workflow concurrency group serializes `Sync Wiki` runs and does not cancel
an in-progress publication. A second run can remain queued; allow the earlier
run to finish, then verify the later run used the intended `main` SHA.

```console
gh run list \
  --repo sodejm/AncestryLLM \
  --workflow sync-wiki.yml \
  --status queued \
  --json databaseId,event,headSha,status,url
```

An external writer can still update `master` after the workflow clones it and
before it pushes. That push correctly fails as non-fast-forward. Do not force
push the Wiki. Wait for any active publisher to finish, inspect the intervening
commit, and dispatch `main` again. The new run clones the latest Wiki tip,
reapplies canonical `docs/`, and pushes a descendant commit.

If non-fast-forward failures repeat, pause publication and identify the writer.
Move any legitimate managed-page edit into `docs/` through a pull request;
otherwise the synchronizer will intentionally replace it. Preserve and review
any non-Markdown or nested Wiki content, which is outside the managed scope.

## Roll back published documentation

Rollback happens in the source repository, not by rewriting Wiki history.

1. Create a dedicated branch from current `origin/main`.
2. Revert the documentation-only source commit, or restore the affected files
   under `docs/` to their last known-good content.
3. Run `python scripts/validate_wiki_docs.py --source docs` and the relevant
   documentation tests.
4. Open and merge the rollback pull request.
5. Verify the automatic run and new Wiki commit using the procedure above.

Do not force push `AncestryLLM.wiki.git` or revert only its bot commit. Either
action separates the published Wiki from canonical `docs/`, and the next
successful synchronization will restore the canonical source anyway.

## Reinitialize an empty Wiki

The authenticated clone requires an initialized Wiki repository. If the Wiki
has no `master` ref:

1. In repository **Settings > General > Features**, enable **Wikis**.
2. Open the repository's **Wiki** tab, create the first page with the title
   `Home`, add a short non-sensitive placeholder, and save it. GitHub creates
   the Wiki repository and `master` branch.
3. Verify initialization:

   ```console
   git ls-remote \
     https://github.com/sodejm/AncestryLLM.wiki.git \
     refs/heads/master
   ```

4. Dispatch `sync-wiki.yml` from `main` and wait for success.
5. Verify the bot commit, exact source SHA, synchronized content, navigation,
   and clean local mirror as described above.

Do not seed the Wiki with genealogy records, credentials, logs, or other
sensitive content. For a damaged but still initialized Wiki, preserve its Git
history and use a fresh dispatch rather than deleting or force-recreating the
repository.
