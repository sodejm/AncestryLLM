"""Deterministic identity comparison and conservative merge boundary."""

from ancestryllm.gedcom.engine import (
    IndividualRecord,
    MatchAssessment,
    assess_similarity,
    find_duplicate_candidates,
    merge_records,
    merge_two_records,
    similarity_score,
)

__all__ = [
    "IndividualRecord",
    "MatchAssessment",
    "assess_similarity",
    "find_duplicate_candidates",
    "merge_records",
    "merge_two_records",
    "similarity_score",
]
