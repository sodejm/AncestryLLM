# Temporary Wiki synchronization E2E probe

> **Temporary verification page:** this page exists only for the first live
> end-to-end verification phase of GitHub issue #69. A follow-up documentation
> pull request will delete it to prove that stale Wiki pages are removed.

The marker below gives maintainers a harmless, exact value to compare between
the canonical documentation and the published Wiki:

`ancestryllm-wiki-sync-issue-69-phase-1`

## Expected lifecycle

1. Merging the documentation-only pull request publishes this page and its
   sidebar entry automatically.
2. Maintainers verify the workflow run, bot attribution, source SHA, page
   content, and navigation using the
   [Wiki operations runbook](WIKI_OPERATIONS.md).
3. A follow-up documentation-only pull request deletes this file and removes
   its sidebar entry.
4. Maintainers verify that the next Wiki commit records the page deletion and
   that a subsequent unchanged manual dispatch creates no duplicate commit.

This probe contains no credentials, genealogy records, logs, or other sensitive
data. It is not permanent product documentation.
