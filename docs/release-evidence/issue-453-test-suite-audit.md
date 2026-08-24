# v0.7.0 test-suite consolidation audit

This source-level maintenance record closes the audit and consolidation work
tracked by issue #453. It records the review method, selected consolidation,
retained test structures, and equivalent before-and-after evidence. It is not
exact-candidate release-readiness evidence and does not replace the v0.7.0
release gates.

## Scope and method

The audit ran on macOS arm64 with Python 3.14.7 on 2026-08-24. It combined:

- a full-suite baseline with coverage and per-test durations;
- an abstract-syntax-tree comparison of normalized test bodies;
- a broader structural-clone comparison with constants normalized; and
- manual review of slow tests and every repeated structure reported by those
  comparisons.

The full measurement used the locked test environment and the equivalent of:

```console
.venv/bin/pytest -q --cov --cov-report=json --cov-report= \
  --durations=100 --durations-min=0.05
```

It ran outside the managed development sandbox because existing tests exercise
loopback sidecar sockets and isolated Git-signing fixtures. No network provider,
real genealogy data, or credential was used.

## Findings and disposition

The exact-body comparison reported one two-function group in the job-progress
tests. The functions deliberately retain separate parameter matrices for
invalid state combinations versus ambiguous types and bounded-input failures.
They are not duplicate cases and remain separate so a failure identifies the
violated contract family.

The structural comparison reported 33 groups containing 125 test functions.
Manual review produced these dispositions:

| Area | Disposition | Rationale |
|---|---|---|
| Documentation-policy path classification | Consolidated | Thirty-six cases used the same single assertion. A named case table now preserves every path, expected class, and diagnostic identity while removing wrapper repetition. |
| GEDCOM date normalization | Retained | Similar assertion shapes cover distinct GEDCOM formats, qualifiers, invalid-date preservation, and privacy-safe logging. Their domain-specific names are useful compatibility diagnostics. |
| Job progress validation | Retained | The two shapes enforce different invalidity taxonomies and type contracts. |
| Documentation pass/fail fixtures | Retained | Repeated harness calls exercise distinct policy diagnostics and fixture mutations; merging them would obscure the failure contract. |
| Slow process and toolchain tests | Retained | Swift typechecking, clean installation, wrong-sidecar rejection, lazy provider loading, and isolated CLI import each cross a distinct process or toolchain boundary. |

No actionable candidate was deferred. Therefore no follow-up issue is needed;
the unselected structures above are reviewed non-candidates, not silently
dropped work.

## Before-and-after evidence

| Measure | Before | After | Result |
|---|---:|---:|---|
| Collected tests | 2,897 | 2,897 | unchanged |
| Passed | 2,888 | 2,888 | unchanged |
| Skipped | 9 | 9 | unchanged |
| Full-suite runtime | 76.49 s | 76.88 s | +0.39 s (0.5%, normal run-to-run noise) |
| Total coverage | 84.14916644514487% | 84.14916644514487% | unchanged |
| Covered / total lines | 17,512 / 20,105 | 17,512 / 20,105 | unchanged |
| Covered / total branches | 4,647 / 6,228 | 4,647 / 6,228 | unchanged |
| Classification test-file lines | 905 | 871 | 34 fewer lines |

The focused documentation-policy suite also remained at 119 passing cases.
The coverage JSON totals were structurally equal before and after, including
missing lines, excluded lines, partial branches, and missing branches.

## Test-development and change impact

This is a test-only structural refactor. A new failing behavior test does not
apply because production behavior, inputs, outputs, and assertions are
unchanged. The required confidence signal is the green pre-change baseline,
the preserved case inventory, and the matching green post-change measurement.

The change does not alter runtime architecture, trust boundaries, data flow,
GEDCOM behavior, user/API/operational surfaces, or the threat model. This audit
record is the only documentation surface required by the maintenance change.
