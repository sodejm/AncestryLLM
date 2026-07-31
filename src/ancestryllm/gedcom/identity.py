"""Deterministic GEDCOM identity evidence, matching, and conservative merge."""

from __future__ import annotations

import dataclasses
import datetime as dt
import difflib
import heapq
import logging
import math
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ancestryllm.gedcom.contracts import DuplicateDecision, IdentityResolver

from ancestryllm.core.cancellation import CancellationError, cancellation_checkpoint
from ancestryllm.gedcom.model import GedcomParseError, GedcomRecord, parse_gedcom_line

_rapidfuzz: ModuleType | None
try:
    from rapidfuzz import fuzz as _rapidfuzz
except ImportError:  # pragma: no cover - exercised in minimal installations
    _rapidfuzz = None

log = logging.getLogger(__name__)

GEDCOM_MONTHS: tuple[str, ...] = (
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
)
DATE_QUALIFIERS: dict[str, str] = {
    "about": "ABT",
    "abt": "ABT",
    "approximately": "ABT",
    "circa": "ABT",
    "ca": "ABT",
    "ca.": "ABT",
    "c.": "ABT",
    "before": "BEF",
    "bef": "BEF",
    "after": "AFT",
    "aft": "AFT",
    "estimated": "EST",
    "est": "EST",
    "calculated": "CAL",
    "cal": "CAL",
}
DEFAULT_SIMILARITY_THRESHOLD = 78
AI_CONFIDENCE_AUTO_ACCEPT = 0.85
MAX_AI_TEXT = 2_000
DEFAULT_DUPLICATE_MAX_BUCKET_SIZE = 128
DEFAULT_DUPLICATE_MAX_PAIRS_PER_PERSON = 32
DEFAULT_DUPLICATE_MAX_SCORED_PAIRS = 50_000
DEFAULT_DUPLICATE_MAX_CANDIDATES = 50_000
DEFAULT_DUPLICATE_MAX_ADJUDICATIONS_PER_PERSON = 4
DEFAULT_DUPLICATE_MAX_ADJUDICATIONS = 250
MAX_DUPLICATE_RELATIVES_PER_ROLE = 16
MAX_DEDUP_PROMPT_TOKENS = MAX_AI_TEXT * 2 + 1_000
DETERMINISTIC_HARD_CONFLICTS = frozenset({"sex", "birth country", "death country"})
XREF_RE = re.compile(r"@[A-Za-z0-9_:-]+@")
IDENTITY_FACT_TAGS = frozenset(
    {
        "ADOP",
        "BAPM",
        "BARM",
        "BASM",
        "BIRT",
        "BLES",
        "BURI",
        "CAST",
        "CENS",
        "CHR",
        "CHRA",
        "CONF",
        "CREM",
        "DEAT",
        "DSCR",
        "EDUC",
        "EMIG",
        "EVEN",
        "FCOM",
        "GRAD",
        "IMMI",
        "NATI",
        "NATU",
        "OCCU",
        "ORDN",
        "PROB",
        "PROP",
        "RELI",
        "RESI",
        "RETI",
        "TITL",
    }
)
FAMILY_IDENTITY_FACT_TAGS = frozenset(
    {"ANUL", "DIV", "DIVF", "ENGA", "MARB", "MARC", "MARL", "MARR", "MARS"}
)
COUNTRY_ALIASES = {
    "america": "united states",
    "england": "united kingdom",
    "great britain": "united kingdom",
    "scotland": "united kingdom",
    "u k": "united kingdom",
    "uk": "united kingdom",
    "united states of america": "united states",
    "us": "united states",
    "u s": "united states",
    "usa": "united states",
    "u s a": "united states",
    "wales": "united kingdom",
}
KNOWN_COUNTRY_NAMES = frozenset(COUNTRY_ALIASES.values()) | frozenset(
    {
        "argentina",
        "australia",
        "austria",
        "belgium",
        "brazil",
        "bulgaria",
        "canada",
        "chile",
        "china",
        "croatia",
        "cuba",
        "czech republic",
        "czechoslovakia",
        "denmark",
        "estonia",
        "finland",
        "france",
        "germany",
        "greece",
        "hungary",
        "iceland",
        "india",
        "ireland",
        "israel",
        "italy",
        "japan",
        "latvia",
        "lithuania",
        "luxembourg",
        "mexico",
        "netherlands",
        "new zealand",
        "norway",
        "poland",
        "portugal",
        "prussia",
        "romania",
        "russia",
        "serbia",
        "slovakia",
        "slovenia",
        "south africa",
        "soviet union",
        "spain",
        "sweden",
        "switzerland",
        "turkey",
        "ukraine",
        "yugoslavia",
    }
)


def _normalise_record_dates(lines: list[str]) -> list[str]:
    """Normalise BIRT/DEAT dates and retain changed originals with a custom tag."""
    output: list[str] = []
    event_tag = ""
    event_level = -1
    for line in lines:
        parsed = parse_gedcom_line(line)
        if parsed.level <= 1:
            event_tag = parsed.tag if parsed.level == 1 else ""
            event_level = parsed.level if parsed.level == 1 else -1
        if (
            parsed.level == event_level + 1
            and parsed.tag == "DATE"
            and event_tag in {"BIRT", "DEAT"}
        ):
            normalised = normalise_gedcom_date(parsed.value)
            if normalised != parsed.value and normalised.strip():
                output.append(f"{parsed.level} DATE {normalised}")
                output.append(f"{parsed.level} _ORIGDATE {parsed.value}")
                continue
        output.append(line)
    return output


def normalise_gedcom_date(raw_date: str) -> str:
    """Normalize common dates without fabricating missing precision.

    Existing GEDCOM ranges are preserved, common qualifiers are canonicalized,
    and fully specified dates become ``DD MMM YYYY``.  Unrecognized text is
    returned unchanged so normalization cannot erase source evidence.
    """
    if not raw_date or not raw_date.strip():
        return raw_date
    original = raw_date.strip()
    upper = original.upper()
    if upper.startswith(("BET ", "FROM ", "TO ")):
        return original
    qualifier = ""
    for prefix, gedcom_prefix in DATE_QUALIFIERS.items():
        if upper == gedcom_prefix or upper.startswith(gedcom_prefix + " "):
            qualifier = gedcom_prefix
            original = original[len(gedcom_prefix) :].strip()
            break
        if upper == prefix.upper() or upper.startswith(prefix.upper() + " "):
            qualifier = gedcom_prefix
            original = original[len(prefix) :].strip()
            break
    year_match = re.fullmatch(r"(\d{3,4})(?:/\d{2})?", original)
    if year_match:
        result = year_match.group(1)
        return f"{qualifier} {result}".strip()
    iso_match = re.fullmatch(r"(\d{3,4})[-/](\d{1,2})[-/](\d{1,2})", original)
    if iso_match:
        year, month, day = (int(part) for part in iso_match.groups())
        try:
            dt.date(year, month, day)
        except ValueError:
            return raw_date
        result = f"{day:02d} {GEDCOM_MONTHS[month - 1]} {year:04d}"
        return f"{qualifier} {result}".strip()
    full_match = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{3,4})", original)
    if full_match and full_match.group(2).upper() in GEDCOM_MONTHS:
        day_text, month_text, year_text = full_match.groups()
        try:
            dt.date(
                int(year_text),
                GEDCOM_MONTHS.index(month_text.upper()) + 1,
                int(day_text),
            )
        except ValueError:
            return raw_date
        result = f"{int(day_text):02d} {month_text.upper()} {year_text}"
        return f"{qualifier} {result}".strip()
    month_year = re.fullmatch(r"([A-Za-z]{3})\s+(\d{3,4})", original)
    if month_year and month_year.group(1).upper() in GEDCOM_MONTHS:
        result = f"{month_year.group(1).upper()} {month_year.group(2)}"
        return f"{qualifier} {result}".strip()
    try:
        from dateutil import parser as date_parser

        sentinel = dt.datetime(1111, 11, 11)
        parsed = date_parser.parse(original, default=sentinel, fuzzy=False)
        if re.fullmatch(r"[A-Za-z]+\s+\d{3,4}", original):
            result = f"{GEDCOM_MONTHS[parsed.month - 1]} {parsed.year}"
        else:
            result = f"{parsed.day:02d} {GEDCOM_MONTHS[parsed.month - 1]} {parsed.year}"
        return f"{qualifier} {result}".strip()
    except (ImportError, ValueError, OverflowError, TypeError):
        # Keep a small standard-library fallback so date normalisation still
        # works when the optional dateutil dependency is not installed.
        for pattern in ("%B %d, %Y", "%d %B %Y", "%b %d, %Y", "%d %b %Y"):
            try:
                parsed = dt.datetime.strptime(original, pattern)
                result = f"{parsed.day:02d} {GEDCOM_MONTHS[parsed.month - 1]} {parsed.year}"
                return f"{qualifier} {result}".strip()
            except ValueError:
                continue
        log.debug("Could not normalise a GEDCOM date value")
        return raw_date


def _extract_year(date_value: str) -> Optional[int]:
    """Extract the first four-digit year from a date or return ``None``."""
    match = re.search(r"\b(\d{4})\b", date_value or "")
    return int(match.group(1)) if match else None


def _normalise_country(value: str) -> str:
    """Return a comparison form for a country or country-like jurisdiction."""
    normalised = " ".join(re.findall(r"[\w]+", value.casefold()))
    return COUNTRY_ALIASES.get(normalised, normalised)


def _country_from_place(place: str) -> str:
    """Infer the country component from a comma-delimited GEDCOM place.

    GEDCOM 5.5.x commonly stores jurisdictions from smallest to largest in a
    single ``PLAC`` value.  The final component is therefore useful evidence,
    but a one-component place such as ``London`` must not be labeled a country.
    Recognized aliases such as ``USA`` are accepted without a comma.
    """
    components = [part.strip() for part in place.split(",") if part.strip()]
    candidate = _normalise_country(components[-1] if components else place)
    if candidate in KNOWN_COUNTRY_NAMES:
        return candidate
    return ""


