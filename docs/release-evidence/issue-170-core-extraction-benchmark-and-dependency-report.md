# Issue #170: CORE-42 extraction benchmark and dependency report

This report records the measured and architecture evidence used by
ADR-0027 for GEDCOM and RootsMagic standalone-package extraction decisions.

## Inputs and method

- **Fixed corpus and semantic scope:** CORE-11/#160 characterization manifest
  (`tests/characterization/core_contracts_0_3_baseline.json`) and
  `scripts/characterize_core_contracts.py verify`.
- **Desktop parity source:** Issue #131 references in
  `docs/DESKTOP_VERIFICATION.md`.
- **Architecture/import boundary source:** #162 records in
  `docs/reference/ARCHITECTURE_CONTRACTS.md` and
  `scripts/check_architecture_contracts.py`.
- **Command environment:** Linux CI workspace checkout at
  `/home/runner/work/AncestryLLM/AncestryLLM`, Python 3.12 virtual environment,
  fictional fixtures only.

Executed commands:

```bash
PYTHONPATH=src .venv/bin/python scripts/characterize_core_contracts.py verify
PYTHONPATH=src .venv/bin/python scripts/check_architecture_contracts.py
```

The full `capture` command could not complete in this environment because the
clean-install contract test requires repository-local verified `uv`
(`.tools/uv/uv`) installed by `make setup`, and `make setup` is blocked here by
GitHub attestation authentication. To preserve the same harness operation logic,
this report measured the built-in `_measure` operation directly with seven warm
runs for each required operation.

## Measurement policy and results

Policy source (#160 baseline): seven runs; warm-operation and RSS regression
budget 10%; CLI cold-start budget max(100 ms, 10%).

### Raw seven-run measurements

- **CLI cold start** (`_measure cli-cold-start --iterations 1`)
  - elapsed_ms samples: 611.055, 610.419, 610.123, 606.237, 609.040, 608.555, 605.742
  - median elapsed_ms: **609.040**
  - peak_rss_bytes samples: 68,759,552; 68,956,160; 68,861,952; 68,968,448; 69,144,576; 69,042,176; 68,796,416
  - median peak_rss_bytes: **68,956,160**
- **Offline GEDCOM merge representative warm operation** (`_measure offline-gedcom-merge --iterations 60`)
  - elapsed_ms samples: 1849.420, 1891.548, 1886.379, 1884.321, 1927.976, 1934.337, 1928.434
  - median elapsed_ms: **1891.548**
  - peak_rss_bytes samples: 49,795,072; 50,118,656; 49,909,760; 50,229,248; 50,298,880; 50,352,128; 50,368,512
  - median peak_rss_bytes: **50,229,248**

Both measured operations are >= 500 ms and satisfy the report requirement for
seven warm-run medians plus peak RSS and CLI cold start.

## Dependency/import and façade stability evidence

- #162 baseline recorded eleven test modules importing `gedcom.engine` or
  `gedcom.incremental` directly at the CORE-24 baseline.
- Current architecture-contract evidence records **exactly two** explicit
  compatibility import exceptions, both in one characterization test that
  verifies legacy re-exports.
- `scripts/check_architecture_contracts.py` passes with:
  - 0 dependency exceptions
  - 2 characterization import exceptions (both exact and live)
- This confirms façade tightening and bounded legacy compatibility without new
  consumer-facing package boundaries.

## Consumer and release-burden analysis

- Observed consumers are repository-internal (CLI, REPL, FastAPI control
  adapter, desktop shell integration, scripts, and tests); no verified external
  consumer is recorded for either candidate core.
- Optional provider dependencies are already segregated in project optional
  dependency groups; standalone extraction does not independently prove a
  material reduction in dependency/release surface.
- Separate package extraction would add ongoing burden for each extracted core:
  SemVer/changelog ownership, release/signing/attestation pipeline,
  vulnerability response ownership, dependency-update cadence, cross-repository
  CI coordination, and compatibility planning.

## Security and invariants checked for the decision

- GEDCOM behavior remains loss-minimal and deterministic by existing contract
  and adversarial coverage.
- RootsMagic source immutability remains required by service/export contracts.
- `provider=none` remains network-free.
- Bounded ingress, opaque artifacts, and cancellation-safe publication remain
  required boundaries.
- No package publication is performed under this issue.

