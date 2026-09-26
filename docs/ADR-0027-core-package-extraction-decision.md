# ADR-0027: GEDCOM and RootsMagic standalone package extraction decision

- Status: Accepted
- Date: 2026-09-24
- Decision owner: AncestryLLM maintainer
- Relates to: #155, #159, #160, #131, #162, #170, #132, ADR-0025, `ARCHITECTURE.md`

## Context and decision boundary

Issue #170 asked whether GEDCOM and/or RootsMagic should move from the
package-shaped monolith into separately released Python distributions. Keep a
candidate core internal unless extraction demonstrates an independent consumer
or materially reduces dependency and release surface. Evaluate GEDCOM and
RootsMagic independently. RootsMagic application orchestration, grants,
provider behavior, and publication remain outside any reusable core.

## Evidence summary

- **Consumers and façades:** Current consumers are repository-internal adapters,
  scripts, and tests; no verified external consumer requires either core as a
  standalone distribution. The fixed CORE-11 characterization manifest covers
  10 public façades, 51 semantic test nodes, and 13 fictional fixtures.
- **Dependency and import boundaries:** Architecture contracts enforce
  dependency direction and private-boundary imports, retaining exactly two
  legacy characterization import exceptions.
- **Provisional performance observations:** The CORE-11/#160 method and fixed
  fictional corpus remain authoritative. Existing CLI/offline-merge samples
  and peak-RSS observations have not been independently reproduced, and the
  standard capture did not complete. These observations do not satisfy the
  reproducible `capture` gate and are not acceptance evidence until reproduced
  through that gate.
- **Desktop parity:** #131 remains the packaged desktop parity gate; this
  decision does not change desktop support scope or adapter ownership.
- **Release and security ownership:** A separate package would add SemVer and
  changelog ownership, release/signing/attestation workflows, vulnerability
  response ownership, dependency maintenance, and cross-repository coordination
  without a demonstrated independent-consumer benefit.

## Decision

### GEDCOM

**Decision: keep internal now (defer extraction).** GEDCOM remains the first
extraction candidate if an independent consumer appears and measured evidence
shows a material reduction in dependency and release surface that offsets the
added operational burden.

### RootsMagic

**Decision: keep internal (decline extraction).** The reusable boundary is
already represented by internal façades without a demonstrated independent
consumer. A separate release train would increase release and vulnerability-
response surface without a compensating measured benefit.

## Inherited safety and behavior invariants

- GEDCOM 5.5.5 behavior remains loss-minimal and deterministic.
- RootsMagic sources remain immutable across success, failure, timeout, and
  cancellation paths.
- `provider=none` remains network-free even when credentials exist.
- File ingress and publication remain bounded, path-safe, opaque-reference
  based, and cancellation-safe.
- No package is published by this story, and #132 remains unblocked.

## Consequences and revisit triggers

Revisit extraction for a candidate core only when all of the following are
true:

1. A maintained independent consumer outside this repository uses its façade.
2. A packaging prototype demonstrates a material dependency/release-surface
   reduction.
3. Security ownership, SemVer/changelog, CI, provenance, and vulnerability
   response plans are approved for the additional release train.
4. Reproduced performance and parity evidence remains within existing
   regression thresholds while preserving the invariants above.

Until then, the package-shaped monolith remains the accepted architecture.
