# GEDCOM Incremental Synchronization Handoff

Paused on 2026-07-16 on branch `copilot/create-gedcom-merge-tool`.

## Status

This is a work-in-progress checkpoint, not a production-ready incremental
release. Existing basic merge and quality-report work is also uncommitted work
from the preceding implementation task and must be preserved.

Checkpoint checks that pass:

```text
python3 -m py_compile tools/gedcom_incremental.py tools/gedcom_merge.py
git diff --check
```

The full pytest suite has not been run in this desktop session. The bundled
Python environment did not include pytest, and an earlier dependency install
attempt was blocked by a revoked desktop authentication refresh. Do not claim
completion until the complete offline suite passes.

## Implemented in the current draft

- `tools/gedcom_incremental.py` defines draft `update` and `rebase` workflows.
- Stable snapshot source IDs and separate vendor metadata are parsed.
- Schema-v1 manifest creation/loading and master SHA-256 checks exist.
- Release staging targets versioned `gNNNN-TIMESTAMP/` bundles.
- Deterministic person matching uses prior bindings, retained identifiers,
  identity fingerprints, and conflict-free similarity evidence.
- New master-controlled xrefs use deterministic `@M_...@` sequences.
- Person facts are canonicalized independently of citations.
- Compatible citations and richer `DATA/TEXT` children are unioned; conflicting
  singleton citation values remain separate.
- Exact duplicate level-zero records and matching family records are drafted
  for consolidation and pointer remapping.
- Snapshot-origin observations stay in the private manifest and are not turned
  into GEDCOM `SOUR` citations.
- Conservative omission removal and protected baseline/manual origins are
  drafted.
- Confirmed manual-deletion tombstones are drafted to prevent later silent
  reintroduction.
- Plain-English `SyncError` messages include stable codes, what happened, why
  it matters, how to fix it, and the no-release-changed guarantee.
- Incremental AI defaults to `none`; provider use is opt-in and reuses existing
  provider credit preflights.
- Five fictional incremental GEDCOM fixtures were added under
  `tests/fixtures/gedcom_incremental/` and passed the existing 5.5.5 parser and
  structural validator when created.
- `tools/README.md`, `tools/GEDCOM_MERGE_QUICKSTART.md`, and the root README now
  contain a draft recurring-update workflow.

## Critical work remaining

1. Wire `update` and `rebase` dispatch into `tools/gedcom_merge.py::main` before
   the legacy argument parser runs. The draft module is not currently reachable
   from the public command shown in the documentation.
2. Add and run `tests/test_gedcom_incremental.py`. A test subagent was started
   but deliberately stopped when the user requested a pause; it produced no
   patch.
3. Run end-to-end fixture generations and inspect emitted GEDCOM, manifest,
   reports, rollback metadata, and idempotency.
4. Review `_map_nonpeople` and `_reconcile_person_blocks` for pointer-order and
   provenance edge cases. Verify master duplicates, family-event attachment
   unions, source/repository aliases, custom records, notes, and objects.
5. Validate rebase manifests as strictly as update manifests, verify the prior
   master checksum before comparison, and account for manual changes/deletions
   to families and other non-person records—not only individual blocks.
6. Confirm manual tombstones cannot resurrect a deleted block from any active
   or replacement snapshot and document how to reverse a tombstone.
7. Finish manifest artifact and parent checksums. Decide whether/how to record
   the manifest's own checksum without creating a circular self-hash.
8. Test malformed GEDCOM diagnostics and redact free-form record contents while
   retaining file and line context. Print tracebacks only under `--verbose`.
9. Correct documentation/CLI mismatches. In particular, current rebase examples
   show `--quality-root-person`, which the draft rebase parser does not accept,
   and omit required `--reason`.
10. Add all incremental provider/model flags to the authoritative CLI reference
    or simplify the incremental provider interface consistently.
11. Verify no HTTP/SDK path is invoked with `--ai-backend none`, even when API
    keys are present. Mock Ollama/OpenAI/Gemini/OpenRouter tests must remain
    network-free.
12. Run formatting/lint, `bash -n scripts/gedcom_merge_quickstart.sh`, documented
    smoke commands, existing GEDCOM tests, and the full repository suite.

## Subagent status

- Fixture agent `019f6dd8-1493-7ab0-9280-ee153afb161b`: completed and closed.
  Added only `tests/fixtures/gedcom_incremental/**`.
- Documentation agent `019f6dd8-15b0-77e2-9191-557ac4202e26`: completed and
  closed. Updated only the root README and the two tool-specific guides.
- Incremental test agent `019f6de3-09cc-73d1-ba78-49eec5d92717`: stopped and
  closed before producing changes.
- Read-only safety reviewer `019f6de3-0ad7-7c83-bef3-5ee889836cc1`: stopped and
  closed before producing a report.
- No subagents remain active.

## Suggested resume sequence

```bash
cd /Users/justinsoderberg/Development/AncestryLLM
git status --short
python3 -m py_compile tools/gedcom_incremental.py tools/gedcom_merge.py
git diff --check
```

Then wire public CLI dispatch, create the focused incremental test module, and
run the first initialization fixture with `--ai-backend none` and
`--no-quality-report` into a temporary release directory. Resolve failures
before expanding rebase or remote-provider tests.

## Resume prompt

> Resume the GEDCOM incremental synchronization implementation in
> `/Users/justinsoderberg/Development/AncestryLLM` on branch
> `copilot/create-gedcom-merge-tool`. Read
> `GEDCOM_INCREMENTAL_HANDOFF_2026-07-16.md` first. Preserve all existing dirty
> work. Continue the approved Offline Incremental GEDCOM Synchronization plan,
> beginning with public CLI dispatch and focused offline end-to-end tests. Treat
> `tools/gedcom_incremental.py` as an unverified draft, fix the documented
> critical gaps, keep `--ai-backend none` strictly network-free, run the full
> available test suite, and do not claim completion until bundle atomicity,
> provenance, citation/source consolidation, snapshot replacement, rebase,
> rollback, idempotency, and plain-English errors are verified.
