# GEDCOM compatibility and release checks

Public commands are `ancestry gedcom merge`, `subtree`, `quality`, and
`sync update|rebase`. RootsMagic export supports portable and preservation
profiles, connected/ancestor/descendant scopes, optional generation limits, and
GEDCOM 5.5.5 with a deliberate 5.5.1 fallback.

The internal characterization CLI still recognizes `--quality-report`,
`--no-quality-report`, `--quality-root-person`, and `--quality-ai`; application
callers should use the service and unified command options documented by
`ancestry --help`.

The merge engine preserves custom/vendor structures, citations, conflicting
facts, family links, and stable pointers whenever representable. Optional LLM
adjudication may identify likely duplicates but cannot delete conflicting
evidence. Incremental sync never automatically deletes people, relationships,
cited facts, protected baseline/manual content, families, or sources.

The safe offline fixture demo uses `quality-source-a.ged`,
`quality-source-b.ged`, and root `Maren Hollow`:

```bash
scripts/gedcom_merge_quickstart.sh --skip-install
```

Before claiming a release interoperable, record automated validation and manual
imports into current Ancestry, Geni, and MyHeritage products for both 5.5.5 and
any advertised 5.5.1 fallback. Verify root selection, people/family counts,
citations, names, dates, living-person behavior, and custom-tag loss reports.
