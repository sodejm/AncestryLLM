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

## Staged synchronization and recovery

The pure synchronization kernel accepts only immutable document metadata and
adapter-issued opaque references. Outer adapters capture and validate host
inputs before invoking the kernel; GEDCOM records, user content, file paths,
credentials, provider payloads, and publication mechanics do not cross this
boundary. The ordered stages are snapshot, comparison, planning, explicit
decision, unpublished application, atomic commit, and recovery.

A normalized plan is deterministic and content-addressed. It carries
provenance references, explicit update, deletion, tombstone, conflict, and
rebase actions, plus a coded payload-free loss report. Decision requests use
stable IDs and coded options. Selected decisions can be serialized and replayed
through the shared application `DecisionPort` without prompting again.

Cancellation checkpoints occur only before atomic commit. A cancellation or
failure before publication invokes recovery with the failed stage, a stable
error code, and opaque state, plan, and staging references. Recovery reports
whether the prior immutable revision was preserved and unpublished state was
removed; it does not log document content or host paths. A commit adapter must
validate the publication result before crossing the atomic boundary and must
not raise afterward. Failure to render structural progress after commit cannot
turn an already published revision into a failed result.

The current CLI and typed sync service entry points remain compatibility shims
to the characterized incremental synchronizer. Issue #166 owns the concrete
stage adapters, publication extraction, and obsolete compatibility-path
removal. Until that migration is complete, the #160 offline baseline protects
initialization, idempotency, rebase, tombstone non-resurrection, rollback
metadata, and failed-publication preservation.

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

## RootsMagic export contract

RootsMagic export feature-detects supported table and column aliases. Optional
name, event, place, note, source, citation, media, and family tables may be
absent; malformed required person structure is rejected. Canonical semantic
keys and stable source identities determine GEDCOM pointers and record order,
so SQLite row order, cycles, pedigree collapse, repeated relationships, and
repeated runs do not change the bytes. Duplicate and conflicting representable
values remain separate. Null values and blobs are not serialized. The loss
report identifies unsupported or unsafe data only by table, column, count, and
reason; it never includes the discarded values.

The `portable` profile emits only safely representable standard GEDCOM. The
`preservation` profile also emits attributable, privacy-safe `_RM_*`
extensions. Living-person `exclude` and `redact` modes are fail closed: records
owned by a living person, and shared records whose safe field-level separation
cannot be proved, are omitted and counted rather than leaked through families,
notes, citations, sources, media, custom fields, reports, CLI output, or JSON.

GEDCOM 5.5.5 output receives strict validation. GEDCOM 5.5.1 output receives
the common structural checks plus a limited compatibility rule set; this
fallback does not establish full 5.5.1 semantics for every extension. All
destination selections are standards-based formatting targets. Automated
destination tests demonstrate format compatibility only, not a successful
import into a current vendor product. Vendor interoperability may be claimed
only from dated fictional-data evidence.

The GEDCOM and Markdown loss report form one staged publication transaction.
Source identity is revalidated before and during publication, and failure
restores the prior complete pair or leaves the new complete pair—never a
partial pair. The RootsMagic input remains immutable. Existing SQLite `-wal`,
and matching `-shm` sidecars when present, are accepted only when they can be
fingerprinted, copied descriptor-relative, fully checkpointed, and consolidated
inside a process-owned directory. SQLite reconstructs a missing SHM only in
owned staging. Busy, malformed, replaced, symbolic-link, or non-regular
sidecars are rejected. Rollback `-journal` files and `-shm` files without a WAL
are always rejected. The source database and sidecars are never opened by
SQLite and are revalidated throughout.

Natural-language RootsMagic queries serialize the inspected schema and question
as JSON data beneath a separate fixed system policy. The combined UTF-8 payload
is bounded by `file_ingress.prompt_body.max_bytes` before any provider call.
An oversized payload fails locally with
`ROOTSMAGIC_SCHEMA_PROMPT_TOO_LARGE` and exit code `2`, without including schema
values or the question in error details. `provider=none` never attempts a
network request, even when provider credentials or SDKs are present.