@dataclass(frozen=True, slots=True)
class GenealogicalFact:
    """Structured identity evidence extracted from one GEDCOM fact block.

    The complete source block remains in ``IndividualRecord.raw_lines``.
    This compact view exists only for comparison and bounded AI prompts.
    """

    tag: str
    value: str = ""
    date: str = ""
    place: str = ""
    country: str = ""

    @property
    def effective_country(self) -> str:
        """Return an explicit country or a conservative place inference."""
        return _normalise_country(self.country) or _country_from_place(self.place)

    def summary(self) -> str:
        """Return a concise, deterministic representation for comparison."""
        parts = [part for part in (self.value, self.date, self.place) if part]
        if self.effective_country and self.effective_country not in {
            _normalise_country(part) for part in parts
        }:
            parts.append(self.effective_country)
        return " | ".join(parts)


@dataclass(frozen=True, slots=True)
class RelativeIdentity:
    """Bounded genealogical context for a partner, parent, or child.

    Names, dates, places, and relationships are personal data.  They are kept
    deliberately compact because this projection can be included in a remote
    adjudication prompt when the operator enables a remote provider.
    """

    pointer: str
    name: str
    birth_date: str = ""
    death_date: str = ""
    relationship: str = ""
    alternate_names: tuple[str, ...] = field(default_factory=tuple)
    birth_place: str = ""
    death_place: str = ""

    def summary(self) -> str:
        """Return a compact relative description that benefits sparse people."""
        parts = [self.name or "(unknown)"]
        if self.birth_date:
            parts.append(f"b. {self.birth_date}")
        if self.death_date:
            parts.append(f"d. {self.death_date}")
        if self.birth_place:
            parts.append(f"b.place={self.birth_place}")
        if self.death_place:
            parts.append(f"d.place={self.death_place}")
        if self.relationship:
            parts.append(f"relationship={self.relationship}")
        return " ".join(parts)


@dataclass(frozen=True, slots=True)
class MatchAssessment:
    """Explain a composite identity score and whether auto-merge is safe."""

    score: float
    evidence_weight: float
    compared_fields: tuple[str, ...]
    conflicts: tuple[str, ...]

    @property
    def automatic_merge_safe(self) -> bool:
        """Return whether independent evidence supports deterministic merge."""
        personal_anchors = {
            "birth date",
            "birth place",
            "birth country",
            "death date",
            "death place",
            "death country",
            "sex",
            "occupation",
            "residence",
            "other standard facts",
        }
        relative_anchors = {"partners", "parents", "children"}
        compared = set(self.compared_fields)
        independent_anchor = bool(personal_anchors.intersection(compared)) or (
            "family events" in compared and len(relative_anchors.intersection(compared)) >= 2
        )
        return (
            self.score >= 95.0
            and self.evidence_weight >= 50.0
            and len(self.compared_fields) >= 3
            and not self.conflicts
            and independent_anchor
        )


@dataclass(frozen=True, slots=True)
class PersonalName:
    """One losslessly represented GEDCOM ``NAME`` structure.

    GEDCOM permits several names for one person and permits structured
    components below each ``NAME``.  Keeping these components separate is
    essential for distinguishing a birth/maiden form from a married form
    without inventing a surname.

    Attributes:
        value: Original display value from the ``NAME`` line.
        given: Structured ``GIVN`` value, or the parsed display-name fallback.
        surname: Structured ``SURN`` value, or the parsed display-name fallback.
        prefix: Structured ``NPFX`` value.
        suffix: Structured ``NSFX`` value.
        nickname: Structured ``NICK`` value.
        name_type: Case-insensitive ``TYPE`` value, normalized to lowercase.
        is_primary: Whether this was the first ``NAME`` structure in the record.
    """

    value: str
    given: str = ""
    surname: str = ""
    prefix: str = ""
    suffix: str = ""
    nickname: str = ""
    name_type: str = ""
    is_primary: bool = False

    @property
    def display_name(self) -> str:
        """Return a compact display name without changing stored components."""
        if self.value.strip():
            given, surname = _name_parts(self.value)
            return " ".join(part for part in (given, surname) if part)
        return " ".join(part for part in (self.given, self.surname) if part)


@dataclass(frozen=True, slots=True)
class MergeDecision:
    """Immutable audit entry for one considered duplicate pair.

    ``disposition`` records whether the pair merged or was retained.  Provider
    metadata describes the route actually used and is deliberately separate
    from deterministic score/evidence so a model explanation cannot rewrite
    the measurable basis for the decision.
    """

    left_pointer: str
    right_pointer: str
    score: float
    compared_fields: tuple[str, ...]
    conflicts: tuple[str, ...]
    disposition: str
    confidence: float = 0.0
    provider: str = "deterministic"
    model: str = ""
    reasoning: str = ""


@dataclass(frozen=True, slots=True)
class DuplicateSearchLimits:
    """Hard bounds for duplicate candidate generation and adjudication.

    Oversized blocking buckets are not expanded pairwise.  More-specific
    deterministic keys remain eligible, while candidate work is capped per
    person and per job.  This can retain an ambiguous common-name group for
    manual research instead of risking an unbounded or weakly supported merge.
    """

    max_bucket_size: int = DEFAULT_DUPLICATE_MAX_BUCKET_SIZE
    max_pairs_per_person: int = DEFAULT_DUPLICATE_MAX_PAIRS_PER_PERSON
    max_scored_pairs: int = DEFAULT_DUPLICATE_MAX_SCORED_PAIRS
    max_candidates: int = DEFAULT_DUPLICATE_MAX_CANDIDATES
    max_adjudications_per_person: int = DEFAULT_DUPLICATE_MAX_ADJUDICATIONS_PER_PERSON
    max_adjudications: int = DEFAULT_DUPLICATE_MAX_ADJUDICATIONS

    def __post_init__(self) -> None:
        """Reject limits that would silently disable conservative safeguards."""
        for field_definition in dataclasses.fields(self):
            if int(getattr(self, field_definition.name)) <= 0:
                raise ValueError(f"{field_definition.name} must be positive")


@dataclass(frozen=True, slots=True)
class DuplicateSearchPlan:
    """Serializable, content-free estimate for a future dry-run API."""

    record_count: int
    blocking_key_count: int
    bounded_bucket_count: int
    oversized_bucket_count: int
    raw_pair_upper_bound: int
    scored_pair_upper_bound: int
    candidate_count_upper_bound: int
    adjudication_count_range: tuple[int, int]
    prompt_token_range: tuple[int, int]
    oversized_bucket_policy: str

    def to_dict(self) -> dict[str, object]:
        """Return JSON-compatible metrics without names, pointers, or paths."""
        return dataclasses.asdict(self)


@dataclass
class IndividualRecord:
    """Deduplication summary plus the complete underlying INDI record.

    Missing summary fields mean "unknown," never "different."  Relationship
    context is attached after all FAM records are available so a sparse aunt,
    uncle, or parent can still be matched through well-documented relatives.
    """

    pointer: str
    given_name: str = ""
    surname: str = ""
    birth_date: str = ""
    birth_place: str = ""
    death_date: str = ""
    death_place: str = ""
    gender: str = ""
    source_file: str = ""
    element: object = field(default=None, repr=False, compare=False)
    extra_fields: dict[str, list[str]] = field(default_factory=dict)
    raw_lines: list[str] = field(default_factory=list, repr=False, compare=False)
    family_links: tuple[str, ...] = field(default_factory=tuple)
    family_references: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    alternate_names: tuple[str, ...] = field(default_factory=tuple)
    names: tuple[PersonalName, ...] = field(default_factory=tuple)
    source_files: tuple[str, ...] = field(default_factory=tuple)
    facts: dict[str, tuple[GenealogicalFact, ...]] = field(default_factory=dict)
    marriages: tuple[GenealogicalFact, ...] = field(default_factory=tuple)
    partners: tuple[RelativeIdentity, ...] = field(default_factory=tuple)
    parents: tuple[RelativeIdentity, ...] = field(default_factory=tuple)
    children: tuple[RelativeIdentity, ...] = field(default_factory=tuple)

    @property
    def full_name(self) -> str:
        """Return the compact display name."""
        return " ".join(part.strip() for part in (self.given_name, self.surname) if part.strip())

    @property
    def birth_year(self) -> Optional[int]:
        """Return the birth year, if present."""
        return _extract_year(self.birth_date)

    @property
    def death_year(self) -> Optional[int]:
        """Return the death year, if present."""
        return _extract_year(self.death_date)

    @property
    def birth_country(self) -> str:
        """Return the best explicit or inferred country of birth."""
        return self.birth_countries[0] if self.birth_countries else ""

    @property
    def birth_countries(self) -> tuple[str, ...]:
        """Return every distinct explicit or inferred birth country."""
        countries = tuple(
            fact.effective_country for fact in self.facts.get("BIRT", ()) if fact.effective_country
        )
        fallback = _country_from_place(self.birth_place)
        return tuple(dict.fromkeys(countries + ((fallback,) if fallback else ())))

    @property
    def death_country(self) -> str:
        """Return the best explicit or inferred country of death."""
        return self.death_countries[0] if self.death_countries else ""

    @property
    def death_countries(self) -> tuple[str, ...]:
        """Return every distinct explicit or inferred death country."""
        countries = tuple(
            fact.effective_country for fact in self.facts.get("DEAT", ()) if fact.effective_country
        )
        fallback = _country_from_place(self.death_place)
        return tuple(dict.fromkeys(countries + ((fallback,) if fallback else ())))

    @property
    def occupations(self) -> tuple[GenealogicalFact, ...]:
        """Return all standard occupation facts used as identity evidence."""
        return self.facts.get("OCCU", ())

    @property
    def residences(self) -> tuple[GenealogicalFact, ...]:
        """Return all residence facts, including their dates and places."""
        return self.facts.get("RESI", ())

    @property
    def partner_names(self) -> tuple[str, ...]:
        """Return known partner names without exposing family pointers."""
        return tuple(relative.name for relative in self.partners if relative.name)

    def summary(self) -> str:
        """Return a bounded operator/provider adjudication summary."""
        parts = [f"[{self.pointer}] {self.full_name or '(unknown)'}"]
        if self.alternate_names:
            parts.append(f"alternate-names={list(self.alternate_names[:3])}")
        if self.birth_date:
            parts.append(f"b. {self.birth_date}")
        if self.birth_place:
            parts.append(f"b.place={self.birth_place}")
        if self.birth_country:
            parts.append(f"b.country={self.birth_country}")
        if len(self.facts.get("BIRT", ())) > 1:
            values = [fact.summary() for fact in self.facts["BIRT"][:3]]
            parts.append(f"birth-alternatives={values}")
        if self.death_date:
            parts.append(f"d. {self.death_date}")
        if self.death_place:
            parts.append(f"d.place={self.death_place}")
        if self.death_country:
            parts.append(f"d.country={self.death_country}")
        if len(self.facts.get("DEAT", ())) > 1:
            values = [fact.summary() for fact in self.facts["DEAT"][:3]]
            parts.append(f"death-alternatives={values}")
        if self.gender:
            parts.append(f"sex={self.gender}")
        if self.family_links:
            parts.append(f"family-links={len(self.family_links)}")
        if self.occupations:
            values = [fact.summary() for fact in self.occupations[:3]]
            parts.append(f"occupations={values}")
        if self.residences:
            values = [fact.summary() for fact in self.residences[:3]]
            parts.append(f"residences={values}")
        if self.marriages:
            values = [fact.summary() for fact in self.marriages[:3]]
            parts.append(f"marriages={values}")
        other_fact_values = [
            f"{tag}:{fact.summary()}"
            for tag, facts in sorted(self.facts.items())
            if tag not in {"BIRT", "DEAT", "OCCU", "RESI"}
            for fact in facts[:2]
            if fact.summary()
        ][:8]
        if other_fact_values:
            parts.append(f"other-facts={other_fact_values}")
        for label, relatives in (
            ("partners", self.partners),
            ("parents", self.parents),
            ("children", self.children),
        ):
            if relatives:
                values = [relative.summary() for relative in relatives[:5]]
                parts.append(f"{label}={values}")
        if self.source_file:
            parts.append(f"src={Path(self.source_file).name}")
        return "  ".join(parts)


