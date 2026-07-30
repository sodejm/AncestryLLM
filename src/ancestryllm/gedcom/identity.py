"""Deterministic identity comparison and conservative merge boundary."""

from ancestryllm.gedcom.engine import (
    DEFAULT_SIMILARITY_THRESHOLD,
    IndividualRecord,
    MatchAssessment,
    MergeDecision,
    _build_dedup_prompt,
    _dedup_response_schema,
    _individual_from_record,
    assess_similarity,
    enrich_relationship_context,
    find_duplicate_candidates,
    merge_records,
    merge_two_records,
    similarity_score,
)

# The compatibility kernel still owns the implementation, but callers use
# stable, intention-revealing names at this boundary. These aliases can retain
# their signatures while the implementation moves behind them.
build_dedup_prompt = _build_dedup_prompt
dedup_response_schema = _dedup_response_schema
individual_from_record = _individual_from_record

__all__ = [
    "DEFAULT_SIMILARITY_THRESHOLD",
    "IndividualRecord",
    "MatchAssessment",
    "MergeDecision",
    "assess_similarity",
    "build_dedup_prompt",
    "dedup_response_schema",
    "enrich_relationship_context",
    "find_duplicate_candidates",
    "individual_from_record",
    "merge_records",
    "merge_two_records",
    "similarity_score",
]
