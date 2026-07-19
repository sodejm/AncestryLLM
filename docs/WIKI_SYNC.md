# Wiki synchronization

The Markdown files under `docs/` are the canonical source for the project wiki.
The `Sync Wiki` workflow validates them and runs the same local command used by
maintainers:

```console
python scripts/sync_wiki_docs.py --source docs --destination /path/to/wiki-checkout
```

## Managed scope

Every regular `*.md` file below the source directory is copied to the top level
of the wiki checkout using its basename. For example, `docs/guides/CLI.md` maps
to `CLI.md`. Validation rejects symlinks and duplicate basenames before the
destination changes, so this flattening is deterministic.

Repository-relative Markdown page links retain their `.md` suffix in the
canonical source. The synchronizer removes that suffix from local page targets
in the mirrored content so GitHub routes navigation through the Wiki UI instead
of serving raw Markdown. External links, images, anchors, and code examples are
left unchanged.

All top-level `*.md` files in the wiki checkout are managed. A managed page is
removed when there is no source page with the same name. The `.git` directory,
nested destination directories, and non-Markdown paths are outside the managed
scope and remain untouched.

The synchronizer writes only pages whose bytes differ. Repeating the command
with unchanged documentation therefore leaves the wiki checkout with an empty
Git diff.

## Bot commit and push

The workflow passes the synchronized checkout to `commit_wiki_changes.py`. The
script stages the complete wiki worktree, exits successfully without a commit
when the staged diff is empty, and commits additions, modifications, and
deletions with the standard `github-actions[bot]` author and committer identity.
Its commit message includes the source repository SHA as
`docs: synchronize from <source-sha>`.

The commit step exposes only a `committed` workflow output. The separate push
step runs only when that value is `true`. The job-scoped token is limited to the
authenticated clone and push steps; it is never written to the wiki checkout or
passed to the local commit script.