def _name_parts(value: str) -> tuple[str, str]:
    """Parse common ``Given /Surname/`` and plain-name variants."""
    value = value.strip()
    if "/" in value:
        before, rest = value.split("/", 1)
        surname = rest.split("/", 1)[0].strip()
        return before.strip(), surname
    tokens = value.split()
    return " ".join(tokens[:-1]), tokens[-1] if tokens else ""


def _top_level_blocks(lines: list[str]) -> list[list[str]]:
    """Split an INDI record into its level-one child blocks."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines[1:]:
        parsed = parse_gedcom_line(line)
        if parsed.level == 1:
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _fact_from_block(block: Sequence[str]) -> GenealogicalFact:
    """Extract comparable fields from one individual or family fact block.

    ``PLAC`` is preferred for location.  Some writers instead emit the
    structured ``ADDR/CITY/STAE/CTRY`` hierarchy, so those components form a
    fallback.  Free-form notes and citations are intentionally excluded from
    this comparison view while remaining untouched in the raw GEDCOM block.
    """
    first = parse_gedcom_line(block[0])
    value = first.value.strip()
    if value.upper() == "Y":
        value = ""
    date_value = ""
    place_value = ""
    country_value = ""
    address_parts: dict[str, str] = {}
    for index, line in enumerate(block[1:], 1):
        child = parse_gedcom_line(line)
        child_value = child.value.strip()
        continuation_parts = [child_value]
        for continuation_line in block[index + 1 :]:
            continuation = parse_gedcom_line(continuation_line)
            if continuation.level <= child.level:
                break
            if continuation.tag == "CONC":
                continuation_parts[-1] += continuation.value
            elif continuation.tag == "CONT":
                continuation_parts.append(continuation.value)
        child_value = "\n".join(continuation_parts).strip()
        if child.tag == "DATE" and not date_value:
            date_value = normalise_gedcom_date(child_value)
        elif child.tag == "PLAC" and not place_value:
            place_value = child_value
        elif child.tag == "CTRY" and not country_value:
            country_value = child_value
        elif child.tag in {"ADDR", "CITY", "STAE"} and child_value:
            address_parts.setdefault(child.tag, child_value)
    if not place_value and address_parts:
        ordered = [
            address_parts.get("ADDR", ""),
            address_parts.get("CITY", ""),
            address_parts.get("STAE", ""),
            country_value,
        ]
        place_value = ", ".join(part for part in ordered if part)
    return GenealogicalFact(
        tag=first.tag,
        value=value,
        date=date_value,
        place=place_value,
        country=country_value,
    )


def _most_complete_fact(
    facts: Sequence[GenealogicalFact],
) -> Optional[GenealogicalFact]:
    """Choose a display fact while retaining every alternative for scoring."""
    if not facts:
        return None
    return max(
        facts,
        key=lambda fact: (
            bool(fact.date),
            len(fact.date),
            bool(fact.place),
            len(fact.place),
            bool(fact.country),
        ),
    )


def _personal_name_from_block(
    block: Sequence[str],
    *,
    is_primary: bool,
) -> PersonalName:
    """Parse one ``NAME`` block while preserving every standard component.

    Subordinate tags are matched case-insensitively by
    :func:`parse_gedcom_line`.  Repeated components are conservatively joined
    in source order because dropping a repeated value would violate the
    tool's lossless-data contract.

    Args:
        block: A complete level-one ``NAME`` structure.
        is_primary: Whether this is the first name in the individual record.

    Returns:
        A structured name suitable for analysis and faithful serialization.

    Raises:
        GedcomParseError: The block does not begin with ``NAME``.
    """
    first = parse_gedcom_line(block[0])
    if first.tag != "NAME":
        raise GedcomParseError("Personal-name block must begin with NAME")
    components: dict[str, list[str]] = defaultdict(list)
    for line in block[1:]:
        parsed = parse_gedcom_line(line)
        if parsed.tag in {"GIVN", "SURN", "NICK", "NPFX", "NSFX", "TYPE"}:
            value = parsed.value.strip()
            if value:
                components[parsed.tag].append(value)
    parsed_given, parsed_surname = _name_parts(first.value)

    def joined(tag: str, fallback: str = "") -> str:
        return "; ".join(components.get(tag, ())) or fallback

    return PersonalName(
        value=first.value.strip(),
        given=joined("GIVN", parsed_given),
        surname=joined("SURN", parsed_surname),
        prefix=joined("NPFX"),
        suffix=joined("NSFX"),
        nickname=joined("NICK"),
        name_type=joined("TYPE").casefold(),
        is_primary=is_primary,
    )


def _individual_from_record(record: GedcomRecord) -> IndividualRecord:
    """Build structured identity evidence from a lossless INDI record.

    Multiple names and event alternatives are retained.  The most complete
    birth/death event becomes the compact display value, but all alternatives
    remain in ``facts`` and ``raw_lines`` so comparison never discards them.
    """
    lines = _normalise_record_dates(record.lines)
    name = surname = birth_date = birth_place = death_date = death_place = gender = ""
    family_links: list[str] = []
    family_references: list[tuple[str, str]] = []
    alternate_names: list[str] = []
    names: list[PersonalName] = []
    facts: dict[str, list[GenealogicalFact]] = defaultdict(list)
    extra: dict[str, list[str]] = defaultdict(list)
    for block in _top_level_blocks(lines):
        first = parse_gedcom_line(block[0])
        if first.tag == "NAME":
            personal_name = _personal_name_from_block(
                block,
                is_primary=not names,
            )
            names.append(personal_name)
            given_name = personal_name.given
            family_name = personal_name.surname
            display_name = personal_name.display_name
            if not name and not surname:
                name, surname = given_name, family_name
            elif display_name:
                alternate_names.append(display_name)
        elif first.tag == "SEX":
            gender = first.value.strip().upper()
        elif first.tag in {"FAMS", "FAMC"}:
            if first.value.strip():
                family_links.append(first.value.strip())
                family_references.append((first.tag, first.value.strip()))
        elif first.tag in {"BIRT", "DEAT"}:
            facts[first.tag].append(_fact_from_block(block))
        else:
            extra[first.tag].append("\n".join(block) + "\n")
            if first.tag in IDENTITY_FACT_TAGS:
                facts[first.tag].append(_fact_from_block(block))

    birth_fact = _most_complete_fact(facts.get("BIRT", ()))
    if birth_fact is not None:
        birth_date, birth_place = birth_fact.date, birth_fact.place
    death_fact = _most_complete_fact(facts.get("DEAT", ()))
    if death_fact is not None:
        death_date, death_place = death_fact.date, death_fact.place
    return IndividualRecord(
        pointer=record.pointer,
        given_name=name,
        surname=surname,
        birth_date=birth_date,
        birth_place=birth_place,
        death_date=death_date,
        death_place=death_place,
        gender=gender,
        source_file=record.source_file,
        element=record,
        extra_fields=dict(extra),
        raw_lines=lines,
        family_links=tuple(family_links),
        family_references=tuple(family_references),
        alternate_names=tuple(dict.fromkeys(alternate_names)),
        names=tuple(names),
        source_files=(record.source_file,),
        facts={tag: tuple(values) for tag, values in facts.items()},
    )


def _relative_identity(
    record: IndividualRecord,
    relationship: str = "",
) -> RelativeIdentity:
    """Project a person into the bounded context shared with related people."""
    return RelativeIdentity(
        pointer=record.pointer,
        name=record.full_name,
        birth_date=record.birth_date,
        death_date=record.death_date,
        relationship=relationship,
        alternate_names=record.alternate_names,
        birth_place=record.birth_place,
        death_place=record.death_place,
    )


def enrich_relationship_context(
    people: Sequence[IndividualRecord],
    source_records: Iterable[GedcomRecord],
) -> list[IndividualRecord]:
    """Attach marriages and relative identities from standard FAM records.

    Family context is corroborating evidence, not a completeness requirement.
    A person with no birth date is not penalized, and a richer child or partner
    can support the match.  Unknown references are skipped because inventing a
    relative identity would be more dangerous than omitting that evidence.

    Args:
        people: Parsed people whose pointers already match the family records.
        source_records: All source records, including ``FAM`` records.

    Returns:
        New person objects with partner, parent, child, and marriage context.
    """
    by_pointer = {person.pointer: person for person in people}
    partners: dict[str, list[RelativeIdentity]] = defaultdict(list)
    parents: dict[str, list[RelativeIdentity]] = defaultdict(list)
    children: dict[str, list[RelativeIdentity]] = defaultdict(list)
    marriages: dict[str, list[GenealogicalFact]] = defaultdict(list)
    pedigree_by_person_family: dict[tuple[str, str], str] = {}
    for person in people:
        cancellation_checkpoint()
        for block in _top_level_blocks(person.raw_lines):
            first = parse_gedcom_line(block[0])
            if first.tag != "FAMC" or not first.value.strip():
                continue
            pedigree = ""
            for line in block[1:]:
                child = parse_gedcom_line(line)
                if child.tag == "PEDI":
                    pedigree = child.value.strip().casefold()
                    break
            if pedigree:
                pedigree_by_person_family[(person.pointer, first.value.strip())] = pedigree

    for family in source_records:
        if family.tag != "FAM":
            continue
        partner_pointers: list[str] = []
        child_pointers: list[str] = []
        family_facts: list[GenealogicalFact] = []
        for block in _top_level_blocks(family.lines):
            first = parse_gedcom_line(block[0])
            pointers = XREF_RE.findall(first.value)
            if first.tag in {"HUSB", "WIFE"}:
                partner_pointers.extend(pointers)
            elif first.tag == "CHIL":
                child_pointers.extend(pointers)
            elif first.tag in FAMILY_IDENTITY_FACT_TAGS:
                family_facts.append(_fact_from_block(block))

        known_partners = [
            by_pointer[pointer]
            for pointer in dict.fromkeys(partner_pointers)
            if pointer in by_pointer
        ]
        known_children = [
            by_pointer[pointer]
            for pointer in dict.fromkeys(child_pointers)
            if pointer in by_pointer
        ]
        for person in known_partners:
            partners[person.pointer].extend(
                _relative_identity(other)
                for other in known_partners
                if other.pointer != person.pointer
            )
            children[person.pointer].extend(
                _relative_identity(
                    child,
                    pedigree_by_person_family.get(
                        (child.pointer, family.pointer),
                        "",
                    ),
                )
                for child in known_children
            )
            marriages[person.pointer].extend(family_facts)
        for relationship_child in known_children:
            pedigree = pedigree_by_person_family.get(
                (relationship_child.pointer, family.pointer),
                "",
            )
            parents[relationship_child.pointer].extend(
                _relative_identity(parent, pedigree) for parent in known_partners
            )

    return [
        dataclasses.replace(
            person,
            partners=tuple(dict.fromkeys(partners[person.pointer])),
            parents=tuple(dict.fromkeys(parents[person.pointer])),
            children=tuple(dict.fromkeys(children[person.pointer])),
            marriages=tuple(dict.fromkeys(marriages[person.pointer])),
        )
        for person in people
    ]


def _text_similarity(left: str, right: str) -> float:
    """Return a 0--100 case-insensitive token similarity."""
    a = " ".join(re.findall(r"[\w]+", left.casefold()))
    b = " ".join(re.findall(r"[\w]+", right.casefold()))
    if _rapidfuzz is not None:
        return float(_rapidfuzz.token_sort_ratio(a, b))
    return difflib.SequenceMatcher(None, a, b).ratio() * 100


def _date_similarity(left: str, right: str) -> float:
    """Compare genealogical dates while tolerating qualified/partial values."""
    normalised_left = normalise_gedcom_date(left)
    normalised_right = normalise_gedcom_date(right)
    if normalised_left.casefold() == normalised_right.casefold():
        return 100.0
    left_years = [int(year) for year in re.findall(r"\b\d{4}\b", normalised_left)]
    right_years = [int(year) for year in re.findall(r"\b\d{4}\b", normalised_right)]
    if not left_years or not right_years:
        return 0.0

    def bounds(value: str, years: Sequence[int]) -> tuple[int, int]:
        lower, upper = min(years), max(years)
        if value.startswith(("ABT ", "CAL ", "EST ")):
            return lower - 2, upper + 2
        if value.startswith("BEF "):
            return lower - 5, upper - 1
        if value.startswith("AFT "):
            return lower + 1, upper + 5
        return lower, upper

    left_lower, left_upper = bounds(normalised_left.upper(), left_years)
    right_lower, right_upper = bounds(normalised_right.upper(), right_years)
    if left_upper >= right_lower and right_upper >= left_lower:
        return 90.0
    gap = min(abs(left_upper - right_lower), abs(right_upper - left_lower))
    if gap <= 5:
        return max(0.0, 100.0 - gap * 20.0)
    return 0.0


def _collection_similarity(
    left: Sequence[Any],
    right: Sequence[Any],
    comparator: Callable[[Any, Any], float],
) -> float:
    """Compare evidence sets with one-to-one, completeness-tolerant matching.

    Genealogy sources are asymmetrically complete.  Matching from the smaller
    collection means an extra residence, spouse, or child in the richer source
    does not count as a contradiction.  Each richer-source item can be used at
    most once, preventing two same-named children from both matching one child.
    The greedy assignment uses O(n) auxiliary memory so unusually large
    families cannot create a quadratic score matrix.
    """
    if not left or not right:
        return 0.0
    smaller, larger = (left, right) if len(left) <= len(right) else (right, left)
    available = list(range(len(larger)))
    assigned_scores: list[float] = []
    for item in smaller:
        best_index, best_score = max(
            ((index, comparator(item, larger[index])) for index in available),
            key=lambda candidate: candidate[1],
        )
        available.remove(best_index)
        assigned_scores.append(best_score)
    return sum(assigned_scores) / len(assigned_scores)


def _fact_similarity(left: GenealogicalFact, right: GenealogicalFact) -> float:
    """Compare value, date, place, and country within the same fact type."""
    if left.tag != right.tag:
        return 0.0
    components: list[tuple[float, float]] = []
    if left.value and right.value:
        components.append((_text_similarity(left.value, right.value), 0.30))
    if left.date and right.date:
        components.append((_date_similarity(left.date, right.date), 0.35))
    if left.place and right.place:
        components.append((_text_similarity(left.place, right.place), 0.25))
    if left.effective_country and right.effective_country:
        country_score = 100.0 if left.effective_country == right.effective_country else 0.0
        components.append((country_score, 0.10))
    if not components:
        return 0.0
    total_weight = sum(weight for _, weight in components)
    return sum(score * weight for score, weight in components) / total_weight


def _relative_similarity(
    left: RelativeIdentity,
    right: RelativeIdentity,
) -> float:
    """Compare relative names, life events, places, and pedigree roles."""
    components: list[tuple[float, float]] = []
    left_names = tuple(value for value in (left.name, *left.alternate_names) if value)
    right_names = tuple(value for value in (right.name, *right.alternate_names) if value)
    if left_names and right_names:
        components.append(
            (
                _collection_similarity(left_names, right_names, _text_similarity),
                0.50,
            )
        )
    if left.birth_date and right.birth_date:
        components.append((_date_similarity(left.birth_date, right.birth_date), 0.20))
    if left.death_date and right.death_date:
        components.append((_date_similarity(left.death_date, right.death_date), 0.10))
    if left.birth_place and right.birth_place:
        components.append((_text_similarity(left.birth_place, right.birth_place), 0.10))
    if left.death_place and right.death_place:
        components.append((_text_similarity(left.death_place, right.death_place), 0.05))
    left_birth_country = _country_from_place(left.birth_place)
    right_birth_country = _country_from_place(right.birth_place)
    if left_birth_country and right_birth_country:
        components.append(
            (
                100.0 if left_birth_country == right_birth_country else 0.0,
                0.05,
            )
        )
    if left.relationship and right.relationship:
        relationship_score = (
            100.0 if left.relationship.casefold() == right.relationship.casefold() else 20.0
        )
        components.append((relationship_score, 0.10))
    if not components:
        return 0.0
    total_weight = sum(weight for _, weight in components)
    return sum(score * weight for score, weight in components) / total_weight


def _event_values(
    record: IndividualRecord,
    tag: str,
    attribute: str,
    fallback: str,
) -> tuple[str, ...]:
    """Return every populated event value, with the summary as a fallback."""
    values = tuple(
        str(getattr(fact, attribute))
        for fact in record.facts.get(tag, ())
        if getattr(fact, attribute)
    )
    if values:
        return tuple(dict.fromkeys(values))
    return (fallback,) if fallback else ()


def _country_values(
    record: IndividualRecord,
    tag: str,
    fallback: str,
) -> tuple[str, ...]:
    """Return normalized countries for every alternative event."""
    values = tuple(
        fact.effective_country for fact in record.facts.get(tag, ()) if fact.effective_country
    )
    if values:
        return tuple(dict.fromkeys(values))
    return (fallback,) if fallback else ()


def _event_years(record: IndividualRecord, tag: str, fallback: str) -> set[int]:
    """Return every year represented by an event's source alternatives."""
    values = _event_values(record, tag, "date", fallback)
    return {int(year) for value in values for year in re.findall(r"\b\d{4}\b", value)}


