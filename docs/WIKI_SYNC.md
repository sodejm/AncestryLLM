# Wiki synchronization

The Markdown files under `docs/` are the canonical source for the project
documentation. The [GitHub Pages site](https://sodejm.github.io/AncestryLLM/)
and `Sync Wiki` workflows publish two views of that source. Neither published
target is an independent source of truth.

Both publishers build one case-safe index of every canonical file before they
write a destination. Relative page, image, and asset links are resolved from
the linking source page. Missing files, directory escapes, malformed encoding,
absolute local paths, case-only filename collisions, and duplicate flattened
Wiki page names fail validation before a Wiki checkout can be mutated or a
Pages artifact can be uploaded. Query strings, fragments, optional Markdown
titles, and percent-encoded path components are retained.

The Pages workflow first creates an isolated Jekyll-ready staging tree. Nested
source directories remain nested, local page links become `.html` routes, and
asset paths remain relative to the linking page. The staging tree adds the
source commit and page metadata used by Jekyll; it never changes canonical
Markdown:

```console
python scripts/prepare_pages_source.py \
  --source docs \
  --destination /path/to/pages-source \
  --source-sha "$(git rev-parse HEAD)"
```

The `Sync Wiki` workflow validates the canonical source and runs the same local
command used by maintainers:

```console
python scripts/sync_wiki_docs.py --source docs --destination /path/to/wiki-checkout
```

## GitHub Wiki managed scope

Every regular `*.md` file below the source directory is copied to the top level
of the wiki checkout using its basename. For example, `docs/guides/CLI.md` maps
to `CLI.md`. Validation rejects symlinks and duplicate basenames before the
destination changes, so this flattening is deterministic.

Repository-relative Markdown page links retain their `.md` suffix in the
canonical source. The synchronizer resolves them before flattening, then maps
them to the unique extensionless Wiki page name so GitHub routes navigation
through the Wiki UI instead of serving raw Markdown. Local image and asset
links are normalized to canonical source-root paths because the Wiki pages are
flat. Every referenced local asset is copied to that path in the Wiki checkout.
The synchronizer records only those copied paths in
`.ancestryllm-managed-assets.json`; on a later run it removes assets that
disappeared from the canonical links while preserving unrecorded Wiki files.
Unsafe, malformed, or symlinked manifest paths fail before destination content
changes. External links and code examples remain unchanged; queries, anchors,
and Markdown link titles are preserved.

`validate_wiki_docs.py` also requires one nonempty, unique title and description
in `_data/page_metadata.json` for every public Markdown page. Pages renders
those fields as title, description, canonical, and Open Graph metadata. The
generated-site validator checks that metadata, every rendered internal route
and anchor, `robots.txt`, `sitemap.xml`, and the exact source-commit marker
before upload. The deployment job checks representative production routes and
the same marker after deployment.

External HTTP links are deliberately excluded from pull-request jobs. A
scheduled or manually dispatched trusted workflow deduplicates them, rejects
private or special-use destinations and unsafe redirects, rate-limits each
host, and checks them with bounded concurrency and retries:

```console
python scripts/check_external_doc_links.py \
  --source docs \
  --exceptions docs/_data/external_link_exceptions.json
```

An exception must identify the exact URL, an accountable `owner`, a nonempty
`reason`, and an ISO `expires` date. Expired, duplicate, unsafe, or stale
exceptions fail the workflow.

All top-level `*.md` files in the wiki checkout are managed. A managed page is
removed when there is no source page with the same name. Referenced local
assets and their hidden manifest are also managed. The `.git` directory and
all unrecorded non-Markdown paths are outside the managed scope and remain
untouched.

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
