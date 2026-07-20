"""Focused regression tests for bounded GEDCOM duplicate planning."""

from __future__ import annotations

import json
from collections import Counter
from unittest.mock import patch

from ancestryllm.gedcom import engine as gm


def _person(
    index: int,
    *,
    source: str,
    given: str = "Jordan",
    surname: str | None = None,
    birth: str = "1900",
    death: str = "1980",
    gender: str = "M",
    raw_lines: list[str] | None = None,
) -> gm.IndividualRecord:
    return gm.IndividualRecord(
        pointer=f"@I{index}@",
        given_name=given,
        surname=surname or f"Sample{index}",
        birth_date=birth,
        death_date=death,
        gender=gender,
        source_file=source,
        raw_lines=raw_lines or [],
    )


def _small_limits(**overrides: int) -> gm.DuplicateSearchLimits:
    values = {
        "max_bucket_size": 8,
        "max_pairs_per_person": 3,
        "max_scored_pairs": 40,
        "max_candidates": 20,
        "max_adjudications_per_person": 2,
        "max_adjudications": 5,
    }
    values.update(overrides)
    return gm.DuplicateSearchLimits(**values)


def test_oversized_initial_bucket_uses_refined_keys_and_stays_bounded() -> None:
    records = [
        _person(
            index,
            source="/a.ged" if index % 2 == 0 else "/b.ged",
            death=str(1700 + index),
        )
        for index in range(80)
    ]
    records.extend(
        (
            _person(1000, source="/a.ged", surname="Smyth", death="1888"),
            _person(1001, source="/b.ged", surname="Smith", death="1888"),
        )
    )
    limits = _small_limits()

    first = gm.find_duplicate_candidates(records, threshold=60, limits=limits)
    second = gm.find_duplicate_candidates(records, threshold=60, limits=limits)

    assert first == second
    assert any({left, right} == {80, 81} for left, right, _score in first)
    assert len(first) <= limits.max_candidates


def test_candidate_generation_caps_both_endpoints_without_global_pair_set() -> None:
    records = [
        _person(
            index,
            source="/a.ged" if index % 2 == 0 else "/b.ged",
            surname="Shared",
        )
        for index in range(20)
    ]
    limits = _small_limits(max_bucket_size=32, max_pairs_per_person=2)
    profiles = tuple(gm._duplicate_profile(record) for record in records)

    pairs = list(gm._bounded_candidate_pairs(profiles, limits, cross_source_only=True))
    endpoint_counts = Counter(index for pair in pairs for index in pair)

    assert len(pairs) <= limits.max_scored_pairs
    assert endpoint_counts
    assert max(endpoint_counts.values()) <= limits.max_pairs_per_person


def test_normalized_profile_is_built_once_per_record() -> None:
    records = [
        _person(index, source="/a.ged" if index % 2 == 0 else "/b.ged") for index in range(12)
    ]

    with patch.object(gm, "_duplicate_profile", wraps=gm._duplicate_profile) as profile:
        gm.find_duplicate_candidates(records, limits=_small_limits())

    assert profile.call_count == len(records)


def test_dry_run_plan_is_json_serializable_and_contains_no_genealogy() -> None:
    records = [
        _person(
            index,
            source="/private/alice-tree.ged" if index % 2 == 0 else "/private/bob-tree.ged",
            given="PrivateGiven",
            surname="PrivateSurname",
        )
        for index in range(30)
    ]

    plan = gm.estimate_duplicate_search(records, _small_limits())
    payload = json.dumps(plan.to_dict(), sort_keys=True)

    assert plan.oversized_bucket_count > 0
    assert plan.scored_pair_upper_bound <= 40
    assert plan.candidate_count_upper_bound <= 20
    assert "PrivateGiven" not in payload
    assert "PrivateSurname" not in payload
    assert "alice-tree" not in payload
    assert "@I" not in payload


def test_hard_conflict_is_retained_without_adjudication() -> None:
    left = _person(1, source="/a.ged", surname="Shared", gender="M")
    right = _person(2, source="/b.ged", surname="Shared", gender="F")
    decisions: list[gm.MergeDecision] = []

    with (
        patch.object(gm, "find_duplicate_candidates", return_value=[(0, 1, 84.0)]),
        patch.object(gm, "_get_ai_verdict") as adjudicate,
    ):
        merged = gm.merge_records([left, right], threshold=70, auto=True, decisions=decisions)

    assert len(merged) == 2
    adjudicate.assert_not_called()
    assert decisions[0].disposition == "retained-hard-conflict"
    assert decisions[0].conflicts == ("sex",)


def test_per_person_adjudication_budget_fails_closed() -> None:
    records = [
        _person(0, source="/a.ged", surname="Smyth"),
        _person(1, source="/b.ged", surname="Smith", birth="1901"),
        _person(2, source="/c.ged", surname="Smith", birth="1901"),
        _person(3, source="/d.ged", surname="Smith", birth="1901"),
    ]
    candidates = [(0, 1, 90.0), (0, 2, 90.0), (0, 3, 90.0)]
    limits = _small_limits(max_adjudications_per_person=1, max_adjudications=3)
    decisions: list[gm.MergeDecision] = []
    verdict = {"is_duplicate": False, "confidence": 1.0, "reasoning": "distinct"}

    with (
        patch.object(gm, "find_duplicate_candidates", return_value=candidates),
        patch.object(gm, "_get_ai_verdict", return_value=verdict) as adjudicate,
    ):
        merged = gm.merge_records(
            records,
            threshold=70,
            auto=True,
            decisions=decisions,
            duplicate_limits=limits,
        )

    assert len(merged) == 4
    assert adjudicate.call_count == 1
    assert [decision.disposition for decision in decisions].count(
        "retained-adjudication-budget"
    ) == 2


def test_safe_merge_preserves_custom_blocks_citations_relationships_and_facts() -> None:
    left = gm._individual_from_record(
        gm.GedcomRecord(
            [
                "0 @I1@ INDI",
                "1 NAME Ada /Sample/",
                "1 SEX F",
                "1 BIRT",
                "2 DATE 1900",
                "2 PLAC Boston, Massachusetts, USA",
                "2 SOUR @S1@",
                "3 PAGE left citation",
                "1 _CUSTOM retained-left",
            ],
            "/a.ged",
            0,
        )
    )
    right = gm._individual_from_record(
        gm.GedcomRecord(
            [
                "0 @I2@ INDI",
                "1 NAME Ada /Sample/",
                "1 SEX F",
                "1 BIRT",
                "2 DATE 1900",
                "2 PLAC Boston, Massachusetts, USA",
                "2 SOUR @S2@",
                "3 PAGE right citation",
                "1 RESI",
                "2 DATE 1930",
                "2 PLAC Cambridge, Massachusetts, USA",
                "1 _CUSTOM retained-right",
            ],
            "/b.ged",
            0,
        )
    )
    left.family_links = ("@F1@",)
    right.family_links = ("@F2@",)
    right.partners = (gm.RelativeIdentity("@P2@", "Grace Example"),)

    merged = gm.merge_records([left, right], auto=True)
    output = "\n".join(merged[0].raw_lines)

    assert len(merged) == 1
    assert "left citation" in output
    assert "right citation" in output
    assert "retained-left" in output
    assert "retained-right" in output
    assert "1 RESI" in output
    assert merged[0].family_links == ("@F1@", "@F2@")
    assert merged[0].partners == (gm.RelativeIdentity("@P2@", "Grace Example"),)