def _sets_are_distant(left: set[int], right: set[int], years: int = 5) -> bool:
    """Return whether every cross-set year pairing exceeds a tolerance."""
    return bool(left and right) and all(
        abs(left_year - right_year) > years for left_year in left for right_year in right
    )


def _other_fact_similarity(
    left: IndividualRecord,
    right: IndividualRecord,
) -> Optional[float]:
    """Aggregate matching standard facts not scored by dedicated components."""
    excluded = {"BIRT", "DEAT", "OCCU", "RESI"}
    common_tags = (left.facts.keys() & right.facts.keys()) - excluded
    scores = [
        _collection_similarity(
            left.facts[tag],
            right.facts[tag],
            _fact_similarity,
        )
        for tag in sorted(common_tags)
        if left.facts[tag] and right.facts[tag]
    ]
    return sum(scores) / len(scores) if scores else None


def assess_similarity(a: IndividualRecord, b: IndividualRecord) -> MatchAssessment:
    """Assess identity using available person and family evidence.

    Missing data is omitted from the denominator rather than assigned a low or
    artificially neutral score.  This protects sparse collateral relatives.
    Strong contradictions lower and cap the result, while extra facts on only
    one record are preserved but do not count against the match.

    Returns:
        A score, the evidence considered, explicit conflicts, and a guarded
        ``automatic_merge_safe`` decision used by merge orchestration.
    """
    components: list[tuple[str, float, float]] = []
    conflicts: list[str] = []

    def add(label: str, score: float, weight: float) -> None:
        components.append((label, max(0.0, min(100.0, score)), weight))

    left_names = tuple(name for name in (a.full_name, *a.alternate_names) if name)
    right_names = tuple(name for name in (b.full_name, *b.alternate_names) if name)
    if left_names and right_names:
        name_score = _collection_similarity(
            left_names,
            right_names,
            _text_similarity,
        )
        add(
            "name",
            name_score,
            30.0,
        )
        if name_score < 55.0:
            conflicts.append("name")

    event_components = (
        (
            "birth date",
            _event_values(a, "BIRT", "date", a.birth_date),
            _event_values(b, "BIRT", "date", b.birth_date),
            _date_similarity,
            12.0,
        ),
        (
            "birth place",
            _event_values(a, "BIRT", "place", a.birth_place),
            _event_values(b, "BIRT", "place", b.birth_place),
            _text_similarity,
            8.0,
        ),
        (
            "birth country",
            _country_values(a, "BIRT", a.birth_country),
            _country_values(b, "BIRT", b.birth_country),
            _text_similarity,
            7.0,
        ),
        (
            "death date",
            _event_values(a, "DEAT", "date", a.death_date),
            _event_values(b, "DEAT", "date", b.death_date),
            _date_similarity,
            8.0,
        ),
        (
            "death place",
            _event_values(a, "DEAT", "place", a.death_place),
            _event_values(b, "DEAT", "place", b.death_place),
            _text_similarity,
            6.0,
        ),
        (
            "death country",
            _country_values(a, "DEAT", a.death_country),
            _country_values(b, "DEAT", b.death_country),
            _text_similarity,
            5.0,
        ),
    )
    for label, left_values, right_values, comparator, weight in event_components:
        if left_values and right_values:
            add(
                label,
                _collection_similarity(
                    left_values,
                    right_values,
                    comparator,
                ),
                weight,
            )

    if a.gender and b.gender:
        gender_match = a.gender.casefold() == b.gender.casefold()
        add("sex", 100.0 if gender_match else 0.0, 4.0)
        if not gender_match:
            conflicts.append("sex")

    fact_collection_components = (
        ("occupation", a.occupations, b.occupations, _fact_similarity, 5.0),
        ("residence", a.residences, b.residences, _fact_similarity, 7.0),
        ("family events", a.marriages, b.marriages, _fact_similarity, 8.0),
    )
    relative_collection_components = (
        ("partners", a.partners, b.partners, _relative_similarity, 12.0),
        ("parents", a.parents, b.parents, _relative_similarity, 10.0),
        ("children", a.children, b.children, _relative_similarity, 8.0),
    )
    collection_scores: dict[str, float] = {}
    for (
        fact_label,
        left_facts,
        right_facts,
        fact_comparator,
        fact_weight,
    ) in fact_collection_components:
        if left_facts and right_facts:
            collection_score = _collection_similarity(
                left_facts,
                right_facts,
                fact_comparator,
            )
            collection_scores[fact_label] = collection_score
            add(fact_label, collection_score, fact_weight)
    for (
        relative_label,
        left_relatives,
        right_relatives,
        relative_comparator,
        relative_weight,
    ) in relative_collection_components:
        if left_relatives and right_relatives:
            collection_score = _collection_similarity(
                left_relatives,
                right_relatives,
                relative_comparator,
            )
            collection_scores[relative_label] = collection_score
            add(relative_label, collection_score, relative_weight)

    other_fact_score = _other_fact_similarity(a, b)
    if other_fact_score is not None:
        add("other standard facts", other_fact_score, 6.0)

    left_birth_years = _event_years(a, "BIRT", a.birth_date)
    right_birth_years = _event_years(b, "BIRT", b.birth_date)
    left_death_years = _event_years(a, "DEAT", a.death_date)
    right_death_years = _event_years(b, "DEAT", b.death_date)
    if _sets_are_distant(left_birth_years, right_birth_years):
        conflicts.append("birth year")
    if _sets_are_distant(left_death_years, right_death_years):
        conflicts.append("death year")
    if left_birth_years and max(left_birth_years) - min(left_birth_years) > 5:
        conflicts.append("birth date alternatives")
    if right_birth_years and max(right_birth_years) - min(right_birth_years) > 5:
        conflicts.append("birth date alternatives")
    if left_death_years and max(left_death_years) - min(left_death_years) > 5:
        conflicts.append("death date alternatives")
    if right_death_years and max(right_death_years) - min(right_death_years) > 5:
        conflicts.append("death date alternatives")

    left_birth_countries = set(a.birth_countries)
    right_birth_countries = set(b.birth_countries)
    left_death_countries = set(a.death_countries)
    right_death_countries = set(b.death_countries)
    if (
        left_birth_countries
        and right_birth_countries
        and left_birth_countries.isdisjoint(right_birth_countries)
    ):
        conflicts.append("birth country")
    if (
        left_death_countries
        and right_death_countries
        and left_death_countries.isdisjoint(right_death_countries)
    ):
        conflicts.append("death country")
    if len(left_birth_countries) > 1 or len(right_birth_countries) > 1:
        conflicts.append("birth country alternatives")
    if len(left_death_countries) > 1 or len(right_death_countries) > 1:
        conflicts.append("death country alternatives")
    if collection_scores.get("partners", 100.0) < 50.0:
        conflicts.append("partners")
    if collection_scores.get("parents", 100.0) < 45.0:
        conflicts.append("parents")
    if (
        len(a.children) >= 2
        and len(b.children) >= 2
        and collection_scores.get("children", 100.0) < 45.0
    ):
        conflicts.append("children")

    evidence_weight = sum(weight for _, _, weight in components)
    if evidence_weight:
        score = (
            sum(component_score * weight for _, component_score, weight in components)
            / evidence_weight
        )
    else:
        score = 0.0
    if len(components) == 1:
        score = min(score, 88.0)
    elif evidence_weight < 45.0:
        score = min(score, 94.0)
    if conflicts:
        score = min(score - min(36.0, 12.0 * len(set(conflicts))), 84.0)
    score = round(max(0.0, min(100.0, score)), 2)
    return MatchAssessment(
        score=score,
        evidence_weight=evidence_weight,
        compared_fields=tuple(label for label, _, _ in components),
        conflicts=tuple(dict.fromkeys(conflicts)),
    )


