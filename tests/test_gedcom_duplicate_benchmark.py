"""Opt-in structural benchmarks for fictional duplicate candidate data.

Run with ``ANCESTRYLLM_RUN_DUPLICATE_BENCHMARKS=1 pytest -s``.  Normal CI
collects and skips these cases.  Wall time and peak RSS are reported, never
asserted, while deterministic candidate/adjudication budgets remain enforced.
"""

from __future__ import annotations

import json
import os
import resource
import sys
import time
from collections import Counter

import pytest

from ancestryllm.gedcom import engine as gm

RUN_BENCHMARKS = os.getenv("ANCESTRYLLM_RUN_DUPLICATE_BENCHMARKS") == "1"


def _fictional_people(count: int) -> list[gm.IndividualRecord]:
    return [
        gm.IndividualRecord(
            pointer=f"@F{index}@",
            given_name="Jordan",
            surname=f"Sample{index // 2}",
            birth_date=str(1800 + (index // 2) % 180),
            death_date=str(1860 + (index // 2) % 160),
            gender="M" if (index // 2) % 2 else "F",
            source_file="/fictional/a.ged" if index % 2 == 0 else "/fictional/b.ged",
        )
        for index in range(count)
    ]


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def _adjudication_counts(
    records: list[gm.IndividualRecord],
    candidates: list[tuple[int, int, float]],
    limits: gm.DuplicateSearchLimits,
) -> tuple[int, int]:
    """Return potential and budget-scheduled prompt counts without model calls."""
    potential = 0
    scheduled = 0
    per_person: Counter[int] = Counter()
    for left, right, _score in candidates:
        assessment = gm.assess_similarity(records[left], records[right])
        hard_conflict = any(
            conflict in gm.DETERMINISTIC_HARD_CONFLICTS for conflict in assessment.conflicts
        )
        if hard_conflict or assessment.automatic_merge_safe:
            continue
        potential += 1
        if (
            scheduled >= limits.max_adjudications
            or per_person[left] >= limits.max_adjudications_per_person
            or per_person[right] >= limits.max_adjudications_per_person
        ):
            continue
        per_person[left] += 1
        per_person[right] += 1
        scheduled += 1
    return potential, scheduled


@pytest.mark.skipif(not RUN_BENCHMARKS, reason="opt-in duplicate benchmark")
@pytest.mark.parametrize("record_count", [1_000, 10_000, 100_000])
def test_bounded_duplicate_benchmark(record_count: int) -> None:
    records = _fictional_people(record_count)
    limits = gm.DuplicateSearchLimits()
    started = time.perf_counter()
    plan = gm.estimate_duplicate_search(records, limits)
    candidates = gm.find_duplicate_candidates(records, limits=limits)
    wall_seconds = time.perf_counter() - started
    candidate_pairs = {(left, right) for left, right, _score in candidates}
    known_duplicate_count = record_count // 2
    found_known_duplicates = sum(
        (index, index + 1) in candidate_pairs for index in range(0, record_count, 2)
    )
    known_duplicate_recall = found_known_duplicates / known_duplicate_count
    potential_adjudication_count, adjudication_count = _adjudication_counts(
        records,
        candidates,
        limits,
    )
    metrics = {
        "record_count": record_count,
        "wall_seconds": round(wall_seconds, 6),
        "peak_rss_bytes": _peak_rss_bytes(),
        "candidate_count": len(candidates),
        "known_duplicate_count": known_duplicate_count,
        "known_duplicate_recall": known_duplicate_recall,
        "potential_adjudication_count": potential_adjudication_count,
        "adjudication_count": adjudication_count,
        "plan": plan.to_dict(),
    }
    print(json.dumps(metrics, sort_keys=True))

    assert len(candidates) <= limits.max_candidates
    assert known_duplicate_recall == 1.0
    assert adjudication_count <= limits.max_adjudications
    assert plan.scored_pair_upper_bound <= limits.max_scored_pairs
