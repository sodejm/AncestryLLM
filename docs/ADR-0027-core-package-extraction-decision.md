# ADR-0027: Keep GEDCOM and RootsMagic package extraction internal for 0.6

- Status: Accepted
- Date: 2026-09-24
- Decision owner: Core extraction decision story #170 under #159
- Extends: [ADR-0025](ADR-0025-electron-fastapi-desktop.md)
- Related issues: #155, #159, #160, #131, #170

## Context and decision boundary

Issue #170 asked whether GEDCOM and/or RootsMagic should move from the
package-shaped monolith into separately released Python distributions. The
accepted decision rule was to keep each core internal unless extraction shows an
independent consumer or a material reduction in dependency and release surface.

This decision does not publish any new package and does not block #132.

## Evidence summary

### Consumers and façade stability

Current consumers are repository-internal adapters: one-shot CLI, prompt-toolkit
REPL, authenticated FastAPI control adapter, and the bounded Electron shell over
application services. No independent external consumer currently requires either
GEDCOM or RootsMagic as a standalone distribution.

The fixed characterization manifest keeps 10 supported façade modules and 51
semantic test nodes over 13 fictional fixtures (`tests/characterization/core_contracts_0_3_baseline.json`).
Architecture contracts continue to enforce dependency direction and private
boundary imports (`reference/ARCHITECTURE_CONTRACTS.md`).

### Dependency and import evidence

`PYTHONPATH=src python scripts/characterize_core_contracts.py verify`
confirmed the fixed CORE-11 inventory (13 fixtures, 5 semantic groups, 51 test
nodes, 10 public façades). The current dependency snapshot digest is:

- `ca42369af4bacf89066cb79295bb52f4a50795bf3ddd88eb188e9b3c88dbb239`

The runtime dependency list remains shared at the monolith boundary (FastAPI,
Pydantic, SQLAlchemy, SQLCipher, prompt-toolkit/Rich, and related support
libraries), so extracting either core now would add release/process burden
without evidence of meaningful dependency-surface reduction.

### Concise benchmark report (fictional corpus)

Method:

1. Use the fixed CORE-11 manifest policy (7 runs, 60 warm merge iterations).
2. Measure with `scripts.characterize_core_contracts.performance_snapshot(...)`
   on fictional fixtures only.
3. Treat medians and peak RSS as the comparison signal.

Environment:

- Repository source tree at this decision commit
- Python 3.12 system runtime
- Local-only execution with `provider=none`

Measured medians (7 runs):

| Operation | Median elapsed | Median peak RSS | Notes |
| --- | ---: | ---: | --- |
| CLI cold start (`python -m ancestryllm --version`) | 659.221 ms | 70,176,768 bytes | Gate remains max(100 ms, 10%) regression from baseline |
| Offline GEDCOM merge (60 warm iterations) | 2,041.434 ms | 50,241,536 bytes | Deterministic digest `27be1afa27ad157ac3db0445521e31e918a6d8e099251e07980bd46e964b6640`, people read/written 6/4 |

No result in this decision indicates a package-extraction performance win.

### Security and operational ownership

Desktop parity and control-surface evidence remains in #131 and
`DESKTOP_VERIFICATION.md`. Existing release, security response, and provenance
workflows are already owned for this repository and would need duplication for
any extracted package release train.

The decision explicitly preserves existing invariants: loss-minimal deterministic
GEDCOM handling, immutable RootsMagic sources, network-free `provider=none`,
bounded ingress, opaque artifacts, and cancellation-safe publication.

## Decision

### GEDCOM

Keep GEDCOM internal for now. It remains the first extraction candidate only if
an independent consumer appears and measured evidence shows a material
release/dependency-surface reduction that offsets added release/security burden.

### RootsMagic

Keep RootsMagic internal. Read/query/mapping kernels remain reusable inside the
repository, but publication, provider orchestration, grants, and application
contracts make a separate release train unjustified at this stage.

## Consequences and revisit triggers

No packaging epic is created from #170. Revisit extraction only when all of the
following are true for a candidate core:

1. A confirmed independent consumer exists outside this repository.
2. Import/dependency evidence shows a material release-surface reduction.
3. Security ownership, SemVer/changelog, CI, provenance, and vulnerability
   response plans are approved for the additional release train.
4. CORE-11/#131-style semantic and parity evidence remains at or within existing
   regression thresholds.

Until those triggers are met, the package-shaped monolith remains the accepted
architecture.
