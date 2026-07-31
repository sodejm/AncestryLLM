"""Compatibility imports for the modular GEDCOM implementation.

New code imports the owning parser, graph, identity, quality, or serialization
module.  This module preserves established import paths without owning
algorithms, adapters, or artifact publication.
"""

from __future__ import annotations

from ancestryllm.gedcom import graph as _graph
from ancestryllm.gedcom import identity as _identity
from ancestryllm.gedcom import parser as _parser
from ancestryllm.gedcom import quality as _quality
from ancestryllm.gedcom import serialization as _serialization

_ROOTED_AUXILIARY_RECORD_TAGS = _graph._ROOTED_AUXILIARY_RECORD_TAGS
_exact_pointer_references = _graph._exact_pointer_references
_rewrite_xrefs = _graph._rewrite_xrefs
_rooted_auxiliary_pointer_closure = _graph._rooted_auxiliary_pointer_closure
connected_tree_pointers = _graph.connected_tree_pointers
resolve_root_person = _graph.resolve_root_person

AI_CONFIDENCE_AUTO_ACCEPT = _identity.AI_CONFIDENCE_AUTO_ACCEPT
COUNTRY_ALIASES = _identity.COUNTRY_ALIASES
DATE_QUALIFIERS = _identity.DATE_QUALIFIERS
DEFAULT_DUPLICATE_MAX_ADJUDICATIONS = _identity.DEFAULT_DUPLICATE_MAX_ADJUDICATIONS
DEFAULT_DUPLICATE_MAX_ADJUDICATIONS_PER_PERSON = (
    _identity.DEFAULT_DUPLICATE_MAX_ADJUDICATIONS_PER_PERSON
)
DEFAULT_DUPLICATE_MAX_BUCKET_SIZE = _identity.DEFAULT_DUPLICATE_MAX_BUCKET_SIZE
DEFAULT_DUPLICATE_MAX_CANDIDATES = _identity.DEFAULT_DUPLICATE_MAX_CANDIDATES
DEFAULT_DUPLICATE_MAX_PAIRS_PER_PERSON = _identity.DEFAULT_DUPLICATE_MAX_PAIRS_PER_PERSON
DEFAULT_DUPLICATE_MAX_SCORED_PAIRS = _identity.DEFAULT_DUPLICATE_MAX_SCORED_PAIRS
DEFAULT_SIMILARITY_THRESHOLD = _identity.DEFAULT_SIMILARITY_THRESHOLD
DETERMINISTIC_HARD_CONFLICTS = _identity.DETERMINISTIC_HARD_CONFLICTS
FAMILY_IDENTITY_FACT_TAGS = _identity.FAMILY_IDENTITY_FACT_TAGS
GEDCOM_MONTHS = _identity.GEDCOM_MONTHS
IDENTITY_FACT_TAGS = _identity.IDENTITY_FACT_TAGS
KNOWN_COUNTRY_NAMES = _identity.KNOWN_COUNTRY_NAMES
MAX_AI_TEXT = _identity.MAX_AI_TEXT
MAX_DEDUP_PROMPT_TOKENS = _identity.MAX_DEDUP_PROMPT_TOKENS
MAX_DUPLICATE_RELATIVES_PER_ROLE = _identity.MAX_DUPLICATE_RELATIVES_PER_ROLE
XREF_RE = _identity.XREF_RE
DuplicateSearchLimits = _identity.DuplicateSearchLimits
DuplicateSearchPlan = _identity.DuplicateSearchPlan
GenealogicalFact = _identity.GenealogicalFact
IndividualRecord = _identity.IndividualRecord
MatchAssessment = _identity.MatchAssessment
PersonalName = _identity.PersonalName
RelativeIdentity = _identity.RelativeIdentity
_blocking_keys = _identity._blocking_keys
_dedup_response_schema = _identity._dedup_response_schema
_normalise_country = _identity._normalise_country
_normalise_record_dates = _identity._normalise_record_dates
_record_to_gedcom_lines = _identity._record_to_gedcom_lines
_top_level_blocks = _identity._top_level_blocks
assess_similarity = _identity.assess_similarity
enrich_relationship_context = _identity.enrich_relationship_context
estimate_duplicate_search = _identity.estimate_duplicate_search
find_duplicate_candidates = _identity.find_duplicate_candidates
merge_two_records = _identity.merge_two_records
normalise_gedcom_date = _identity.normalise_gedcom_date
similarity_score = _identity.similarity_score

GedcomLine = _parser.GedcomLine
GedcomParseError = _parser.GedcomParseError
GedcomRecord = _parser.GedcomRecord
ParsedSource = _parser.ParsedSource
iter_gedcom_records = _parser.iter_gedcom_records
load_sources = _parser.load_sources
parse_gedcom_line = _parser.parse_gedcom_line

QUALITY_AI_LIMIT = _quality.QUALITY_AI_LIMIT
QUALITY_DUPLICATE_THRESHOLD = _quality.QUALITY_DUPLICATE_THRESHOLD
QUALITY_SEVERITY_ORDER = _quality.QUALITY_SEVERITY_ORDER
QualityFinding = _quality.QualityFinding
QualityReport = _quality.QualityReport
_markdown = _quality._markdown
_quality_response_schema = _quality._quality_response_schema
analyze_quality = _quality.analyze_quality
refine_quality_report_with_ai = _quality.refine_quality_report_with_ai
render_quality_report = _quality.render_quality_report

SUPPORTED_GEDCOM_VERSIONS = _serialization.SUPPORTED_GEDCOM_VERSIONS
validate_gedcom_555 = _serialization.validate_gedcom_555
wrap_long_gedcom_lines = _serialization.wrap_long_gedcom_lines
write_gedcom = _serialization.write_gedcom
write_quality_diagnostic = _serialization.write_quality_diagnostic
write_quality_report = _serialization.write_quality_report

__all__ = [
    "GedcomLine",
    "GedcomParseError",
    "GedcomRecord",
    "IndividualRecord",
    "ParsedSource",
    "QualityFinding",
    "QualityReport",
    "analyze_quality",
    "assess_similarity",
    "connected_tree_pointers",
    "enrich_relationship_context",
    "estimate_duplicate_search",
    "find_duplicate_candidates",
    "iter_gedcom_records",
    "load_sources",
    "merge_two_records",
    "normalise_gedcom_date",
    "parse_gedcom_line",
    "refine_quality_report_with_ai",
    "render_quality_report",
    "resolve_root_person",
    "similarity_score",
    "validate_gedcom_555",
    "write_gedcom",
    "write_quality_diagnostic",
    "write_quality_report",
]