def similarity_score(a: IndividualRecord, b: IndividualRecord) -> float:
    """Return the evidence-aware identity score in the range 0--100."""
    return assess_similarity(a, b).score


def _normalised_key_text(value: str, limit: int = 0) -> str:
    """Return one reusable ASCII comparison key, optionally length-bounded."""
    normalised = re.sub(r"[^a-z0-9]", "", value.casefold())
    return normalised[:limit] if limit else normalised


@dataclass(frozen=True, slots=True)
class _DuplicateProfile:
    """Cached normalized evidence used only by the blocking planner."""

    source_file: str
    blocking_keys: tuple[tuple[str, ...], ...]


def duplicate_profile(record: IndividualRecord) -> _DuplicateProfile:
    """Normalize person, event, place, and relationship keys exactly once."""
    cancellation_checkpoint()
    year = record.birth_year
    year_bucket = str(year // 5) if year is not None else "?"
    gender = _normalised_key_text(record.gender) or "?"
    keys: set[tuple[str, ...]] = set()
    names = tuple(name for name in (record.full_name, *record.alternate_names) if name)
    normalized_name_parts: list[tuple[str, str, str]] = []
    for display_name in names:
        given_name, surname = _name_parts(display_name)
        normalised_surname = _normalised_key_text(surname)
        normalised_given = _normalised_key_text(given_name)
        normalised_full = normalised_given + normalised_surname
        normalized_name_parts.append(
            (
                normalised_given,
                normalised_surname,
                normalised_full,
            )
        )
        surname_initial = normalised_surname[:1] or "?"
        given_initial = normalised_given[:1] or "?"
        # Broad keys retain recall for spelling variants when their observed
        # frequency is safe.  Exact and compound keys refine common buckets.
        keys.add(("sn", surname_initial, "gn", given_initial, "y", year_bucket))
        keys.add(("sn", surname_initial, "y", year_bucket))
        keys.add(("gn", given_initial, "y", year_bucket))
        keys.add(("name", surname_initial, given_initial))
        if normalised_full:
            keys.add(("name-exact", normalised_full))
        if normalised_surname and year is not None:
            keys.add(("name-birth", normalised_surname[:8], str(year), gender))

    event_keys: dict[str, tuple[str, str, tuple[str, ...]]] = {}
    for label, date_value, place, countries in (
        ("birth", record.birth_date, record.birth_place, record.birth_countries),
        ("death", record.death_date, record.death_place, record.death_countries),
    ):
        event_year = _extract_year(date_value)
        normalised_place = _normalised_key_text(place, 18)
        normalized_countries = tuple(_normalised_key_text(country) for country in countries)
        event_keys[label] = (
            str(event_year) if event_year is not None else "",
            normalised_place,
            normalized_countries,
        )
        if event_year is not None:
            keys.add((label, "year", str(event_year)))
            for country in normalized_countries:
                keys.add((label, "year-country", str(event_year), country))
        if normalised_place:
            keys.add((label, "place", normalised_place))

    birth_year, birth_place, birth_countries = event_keys["birth"]
    death_year, death_place, death_countries = event_keys["death"]
    if birth_year and death_year:
        keys.add(("life", birth_year, death_year, gender))
    if birth_year and birth_countries:
        keys.add(("birth-refined", birth_year, birth_countries[0], gender))
    if death_year and death_countries:
        keys.add(("death-refined", death_year, death_countries[0], gender))
    if birth_place and normalized_name_parts:
        keys.add(("birth-place-name", birth_place, normalized_name_parts[0][1][:8]))
    if death_place and normalized_name_parts:
        keys.add(("death-place-name", death_place, normalized_name_parts[0][1][:8]))

    for role, relatives in (
        ("partner", record.partners),
        ("parent", record.parents),
        ("child", record.children),
    ):
        # Relationship context is corroborating evidence, not permission for
        # one unusually large family to grow the index without a hard bound.
        for relative in relatives[:MAX_DUPLICATE_RELATIVES_PER_ROLE]:
            relative_name = _normalised_key_text(relative.name, 18)
            if relative_name:
                keys.add((role, relative_name))
                relative_year = _extract_year(relative.birth_date)
                if relative_year is not None:
                    keys.add((role, relative_name[:12], str(relative_year)))
    return _DuplicateProfile(
        source_file=record.source_file,
        blocking_keys=tuple(sorted(keys)),
    )


def _blocking_keys(record: IndividualRecord) -> set[tuple[str, ...]]:
    """Return cached-profile keys for compatibility with quality helpers."""
    return set(duplicate_profile(record).blocking_keys)


def _blocking_frequencies(
    profiles: Sequence[_DuplicateProfile],
) -> dict[tuple[str, ...], int]:
    """Count each normalized key without retaining another person index."""
    frequencies: dict[tuple[str, ...], int] = defaultdict(int)
    for profile in profiles:
        cancellation_checkpoint()
        for key in profile.blocking_keys:
            frequencies[key] += 1
    return dict(frequencies)


def _bounded_blocking_buckets(
    profiles: Sequence[_DuplicateProfile],
    frequencies: Mapping[tuple[str, ...], int],
    limits: DuplicateSearchLimits,
) -> dict[tuple[str, ...], list[int]]:
    """Index only buckets whose observed frequency is safe to expand.

    Broad initial/year buckets are useful only while small.  Exact names,
    combined life years, countries, places, and relationships provide refined
    routes around an oversized broad bucket.  If every available key remains
    oversized, no pair is inferred from weak evidence alone.
    """
    buckets: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for index, profile in enumerate(profiles):
        cancellation_checkpoint()
        for key in profile.blocking_keys:
            if frequencies[key] <= limits.max_bucket_size:
                buckets[key].append(index)
    return dict(buckets)


def _raw_pair_upper_bound(
    profiles: Sequence[_DuplicateProfile],
    cross_source_only: bool,
) -> int:
    """Return a content-free upper bound before overlapping-key deduplication."""
    totals: dict[tuple[str, ...], int] = defaultdict(int)
    source_totals: dict[tuple[str, ...], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for profile in profiles:
        cancellation_checkpoint()
        for key in profile.blocking_keys:
            totals[key] += 1
            source_totals[key][profile.source_file] += 1
    result = 0
    for key, total in totals.items():
        pairs = total * (total - 1) // 2
        if cross_source_only:
            pairs -= sum(count * (count - 1) // 2 for count in source_totals[key].values())
        result += pairs
    return result


def estimate_duplicate_search(
    records: Sequence[IndividualRecord],
    limits: Optional[DuplicateSearchLimits] = None,
    *,
    cross_source_only: bool = True,
) -> DuplicateSearchPlan:
    """Estimate bounded duplicate work without scoring or exposing genealogy."""
    effective_limits = limits or DuplicateSearchLimits()
    profiles = tuple(duplicate_profile(record) for record in records)
    frequencies = _blocking_frequencies(profiles)
    raw_pairs = _raw_pair_upper_bound(profiles, cross_source_only)
    per_person_pair_bound = len(records) * effective_limits.max_pairs_per_person // 2
    scored_upper = min(raw_pairs, effective_limits.max_scored_pairs, per_person_pair_bound)
    candidate_upper = min(scored_upper, effective_limits.max_candidates)
    per_person_adjudication_bound = (
        len(records) * effective_limits.max_adjudications_per_person // 2
    )
    adjudication_upper = min(
        candidate_upper,
        effective_limits.max_adjudications,
        per_person_adjudication_bound,
    )
    return DuplicateSearchPlan(
        record_count=len(records),
        blocking_key_count=len(frequencies),
        bounded_bucket_count=sum(
            frequency <= effective_limits.max_bucket_size for frequency in frequencies.values()
        ),
        oversized_bucket_count=sum(
            frequency > effective_limits.max_bucket_size for frequency in frequencies.values()
        ),
        raw_pair_upper_bound=raw_pairs,
        scored_pair_upper_bound=scored_upper,
        candidate_count_upper_bound=candidate_upper,
        adjudication_count_range=(0, adjudication_upper),
        prompt_token_range=(0, adjudication_upper * MAX_DEDUP_PROMPT_TOKENS),
        oversized_bucket_policy=(
            "skip pairwise expansion; use bounded exact life event place and relationship keys"
        ),
    )


def bounded_candidate_pairs(
    profiles: Sequence[_DuplicateProfile],
    limits: DuplicateSearchLimits,
    *,
    cross_source_only: bool,
) -> Iterator[tuple[int, int]]:
    """Yield strongest deterministic pairs without a global pair set.

    Buckets are processed rarest first, so exact and compound keys across the
    complete job receive priority over common initials.  A pair is emitted only
    by its single rarest shared key; this removes overlap without materializing
    every pair globally.
    """
    frequencies = _blocking_frequencies(profiles)
    buckets = _bounded_blocking_buckets(profiles, frequencies, limits)
    eligible_keys = tuple(
        frozenset(key for key in profile.blocking_keys if key in buckets) for profile in profiles
    )
    pair_counts = [0] * len(profiles)
    scored_pairs = 0
    for key in sorted(buckets, key=lambda candidate: (frequencies[candidate], candidate)):
        cancellation_checkpoint()
        indexes = buckets[key]
        for position, left in enumerate(indexes):
            if pair_counts[left] >= limits.max_pairs_per_person:
                continue
            for right in indexes[position + 1 :]:
                cancellation_checkpoint()
                if scored_pairs >= limits.max_scored_pairs:
                    return
                if pair_counts[left] >= limits.max_pairs_per_person:
                    break
                if pair_counts[right] >= limits.max_pairs_per_person:
                    continue
                if cross_source_only and profiles[left].source_file == profiles[right].source_file:
                    continue
                owner = min(
                    eligible_keys[left].intersection(eligible_keys[right]),
                    key=lambda candidate: (frequencies[candidate], candidate),
                )
                if owner != key:
                    continue
                pair_counts[left] += 1
                pair_counts[right] += 1
                scored_pairs += 1
                yield left, right


def find_duplicate_candidates(
    records: list[IndividualRecord],
    threshold: int = DEFAULT_SIMILARITY_THRESHOLD,
    *,
    limits: Optional[DuplicateSearchLimits] = None,
) -> list[tuple[int, int, float]]:
    """Find cross-file candidates with bounded frequency-aware blocking."""
    if not 0 <= threshold <= 100:
        raise ValueError("similarity threshold must be between 0 and 100")
    effective_limits = limits or DuplicateSearchLimits()
    profiles = tuple(duplicate_profile(record) for record in records)
    candidates: list[tuple[float, int, int]] = []
    for left, right in bounded_candidate_pairs(
        profiles,
        effective_limits,
        cross_source_only=True,
    ):
        cancellation_checkpoint()
        score = similarity_score(records[left], records[right])
        if score >= threshold:
            ranked_candidate = (score, -left, -right)
            if len(candidates) < effective_limits.max_candidates:
                heapq.heappush(candidates, ranked_candidate)
            elif ranked_candidate > candidates[0]:
                heapq.heapreplace(candidates, ranked_candidate)
    return [
        (-left, -right, score)
        for score, left, right in sorted(
            candidates, key=lambda item: (-item[0], -item[1], -item[2])
        )
    ]


def _build_dedup_prompt(a: IndividualRecord, b: IndividualRecord) -> str:
    """Build a bounded JSON-only prompt for an AI adjudicator."""
    return (
        "You are adjudicating two genealogy records. Decide whether they are "
        "the same real person. Dates may be approximate and names may be "
        "transliterated. Never infer identity from a name alone. Return only "
        "valid JSON. A preferred value is optional and cannot delete the other "
        "value; the merge tool retains every source fact.\n\n"
        f"A: {a.summary()[:MAX_AI_TEXT]}\n"
        f"B: {b.summary()[:MAX_AI_TEXT]}\n\n"
        '{"is_duplicate":true,"confidence":0.0,"reasoning":"...",'
        '"preferred_values":{"given_name":"","surname":"",'
        '"birth_date":"","birth_place":"","death_date":"",'
        '"death_place":"","gender":""}}'
    )


def _dedup_response_schema() -> dict[str, object]:
    """Return one strict schema shared by every structured-output backend."""
    fact_fields = (
        "given_name",
        "surname",
        "birth_date",
        "birth_place",
        "death_date",
        "death_place",
        "gender",
    )
    return {
        "type": "object",
        "properties": {
            "is_duplicate": {"type": "boolean"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reasoning": {"type": "string"},
            "preferred_values": {
                "type": "object",
                "properties": {name: {"type": "string"} for name in fact_fields},
                "required": list(fact_fields),
                "additionalProperties": False,
            },
        },
        "required": [
            "is_duplicate",
            "confidence",
            "reasoning",
            "preferred_values",
        ],
        "additionalProperties": False,
    }


def resolve_duplicate(
    a: IndividualRecord,
    b: IndividualRecord,
    *,
    resolver: IdentityResolver | None = None,
) -> dict[str, object]:
    """Resolve one identity pair through an injected provider-neutral contract."""
    if resolver is None:
        return {
            "is_duplicate": False,
            "confidence": 0.0,
            "reasoning": "LLM adjudication disabled",
            "preferred_values": {},
            "_provider": "none",
            "_model": "",
        }
    return dict(resolver(a, b))


def _field_value(record: IndividualRecord, field_name: str) -> str:
    """Read a mergeable summary field by name."""
    return str(getattr(record, field_name, ""))


def _confidence_value(verdict: Mapping[str, object]) -> float:
    """Return a numeric confidence while rejecting non-scalar provider data."""
    value = verdict.get("confidence", 0.0)
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return 0.0
    try:
        confidence = float(value)
    except (OverflowError, ValueError):
        return 0.0
    return confidence if math.isfinite(confidence) and 0.0 <= confidence <= 1.0 else 0.0


def merge_two_records(
    primary: IndividualRecord,
    secondary: IndividualRecord,
    ai_verdict: Optional[dict[str, object]] = None,
) -> IndividualRecord:
    """Merge summaries and complete raw blocks without deleting conflicts.

    Args:
        primary: Survivor whose pointer and source ordering are retained.
        secondary: Duplicate whose unique facts and relationships are appended.
        ai_verdict: Optional preferred summary values.  A suggestion is honored
            only when it exactly equals a value found on one input record.

    Returns:
        A new record containing the union of facts, names, family references,
        relative context, extra fields, and original level-one blocks.
    """
    preferred = (ai_verdict or {}).get("preferred_values", {})
    preferred = preferred if isinstance(preferred, dict) else {}

    def choose(field_name: str) -> str:
        first = _field_value(primary, field_name)
        second = _field_value(secondary, field_name)
        suggested = str(preferred.get(field_name, "")).strip()
        if suggested and suggested in {first, second}:
            return suggested
        if not first:
            return second
        if field_name in {"birth_date", "death_date"} and second:
            first_date = normalise_gedcom_date(first)
            second_date = normalise_gedcom_date(second)
            return first_date if len(first_date) >= len(second_date) else second_date
        if field_name in {"birth_place", "death_place"} and second:
            first_parts = set(re.findall(r"[\w]+", first.casefold()))
            second_parts = set(re.findall(r"[\w]+", second.casefold()))
            if first_parts < second_parts:
                return second
        return first

    merged_extra = {tag: list(values) for tag, values in primary.extra_fields.items()}
    for tag, extra_values in secondary.extra_fields.items():
        target = merged_extra.setdefault(tag, [])
        target.extend(value for value in extra_values if value not in target)
    merged_facts: dict[str, tuple[GenealogicalFact, ...]] = {
        tag: tuple(values) for tag, values in primary.facts.items()
    }
    for tag, fact_values in secondary.facts.items():
        merged_facts[tag] = tuple(dict.fromkeys(merged_facts.get(tag, ()) + tuple(fact_values)))
    first_lines = primary.raw_lines or _record_to_gedcom_lines(primary).rstrip("\n").splitlines()
    second_lines = (
        secondary.raw_lines or _record_to_gedcom_lines(secondary).rstrip("\n").splitlines()
    )
    merged_lines = list(first_lines)
    for block in _top_level_blocks(second_lines):
        # Keep the source block even when it is byte-for-byte identical.  A
        # duplicate fact is harmless and preserving it keeps an audit trail;
        # the summary/extra_fields view still de-duplicates exact values for
        # callers that need a compact display.
        merged_lines.extend(block)
    return dataclasses.replace(
        primary,
        given_name=choose("given_name"),
        surname=choose("surname"),
        birth_date=choose("birth_date"),
        birth_place=choose("birth_place"),
        death_date=choose("death_date"),
        death_place=choose("death_place"),
        gender=choose("gender"),
        family_links=tuple(dict.fromkeys(primary.family_links + secondary.family_links)),
        family_references=tuple(
            dict.fromkeys(primary.family_references + secondary.family_references)
        ),
        alternate_names=tuple(
            dict.fromkeys(
                primary.alternate_names
                + secondary.alternate_names
                + (
                    (secondary.full_name,)
                    if secondary.full_name and secondary.full_name != primary.full_name
                    else ()
                )
            )
        ),
        names=tuple(dict.fromkeys(primary.names + secondary.names)),
        source_files=tuple(
            dict.fromkeys(
                (primary.source_files or (primary.source_file,))
                + (secondary.source_files or (secondary.source_file,))
            )
        ),
        facts=merged_facts,
        marriages=tuple(dict.fromkeys(primary.marriages + secondary.marriages)),
        partners=tuple(dict.fromkeys(primary.partners + secondary.partners)),
        parents=tuple(dict.fromkeys(primary.parents + secondary.parents)),
        children=tuple(dict.fromkeys(primary.children + secondary.children)),
        extra_fields=merged_extra,
        raw_lines=merged_lines,
    )


def _resolve_duplicate_decision(
    left: IndividualRecord,
    right: IndividualRecord,
    decision: DuplicateDecision | None,
) -> bool:
    """Invoke an injected decision callback and conservatively fail closed."""
    if decision is None:
        return False
    try:
        return decision(left, right) is True
    except (CancellationError, EOFError, KeyboardInterrupt):
        return False


def _get_ai_verdict(
    a: IndividualRecord,
    b: IndividualRecord,
    resolver: IdentityResolver | None,
) -> dict[str, object]:
    """Resolve a bounded pair, allowing stable service errors to propagate."""
    return resolve_duplicate(a, b, resolver=resolver)


def merge_records(
    all_records: list[IndividualRecord],
    threshold: int = DEFAULT_SIMILARITY_THRESHOLD,
    auto: bool = False,
    identity_resolver: IdentityResolver | None = None,
    pointer_map: Optional[dict[str, str]] = None,
    decisions: Optional[list[MergeDecision]] = None,
    duplicate_limits: Optional[DuplicateSearchLimits] = None,
    duplicate_decision: DuplicateDecision | None = None,
) -> list[IndividualRecord]:
    """Merge candidate people while retaining every source fact and family edge.

    Deterministic merging requires at least three independent comparable fields,
    sufficient evidence weight, an identity anchor, and no hard conflict.  All
    other candidates use the configured adjudicator and fail closed if it is
    unavailable.  Merging parents never removes their child/family records;
    ``pointer_map`` redirects every retained family edge to the survivor.

    Args:
        all_records: Globally namespaced, relationship-enriched people.
        threshold: Minimum composite score considered for adjudication.
        auto: Skip low-confidence operator confirmation when true.
        identity_resolver: Optional provider-neutral application-service callback.
        pointer_map: Optional mutable map populated with survivor pointers.
        decisions: Optional mutable audit sink.  Entries describe considered
            pairs but do not affect merge behavior.
        duplicate_limits: Candidate and adjudication budgets.  Defaults are
            deliberately bounded for production-sized common-name groups.
        duplicate_decision: Optional transport-owned callback for low-confidence
            matches. Missing, cancelled, interrupted, and EOF decisions retain
            both people.

    Returns:
        Canonical people in stable source order.

    Raises:
        ValueError: The threshold is outside 0 through 100.
    """
    if not 0 <= threshold <= 100:
        raise ValueError("similarity threshold must be between 0 and 100")
    limits = duplicate_limits or DuplicateSearchLimits()
    by_pointer = {record.pointer: record for record in all_records}
    parent = {record.pointer: record.pointer for record in all_records}
    cluster_members = {record.pointer: [record] for record in all_records}
    adjudications_by_root: dict[str, int] = defaultdict(int)
    adjudication_count = 0

    def find(pointer: str) -> str:
        while parent[pointer] != pointer:
            parent[pointer] = parent[parent[pointer]]
            pointer = parent[pointer]
        return pointer

    candidates = find_duplicate_candidates(all_records, threshold, limits=limits)
    log.info("Found %d candidate pairs", len(candidates))
    for left, right, score in candidates:
        cancellation_checkpoint()
        root_left = find(all_records[left].pointer)
        root_right = find(all_records[right].pointer)
        if root_left == root_right:
            continue
        if len(cluster_members[root_left]) > 1 or len(cluster_members[root_right]) > 1:
            pairwise_conflicts = {
                conflict
                for left_member in cluster_members[root_left]
                for right_member in cluster_members[root_right]
                for conflict in assess_similarity(
                    left_member,
                    right_member,
                ).conflicts
            }
            if pairwise_conflicts:
                log.warning(
                    "Retaining candidate cluster %s/%s; a source member conflicts on %s",
                    root_left,
                    root_right,
                    ", ".join(sorted(pairwise_conflicts)),
                )
                if decisions is not None:
                    decisions.append(
                        MergeDecision(
                            left_pointer=root_left,
                            right_pointer=root_right,
                            score=score,
                            compared_fields=(),
                            conflicts=tuple(sorted(pairwise_conflicts)),
                            disposition="retained-cluster-conflict",
                            reasoning="A member of an existing cluster conflicts.",
                        )
                    )
                continue
        first, second = by_pointer[root_left], by_pointer[root_right]
        verdict: dict[str, object]
        assessment = assess_similarity(first, second)
        hard_conflicts = tuple(
            conflict
            for conflict in assessment.conflicts
            if conflict in DETERMINISTIC_HARD_CONFLICTS
        )
        if hard_conflicts:
            log.info(
                "Retaining candidate %s/%s due to deterministic conflicts: %s",
                root_left,
                root_right,
                ", ".join(hard_conflicts),
            )
            if decisions is not None:
                decisions.append(
                    MergeDecision(
                        left_pointer=root_left,
                        right_pointer=root_right,
                        score=score,
                        compared_fields=assessment.compared_fields,
                        conflicts=hard_conflicts,
                        disposition="retained-hard-conflict",
                        reasoning="Deterministic identity evidence conflicts.",
                    )
                )
            continue
        if assessment.automatic_merge_safe:
            verdict = {
                "is_duplicate": True,
                "confidence": 1.0,
                "reasoning": (
                    "deterministic multi-field evidence: " + ", ".join(assessment.compared_fields)
                ),
                "preferred_values": {},
            }
        else:
            if (
                adjudication_count >= limits.max_adjudications
                or adjudications_by_root[root_left] >= limits.max_adjudications_per_person
                or adjudications_by_root[root_right] >= limits.max_adjudications_per_person
            ):
                log.info(
                    "Retaining candidate %s/%s because the adjudication budget is exhausted",
                    root_left,
                    root_right,
                )
                if decisions is not None:
                    decisions.append(
                        MergeDecision(
                            left_pointer=root_left,
                            right_pointer=root_right,
                            score=score,
                            compared_fields=assessment.compared_fields,
                            conflicts=(),
                            disposition="retained-adjudication-budget",
                            reasoning="Bounded adjudication budget exhausted.",
                        )
                    )
                continue
            adjudication_count += 1
            adjudications_by_root[root_left] += 1
            adjudications_by_root[root_right] += 1
            verdict = _get_ai_verdict(first, second, identity_resolver)
            if verdict.get("_provider"):
                log.info(
                    "AI decision route: %s/%s",
                    verdict.get("_provider"),
                    verdict.get("_model", "unknown"),
                )
            confidence = _confidence_value(verdict)
            if confidence < AI_CONFIDENCE_AUTO_ACCEPT:
                verdict = dict(verdict)
                verdict["is_duplicate"] = (
                    False
                    if auto
                    else _resolve_duplicate_decision(
                        first,
                        second,
                        duplicate_decision,
                    )
                )
            if not bool(verdict.get("is_duplicate", False)):
                if decisions is not None:
                    decisions.append(
                        MergeDecision(
                            left_pointer=root_left,
                            right_pointer=root_right,
                            score=score,
                            compared_fields=assessment.compared_fields,
                            conflicts=assessment.conflicts,
                            disposition="retained",
                            confidence=confidence,
                            provider=str(verdict.get("_provider", "none")),
                            model=str(verdict.get("_model", "")),
                            reasoning=str(verdict.get("reasoning", "")),
                        )
                    )
                continue
        if bool(verdict.get("is_duplicate", False)):
            merged = merge_two_records(first, second, verdict)
            parent[root_right] = root_left
            by_pointer[root_left] = merged
            cluster_members[root_left].extend(cluster_members.pop(root_right))
            adjudications_by_root[root_left] += adjudications_by_root.pop(root_right, 0)
            log.info(
                "Merged %s <- %s (score %.1f)",
                root_left,
                root_right,
                score,
            )
            if decisions is not None:
                decisions.append(
                    MergeDecision(
                        left_pointer=root_left,
                        right_pointer=root_right,
                        score=score,
                        compared_fields=assessment.compared_fields,
                        conflicts=assessment.conflicts,
                        disposition="merged",
                        confidence=_confidence_value(verdict),
                        provider=str(verdict.get("_provider", "deterministic")),
                        model=str(verdict.get("_model", "")),
                        reasoning=str(verdict.get("reasoning", "")),
                    )
                )
        elif decisions is not None:
            decisions.append(
                MergeDecision(
                    left_pointer=root_left,
                    right_pointer=root_right,
                    score=score,
                    compared_fields=assessment.compared_fields,
                    conflicts=assessment.conflicts,
                    disposition="retained-operator",
                    confidence=_confidence_value(verdict),
                    provider=str(verdict.get("_provider", "none")),
                    model=str(verdict.get("_model", "")),
                    reasoning=str(verdict.get("reasoning", "")),
                )
            )
    result: list[IndividualRecord] = []
    seen: set[str] = set()
    for record in all_records:
        root = find(record.pointer)
        if root not in seen:
            result.append(by_pointer[root])
            seen.add(root)
    if pointer_map is not None:
        pointer_map.update({record.pointer: find(record.pointer) for record in all_records})
    log.info("Merge complete: %d -> %d individuals", len(all_records), len(result))
    return result


def _record_to_gedcom_lines(record: IndividualRecord) -> str:
    """Serialize structured individual data when no source block is available.

    The normal CLI writes preserved source blocks.  This fallback supports
    library callers that construct ``IndividualRecord`` objects directly, so it
    must include alternative names, all structured individual facts, and typed
    family references rather than silently reducing a person to vital dates.
    Derived partner/parent/child summaries are not emitted as invented family
    records; callers must provide source ``FAM`` records for those edges.
    """
    lines = [f"0 {record.pointer} INDI\n"]
    if record.names:
        for personal_name in record.names:
            value = personal_name.value or (
                f"{personal_name.given} /{personal_name.surname}/".strip()
                if personal_name.surname
                else personal_name.given
            )
            lines.append(f"1 NAME {value}\n")
            for tag, component in (
                ("TYPE", personal_name.name_type),
                ("NPFX", personal_name.prefix),
                ("GIVN", personal_name.given),
                ("NICK", personal_name.nickname),
                ("SURN", personal_name.surname),
                ("NSFX", personal_name.suffix),
            ):
                if component:
                    lines.append(f"2 {tag} {component}\n")
    else:
        name = (
            f"{record.given_name} /{record.surname}/".strip()
            if record.surname
            else record.given_name
        )
        if name:
            lines.append(f"1 NAME {name}\n")
        for alternate_name in record.alternate_names:
            if alternate_name and alternate_name != record.full_name:
                lines.append(f"1 NAME {alternate_name}\n")
    if record.gender:
        lines.append(f"1 SEX {record.gender}\n")

    def append_fact(fact: GenealogicalFact) -> None:
        value = f" {fact.value}" if fact.value else ""
        lines.append(f"1 {fact.tag}{value}\n")
        if fact.date:
            lines.append(f"2 DATE {fact.date}\n")
        if fact.place:
            lines.append(f"2 PLAC {fact.place}\n")
            if fact.country:
                lines.append(f"3 CTRY {fact.country}\n")
        elif fact.country:
            lines.append(f"2 PLAC {fact.country}\n")

    for tag, date_value, place in (
        ("BIRT", record.birth_date, record.birth_place),
        ("DEAT", record.death_date, record.death_place),
    ):
        facts = record.facts.get(tag, ())
        if facts:
            for fact in facts:
                append_fact(fact)
        elif date_value or place:
            append_fact(GenealogicalFact(tag, date=date_value, place=place))
    for tag, facts in sorted(record.facts.items()):
        if tag in {"BIRT", "DEAT"} or tag in record.extra_fields:
            continue
        for fact in facts:
            append_fact(fact)
    for tag, pointer in record.family_references:
        if tag in {"FAMS", "FAMC"} and pointer:
            lines.append(f"1 {tag} {pointer}\n")
    for values in record.extra_fields.values():
        lines.extend(value if value.endswith("\n") else value + "\n" for value in values)
    return "".join(lines)


build_dedup_prompt = _build_dedup_prompt
collection_similarity = _collection_similarity
confidence_value = _confidence_value
country_from_place = _country_from_place
dedup_response_schema = _dedup_response_schema
fact_similarity = _fact_similarity
individual_from_record = _individual_from_record
relative_similarity = _relative_similarity
serialize_individual_record = _record_to_gedcom_lines

__all__ = [
    "DEFAULT_SIMILARITY_THRESHOLD",
    "DETERMINISTIC_HARD_CONFLICTS",
    "DuplicateSearchLimits",
    "DuplicateSearchPlan",
    "GenealogicalFact",
    "IndividualRecord",
    "MatchAssessment",
    "MergeDecision",
    "PersonalName",
    "RelativeIdentity",
    "assess_similarity",
    "bounded_candidate_pairs",
    "build_dedup_prompt",
    "collection_similarity",
    "confidence_value",
    "country_from_place",
    "dedup_response_schema",
    "duplicate_profile",
    "enrich_relationship_context",
    "estimate_duplicate_search",
    "fact_similarity",
    "find_duplicate_candidates",
    "individual_from_record",
    "merge_records",
    "merge_two_records",
    "normalise_gedcom_date",
    "relative_similarity",
    "resolve_duplicate",
    "serialize_individual_record",
    "similarity_score",
]
