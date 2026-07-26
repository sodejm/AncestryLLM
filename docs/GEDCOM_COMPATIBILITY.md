# GEDCOM compatibility and release checks

The supported GEDCOM and RootsMagic command syntax is maintained in
[the CLI reference](CLI.md#rootsmagic-and-gedcom). RootsMagic export supports portable and preservation
profiles, connected/ancestor/descendant scopes, optional generation limits, and
GEDCOM 5.5.5 with a deliberate 5.5.1 fallback.

The internal characterization CLI recognizes `--quality-report`,
`--no-quality-report`, `--quality-root-person`, and the compatibility spelling
`--ai-backend none`. It is deliberately offline-only. Identity adjudication,
incremental matching, and quality refinement use the unified application
service and explicit `--provider`, `--model`, and `--consent` options documented
by `ancestry --help`. `--provider PROFILE` uses the profile's model, endpoint,
execution limits, shared client, and exact-cache policy; direct built-in
selection requires `--model`.

The merge engine preserves custom/vendor structures, citations, conflicting
facts, family links, and stable pointers whenever representable. Optional LLM
adjudication may identify likely duplicates but cannot delete conflicting
evidence. Incremental sync never automatically deletes people, relationships,
cited facts, protected baseline/manual content, families, or sources.
Optional provider output reaches the deterministic engine only through narrow
resolver contracts. A timeout, cancellation, consent denial, malformed
response, or provider failure aborts the operation with a stable coded error;
it cannot publish a partial synchronization bundle or weaken preservation
rules. Identity confidence must be finite and between zero and one. An
automatic merge accepts a provider duplicate decision only at or above the
conservative confidence floor; lower-confidence pairs remain separate.

The safe offline fixture demo uses `quality-source-a.ged`,
`quality-source-b.ged`, and root `Maren Hollow`:

```bash
scripts/gedcom_merge_quickstart.sh --skip-install
```

Automated validation does not establish product interoperability. Before
claiming a release interoperable, complete the dated vendor evidence matrix in
[`release-evidence/issue-10-import-smoke-tests.md`](release-evidence/issue-10-import-smoke-tests.md)
for current Ancestry, Geni, and MyHeritage products. The matrix covers both
5.5.5 and any advertised 5.5.1 fallback and must identify each vendor result as
verified, failed, unavailable, or unverified. Verify root selection,
people/family counts, citations, names, dates, living-person behavior, and
custom-tag loss reports using fictional fixtures only. A blank or pending row
is an evidence gap, not a passing interoperability result.
