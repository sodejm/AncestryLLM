# GEDCOM compatibility and release checks

The supported GEDCOM and RootsMagic command syntax is maintained in
[the CLI reference](CLI.md#rootsmagic-and-gedcom). RootsMagic export supports portable and preservation
profiles, connected/ancestor/descendant scopes, optional generation limits, and
GEDCOM 5.5.5 with a deliberate 5.5.1 fallback.

The supported merge command recognizes `--quality-report`, `--root-person`,
`--provider`, `--model`, and `--consent` as documented by `ancestry gedcom merge
--help`. The fixture quickstart uses this public modular command with
`--provider none`; it does not invoke the internal characterization CLI.
Identity adjudication, incremental matching, and quality refinement use the
unified application service. `--provider PROFILE` uses the profile's model,
endpoint, execution limits, shared client, and exact-cache policy; direct
built-in selection requires `--model`.

The merge engine preserves custom/vendor structures, citations, conflicting
facts, notes, media, family links, and stable pointers whenever representable.
Incremental synchronization retains the greatest exact duplicate-citation
multiplicity supplied by any one origin while consolidating only identical
cross-origin repetitions. Persisted bindings and canonical semantic ordering,
not input-path, argument, or record order, control new pointer allocation.
Independent origins remain independent unless a persisted binding explicitly
associates them. Optional LLM adjudication may identify likely duplicates but
cannot delete conflicting evidence. Incremental sync never automatically
deletes people, relationships, cited facts, protected baseline/manual content,
families, or sources.

Automatic reconciliation may remove only source-owned, uncited, unprotected
fact blocks. Snapshot imports cannot revive tombstones. A changed fact matching
a tombstoned logical identity is a conflict, while an explicit manual rebase
that restores matching content retires that tombstone. Manual-deletion
acceptance applies only to the reviewed rebase where it is supplied. Manifest
schema, history, lineage, active-source continuity, and master/artifact
fingerprints are validated before publication begins. An invalid history fails
closed, and atomic publication leaves either the prior complete release or the
new complete release with usable rollback metadata—never a partial bundle.
Optional provider output reaches the deterministic engine only through narrow
resolver contracts. A timeout, cancellation, consent denial, malformed
response, or provider failure aborts the operation with a stable coded error;
it cannot publish a partial synchronization bundle or weaken preservation
rules. Identity confidence must be finite and between zero and one. An
automatic merge accepts a provider duplicate decision only at or above the
conservative confidence floor; lower-confidence pairs remain separate.

## Adversarial input dispositions

The fictional adversarial corpus in
`tests/fixtures/gedcom_adversarial/manifest.json` records the expected result at
each pure-engine boundary. "Accepted with findings" means preserve the source
evidence and report the anomaly; it never authorizes a silent repair or a
guessed relationship.

| Input or evidence class | Disposition | Deterministic behavior and ownership |
| --- | --- | --- |
| Well-formed 5.5.5 and 5.5.1 input | Accepted | Parse loss-minimally. Output uses the explicitly requested supported version. |
| UTF-8, UTF-8 BOM, UTF-16 LE/BE with BOM, LF, and CRLF | Accepted and normalized on output | Decode strictly, then emit UTF-8 with LF newlines. A UTF-16 stream without a BOM is rejected rather than guessed. |
| Unicode text, `CONC`/`CONT`, alternative names/facts, empty optional values, and unknown or nested vendor tags | Accepted | Preserve representable logical content and unknown structures. Long output values are split on UTF-8 character boundaries into lines of at most 255 bytes. |
| Colliding xref definitions across sources | Accepted and normalized | Allocate a collision-free pointer per source and rewrite only exact xref fields. Xref-looking text in notes or custom text remains literal. |
| Malformed dates, dangling references, empty `INDI`/`FAM` records, broken family edges, and cycles | Accepted with findings | Preserve evidence without inventing data. Pure-validator acceptance and deterministic findings are covered by the issue #77 adversarial matrix. |
| Deep, valid family graphs | Accepted | The parser and validator do not impose a shallow genealogy-depth limit. |
| Invalid bytes, malformed GEDCOM grammar, invalid levels/xrefs, or a required wrap on a level-99 line | Rejected | Fail deterministically at decode, line parse, validator, or wrapping; do not partially serialize. |
| Missing, misplaced, or duplicate `HEAD`/`TRLR`; duplicate level-zero xref definitions | Rejected at the public service boundary | Reject the ambiguous source before provider execution or publication. Other internal analysis paths can preserve malformed structures long enough to produce advisory diagnostics. |
| Unsupported 5.5.5 output header/version/charset; lowercase or overlong tags; skipped levels; or overlong emitted lines | Rejected before publication | The strict output validator rejects the generated document. |
| NUL policy, physical ingress limits, disallowed file types, path/symlink races, non-regular files, and failure-artifact cleanup | Enforced by the shared file-ingress policy | These are service/ingress safety rules rather than pure GEDCOM semantics; issue #77 verifies their observable GEDCOM behavior without duplicating the underlying policy implementation. |

Characterization tests establish deterministic local behavior, not compatibility
with a third-party importer.

The safe offline fixture demo uses the structurally valid fictional fixtures
`xref-source-a.ged`, `xref-source-b.ged`, and root `Aster Fiction`:

```bash
scripts/gedcom_merge_quickstart.sh --skip-install
```

Both CLI invocations isolate their non-secret configuration and data
directories beneath the timestamped run directory. The demo does not create or
modify the caller's normal AncestryLLM or XDG configuration/data directories.

Automated validation does not establish product interoperability. Before
claiming a release interoperable, complete the dated vendor evidence matrix in
[`release-evidence/issue-10-import-smoke-tests.md`](release-evidence/issue-10-import-smoke-tests.md)
for current Ancestry, Geni, and MyHeritage products. The matrix covers both
5.5.5 and any advertised 5.5.1 fallback and must identify each vendor result as
verified, failed, unavailable, or unverified. Verify root selection,
people/family counts, citations, names, dates, living-person behavior, and
custom-tag loss reports using fictional fixtures only. A blank or pending row
is an evidence gap, not a passing interoperability result.
