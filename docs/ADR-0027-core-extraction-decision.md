# ADR-0027: GEDCOM and RootsMagic standalone package extraction decision

- Status: Accepted
- Date: 2026-09-24
- Decision owner: AncestryLLM maintainer
- Relates to: #159, #160, #131, #162, #170, #132, ADR-0025, `ARCHITECTURE.md`

## Context

This ADR records the CORE-42 decision rule outcome for two candidate cores:
GEDCOM and RootsMagic.

Decision rule:

1. Keep a core internal unless extraction provides an independent consumer or
   materially reduces the dependency/release surface.
2. Evaluate GEDCOM and RootsMagic independently.
3. RootsMagic application orchestration, grants, provider behavior, and
   publication remain outside any reusable core.

## Evidence summary

- **Independent consumers today:** repository-local adapters, scripts, and
  tests only; no verified external consumer is recorded.
- **Dependency/import and façade evidence:** architecture contracts and #162
  inventory show migration from broad internal-kernel access to enforced public
  façades, with exactly two retained legacy characterization import exceptions.
- **Performance evidence:** CORE-11/#160 characterization method and fixed
  fictional corpus remain authoritative; updated CLI/offline merge medians and
  peak RSS are recorded in
  [`release-evidence/issue-170-core-extraction-benchmark-and-dependency-report.md`](release-evidence/issue-170-core-extraction-benchmark-and-dependency-report.md).
- **Desktop parity evidence:** #131 remains the packaged desktop parity gate;
  this decision does not shift desktop support scope or adapter ownership.
- **Release/security ownership cost:** extraction would add another SemVer and
  changelog stream, release/signing/attestation workflows, security response
  ownership, dependency maintenance, and cross-repository coordination without
  a measured independent-consumer payoff.

## Decision

### GEDCOM

**Decision: keep internal now (defer extraction).**

Rationale:

- No current independent consumer is evidenced.
- Current package-level dependency and release burden is not materially reduced
  by immediate extraction.
- Existing façade and contract boundaries already provide stable internal reuse.

Revisit triggers (all measurable):

1. A maintained external consumer (outside this repository) uses the GEDCOM
   façade for at least two consecutive releases.
2. A packaging prototype demonstrates a material reduction in dependency/release
   surface versus internal delivery (including security response and CI burden).
3. Benchmark and parity evidence remains within the existing thresholds while
   preserving the inherited safety guarantees listed below.

GEDCOM remains the first extraction candidate if those triggers are met.

### RootsMagic

**Decision: keep internal (decline extraction).**

Rationale:

- Current behavior is tightly coupled to immutable `.rmtree` handling, bounded
  ingress, private staging/publication coordination, and provider-none/offline
  policy in application orchestration.
- The reusable boundary is already represented by internal façades without a
  demonstrated independent consumer.
- Separate packaging would increase release and vulnerability-response surface
  without a compensating measured benefit.

RootsMagic application orchestration, grants, provider behavior, and
publication are permanently outside any reusable core package.

## Inherited safety and behavior invariants preserved by this decision

- GEDCOM 5.5.5 behavior remains loss-minimal and deterministic.
- RootsMagic sources remain immutable and guarded across success, failure,
  timeout, and cancellation paths.
- `provider=none` remains network-free even when credentials exist.
- File ingress and publication remain bounded, path-safe, opaque-reference
  based, and cancellation-safe.
- No package is published by this story, and #132 remains unblocked.

