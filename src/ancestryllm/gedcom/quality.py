"""Deterministic, immutable GEDCOM quality analysis and rendering."""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from ancestryllm.gedcom.contracts import QualityResolver

from ancestryllm.core.cancellation import cancellation_checkpoint
from ancestryllm.gedcom.graph import _exact_pointer_references
from ancestryllm.gedcom.identity import (
    GEDCOM_MONTHS,
    MAX_AI_TEXT,
    XREF_RE,
    DuplicateSearchLimits,
    IndividualRecord,
    MatchAssessment,
    MergeDecision,
    _extract_year,
    _fact_from_block,
    _top_level_blocks,
    assess_similarity,
    bounded_candidate_pairs,
    duplicate_profile,
    normalise_gedcom_date,
)
from ancestryllm.gedcom.model import GedcomRecord, ParsedSource, parse_gedcom_line
from ancestryllm.gedcom.serializer import SUPPORTED_GEDCOM_VERSIONS

QUALITY_DUPLICATE_THRESHOLD = 90
QUALITY_AI_LIMIT = 25
QUALITY_SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


@dataclass(frozen=True, slots=True)
class QualityFinding:
    """One deterministic, advisory tree-quality recommendation.

    Findings never mutate a person or family.  ``finding_id`` is stable for
    equivalent evidence, allowing reports to be compared between runs.
    ``ai_why`` and ``ai_research`` are the only model-controlled fields; all
    identity, severity, evidence, targets, and ordering remain deterministic.
    """

    finding_id: str
    code: str
    severity: str
    category: str
    title: str
    description: str
    recommendation: str
    person_pointers: tuple[str, ...] = field(default_factory=tuple)
    family_pointers: tuple[str, ...] = field(default_factory=tuple)
    evidence: tuple[str, ...] = field(default_factory=tuple)
    source_files: tuple[str, ...] = field(default_factory=tuple)
    direct_ancestor: bool = False
    generation: int | None = None
    confidence: str = "deterministic"
    ai_why: str = ""
    ai_research: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class QualityReport:
    """Complete deterministic report input and optional AI annotations.

    The model is immutable so rendering and AI refinement must return a new
    value.  ``ancestor_relationships`` stores pointer, generation, and retained
    ``PEDI`` context for a recursion-free direct-ancestor roster.
    """

    root_pointer: str
    root_name: str
    input_files: tuple[str, ...]
    output_file: str
    findings: tuple[QualityFinding, ...]
    merge_decisions: tuple[MergeDecision, ...] = field(default_factory=tuple)
    ancestor_relationships: tuple[tuple[str, int, str], ...] = field(default_factory=tuple)
    ai_backend: str = "none"
    ai_refined: bool = False
    privacy_status: str = "Local deterministic analysis only"


def _stable_finding_id(
    code: str,
    people: Sequence[str] = (),
    families: Sequence[str] = (),
    evidence: Sequence[str] = (),
    source_files: Sequence[str] = (),
) -> str:
    """Return a short stable identifier derived only from deterministic data."""
    identity = json.dumps(
        [
            code,
            sorted(people),
            sorted(families),
            sorted(evidence),
            sorted(Path(path).name for path in source_files),
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"{code.lower().replace('_', '-')}-{digest}"


class _QualityFindingContext(TypedDict):
    """Shared typed keyword arguments for one person's quality findings."""

    people: Sequence[str]
    source_files: Sequence[str]
    generations: Mapping[str, int]


def _quality_finding(
    code: str,
    severity: str,
    category: str,
    title: str,
    description: str,
    recommendation: str,
    *,
    people: Sequence[str] = (),
    families: Sequence[str] = (),
    evidence: Sequence[str] = (),
    source_files: Sequence[str] = (),
    generations: Mapping[str, int] | None = None,
    confidence: str = "deterministic",
) -> QualityFinding:
    """Construct a validated finding with ancestry priority metadata."""
    if severity not in QUALITY_SEVERITY_ORDER:
        raise ValueError(f"Unknown quality severity: {severity}")
    generation_values = [
        generations[pointer]
        for pointer in people
        if generations is not None and pointer in generations
    ]
    generation = min(generation_values) if generation_values else None
    return QualityFinding(
        finding_id=_stable_finding_id(code, people, families, evidence, source_files),
        code=code,
        severity=severity,
        category=category,
        title=title,
        description=description,
        recommendation=recommendation,
        person_pointers=tuple(dict.fromkeys(people)),
        family_pointers=tuple(dict.fromkeys(families)),
        evidence=tuple(dict.fromkeys(evidence)),
        source_files=tuple(dict.fromkeys(source_files)),
        direct_ancestor=bool(generation_values),
        generation=generation,
        confidence=confidence,
    )


def _actionability_rank(finding: QualityFinding) -> int:
    """Rank concrete repairs before open-ended research at equal priority."""
    immediate = {
        "ANCESTRY_CYCLE",
        "BIRTH_AFTER_DEATH",
        "DANGLING_REFERENCE",
        "DUPLICATE_HEAD",
        "DUPLICATE_TRLR",
        "EMPTY_FAMILY",
        "INVALID_DATE",
        "INVALID_MARRIAGE_DATE",
        "LEVEL_SKIP",
        "MARRIAGE_AFTER_DEATH",
        "MISSING_HEAD",
        "MISSING_TRLR",
        "NONRECIPROCAL_FAMILY_REFERENCE",
        "NONRECIPROCAL_PERSON_REFERENCE",
    }
    manual_review = {
        "ALTERNATIVE_VITAL_EVENTS",
        "POSSIBLE_DUPLICATE",
        "POSSIBLE_MARRIED_PRIMARY_NAME",
    }
    if finding.code in immediate:
        return 0
    if finding.code in manual_review:
        return 2
    return 1


def _canonical_pointer(
    pointer: str,
    pointer_map: Mapping[str, str],
) -> str:
    """Follow a duplicate pointer map defensively without looping forever."""
    seen: set[str] = set()
    while pointer in pointer_map and pointer_map[pointer] != pointer:
        if pointer in seen:
            break
        seen.add(pointer)
        pointer = pointer_map[pointer]
    return pointer


def _family_graph(
    source_records: Iterable[GedcomRecord],
    pointer_map: Mapping[str, str],
) -> tuple[
    dict[str, set[str]],
    dict[str, set[str]],
    dict[str, set[str]],
    dict[str, dict[str, tuple[str, ...]]],
]:
    """Build parent, child, spouse, and family-role maps from retained FAMs."""
    parents: dict[str, set[str]] = defaultdict(set)
    children: dict[str, set[str]] = defaultdict(set)
    spouses: dict[str, set[str]] = defaultdict(set)
    families: dict[str, dict[str, tuple[str, ...]]] = {}
    for record in source_records:
        if record.tag != "FAM":
            continue
        roles: dict[str, list[str]] = defaultdict(list)
        for block in _top_level_blocks(record.lines):
            first = parse_gedcom_line(block[0])
            if first.tag in {"HUSB", "WIFE", "CHIL"}:
                roles[first.tag].extend(XREF_RE.findall(first.value))
        canonical_roles = {
            role: tuple(
                dict.fromkeys(_canonical_pointer(pointer, pointer_map) for pointer in pointers)
            )
            for role, pointers in roles.items()
        }
        families[record.pointer] = canonical_roles
        parent_people = set(canonical_roles.get("HUSB", ()) + canonical_roles.get("WIFE", ()))
        child_people = set(canonical_roles.get("CHIL", ()))
        for child in child_people:
            parents[child].update(parent_people)
        for parent in parent_people:
            children[parent].update(child_people)
        for left in parent_people:
            spouses[left].update(parent_people - {left})
    return parents, children, spouses, families


def ancestor_generations(
    root_pointer: str,
    source_records: Iterable[GedcomRecord],
    pointer_map: Mapping[str, str] | None = None,
) -> tuple[dict[str, int], set[str]]:
    """Traverse direct ancestors iteratively and report cycle participants.

    Args:
        root_pointer: Canonical person xref at generation zero.
        source_records: Retained source records containing family structures.
        pointer_map: Optional duplicate-to-survivor mapping.

    Returns:
        A pointer-to-generation map and pointers encountered through an
        ancestry cycle.  The traversal is iterative, so malformed deep trees
        cannot exhaust Python's call stack.

    Mutation guarantees:
        Inputs are never modified.
    """
    mapping = pointer_map or {}
    parents, _, _, _ = _family_graph(source_records, mapping)
    generations = {root_pointer: 0}
    cycles: set[str] = set()
    pending: deque[tuple[str, tuple[str, ...]]] = deque([(root_pointer, (root_pointer,))])
    while pending:
        child, path = pending.popleft()
        next_generation = generations[child] + 1
        for parent in sorted(parents.get(child, ())):
            if parent in path:
                cycles.update((*path[path.index(parent) :], parent))
                continue
            if parent not in generations or next_generation < generations[parent]:
                generations[parent] = next_generation
                pending.append((parent, (*path, parent)))
    return generations, cycles


def _valid_quality_date(value: str) -> bool:
    """Return whether a date contains a plausible GEDCOM year expression."""
    if not value:
        return True
    normalized = normalise_gedcom_date(value).upper()
    if not re.search(r"\b\d{3,4}\b", normalized):
        return False
    full = re.search(r"\b(\d{1,2})\s+([A-Z]{3})\s+(\d{3,4})\b", normalized)
    if not full:
        return True
    day, month, year = full.groups()
    try:
        dt.date(int(year), GEDCOM_MONTHS.index(month) + 1, int(day))
    except (ValueError, IndexError):
        return False
    return True


def _quality_duplicate_pairs(
    people: Sequence[IndividualRecord],
) -> list[tuple[IndividualRecord, IndividualRecord, MatchAssessment]]:
    """Find bounded report-only same-source and cross-source duplicate pairs."""
    profiles = tuple(duplicate_profile(person) for person in people)
    limits = DuplicateSearchLimits()
    results: list[tuple[IndividualRecord, IndividualRecord, MatchAssessment]] = []
    for left, right in bounded_candidate_pairs(
        profiles,
        limits,
        cross_source_only=False,
    ):
        assessment = assess_similarity(people[left], people[right])
        if assessment.score >= QUALITY_DUPLICATE_THRESHOLD:
            results.append((people[left], people[right], assessment))
    return sorted(
        results,
        key=lambda item: (-item[2].score, item[0].pointer, item[1].pointer),
    )


def _has_source_citation(person: IndividualRecord) -> bool:
    """Return whether any preserved individual line contains ``SOUR``."""
    return any(parse_gedcom_line(line).tag == "SOUR" for line in person.raw_lines)


def _record_source_files(person: IndividualRecord) -> tuple[str, ...]:
    """Return complete source provenance, including legacy constructed records."""
    return tuple(
        dict.fromkeys(person.source_files or ((person.source_file,) if person.source_file else ()))
    )


def _analyze_married_names(
    people: Sequence[IndividualRecord],
    spouses: Mapping[str, set[str]],
    parents: Mapping[str, set[str]],
    families: Mapping[str, Mapping[str, tuple[str, ...]]],
    generations: Mapping[str, int],
) -> list[QualityFinding]:
    """Conservatively identify primary names that may be married forms."""
    by_pointer = {person.pointer: person for person in people}
    wife_roles = {pointer for roles in families.values() for pointer in roles.get("WIFE", ())}
    findings: list[QualityFinding] = []
    maiden_types = {"birth", "maiden", "birth name", "maiden name"}
    married_types = {"married", "married name"}
    for person in people:
        primary = next((name for name in person.names if name.is_primary), None)
        if primary is None:
            continue
        maiden_names = [name for name in person.names if name.name_type in maiden_types]
        primary_is_married = primary.name_type in married_types
        evidence: list[str] = [f"primary name: {primary.display_name}"]
        severity = ""
        description = ""
        confidence = "deterministic"
        if primary_is_married and maiden_names:
            severity = "high"
            description = (
                "The primary NAME is typed married even though a separate "
                "birth or maiden NAME is present."
            )
            evidence.append("typed birth/maiden NAME exists")
        elif primary_is_married:
            severity = "high"
            description = (
                "The primary NAME is explicitly TYPE married and no separate "
                "birth or maiden NAME is present."
            )
            evidence.append("TYPE married")
        elif not maiden_names and primary.surname:
            spouse_surnames = {
                by_pointer[pointer].surname.casefold()
                for pointer in spouses.get(person.pointer, ())
                if pointer in by_pointer and by_pointer[pointer].surname
            }
            parent_surnames = {
                by_pointer[pointer].surname.casefold()
                for pointer in parents.get(person.pointer, ())
                if pointer in by_pointer and by_pointer[pointer].surname
            }
            surname = primary.surname.casefold()
            spouse_match = surname in spouse_surnames
            parent_match = surname in parent_surnames
            if spouse_match and parent_surnames and not parent_match:
                if not person.gender and person.pointer in wife_roles:
                    severity = "low"
                    confidence = "low-confidence relationship context"
                elif person.gender == "F":
                    severity = "medium"
                    confidence = "corroborated inference"
                if severity:
                    description = (
                        "The primary surname matches a spouse and differs from "
                        "all known parent surnames; no typed birth/maiden name "
                        "is retained."
                    )
                    evidence.extend(
                        (
                            f"spouse surnames: {', '.join(sorted(spouse_surnames))}",
                            f"parent surnames: {', '.join(sorted(parent_surnames))}",
                        )
                    )
        if not severity:
            continue
        findings.append(
            _quality_finding(
                "POSSIBLE_MARRIED_PRIMARY_NAME",
                severity,
                "married-name",
                "Possible married surname used as primary name",
                description,
                "Verify records, then retain separate GEDCOM NAME structures with "
                "TYPE birth/maiden and TYPE married; do not invent a surname.",
                people=(person.pointer,),
                evidence=evidence,
                source_files=_record_source_files(person),
                generations=generations,
                confidence=confidence,
            )
        )
    return findings


def _analyze_source_structure(
    sources: Sequence[ParsedSource],
    generations: Mapping[str, int],
) -> list[QualityFinding]:
    """Return advisory diagnostics for source headers, references, and lines."""
    findings: list[QualityFinding] = []
    for source in sources:
        records = source.records
        source_name = str(source.path)
        tags = [record.tag for record in records]
        if tags.count("HEAD") > 1:
            findings.append(
                _quality_finding(
                    "DUPLICATE_HEAD",
                    "high",
                    "structural",
                    "Duplicate HEAD records",
                    f"The source contains {tags.count('HEAD')} HEAD records.",
                    "Retain exactly one leading HEAD record.",
                    source_files=(source_name,),
                    generations=generations,
                )
            )
        if tags.count("TRLR") > 1:
            findings.append(
                _quality_finding(
                    "DUPLICATE_TRLR",
                    "high",
                    "structural",
                    "Duplicate TRLR records",
                    f"The source contains {tags.count('TRLR')} TRLR records.",
                    "Retain exactly one final TRLR record.",
                    source_files=(source_name,),
                    generations=generations,
                )
            )
        if not records or tags[0] != "HEAD":
            findings.append(
                _quality_finding(
                    "MISSING_HEAD",
                    "high",
                    "structural",
                    "Missing leading HEAD",
                    "The source does not begin with a HEAD record.",
                    "Export the source again or add a standards-compliant HEAD.",
                    source_files=(source_name,),
                    generations=generations,
                )
            )
        if not records or tags[-1] != "TRLR":
            findings.append(
                _quality_finding(
                    "MISSING_TRLR",
                    "high",
                    "structural",
                    "Missing final TRLR",
                    "The source does not end with a TRLR record.",
                    "Add one final `0 TRLR` record.",
                    source_files=(source_name,),
                    generations=generations,
                )
            )
        heads = [record for record in records if record.tag == "HEAD"]
        head_lines = heads[0].lines if heads else []
        parsed_head = [parse_gedcom_line(line) for line in head_lines]
        charset = next((line.value for line in parsed_head if line.tag == "CHAR"), "")
        versions = [
            parse_gedcom_line(line).value.strip()
            for block in _top_level_blocks(head_lines)
            if parse_gedcom_line(block[0]).tag == "GEDC"
            for line in block[1:]
            if parse_gedcom_line(line).tag == "VERS"
        ]
        if not charset:
            findings.append(
                _quality_finding(
                    "MISSING_CHARSET",
                    "medium",
                    "structural",
                    "Missing charset",
                    "HEAD has no CHAR declaration.",
                    "Declare `1 CHAR UTF-8` for portable output.",
                    source_files=(source_name,),
                    generations=generations,
                )
            )
        elif charset.upper() not in {"UTF-8", "UNICODE", "ANSEL", "ASCII"}:
            findings.append(
                _quality_finding(
                    "PORTABILITY_CHARSET",
                    "medium",
                    "structural",
                    "Potentially nonportable charset",
                    f"HEAD.CHAR is {charset!r}.",
                    "Convert the file to UTF-8 before exchanging it.",
                    evidence=(charset,),
                    source_files=(source_name,),
                    generations=generations,
                )
            )
        if not versions:
            findings.append(
                _quality_finding(
                    "MISSING_VERSION",
                    "medium",
                    "structural",
                    "Missing GEDCOM version",
                    "HEAD.GEDC.VERS was not found.",
                    "Declare GEDCOM 5.5.5 (or intentional 5.5.1 compatibility).",
                    source_files=(source_name,),
                    generations=generations,
                )
            )
        elif versions[0] not in SUPPORTED_GEDCOM_VERSIONS:
            findings.append(
                _quality_finding(
                    "PORTABILITY_VERSION",
                    "medium",
                    "structural",
                    "Potentially unsupported GEDCOM version",
                    f"HEAD.GEDC.VERS is {versions[0]!r}.",
                    "Confirm the source version and test a 5.5.5 or deliberate "
                    "5.5.1 export with the destination importer.",
                    evidence=(versions[0],),
                    source_files=(source_name,),
                    generations=generations,
                )
            )
        pointers = [record.pointer for record in records if record.pointer]
        pointer_counts: dict[str, int] = defaultdict(int)
        for pointer in pointers:
            pointer_counts[pointer] += 1
        findings.extend(
            _quality_finding(
                "DUPLICATE_XREF",
                "high",
                "structural",
                "Duplicate xref",
                f"The source declares {duplicate} more than once.",
                "Assign one unique xref to each level-zero record.",
                evidence=(duplicate,),
                source_files=(source_name,),
                generations=generations,
            )
            for duplicate in sorted(
                pointer for pointer, count in pointer_counts.items() if count > 1
            )
        )
        findings.extend(
            _quality_finding(
                "MALFORMED_XREF",
                "high",
                "structural",
                "Malformed xref",
                f"{pointer!r} is not a portable GEDCOM 5.5.5 xref.",
                "Replace it with a unique letter-led xref of at most 22 characters.",
                evidence=(pointer,),
                source_files=(source_name,),
                generations=generations,
            )
            for pointer in sorted(set(pointers))
            if len(pointer) > 22 or re.fullmatch(r"@[A-Za-z_][A-Za-z0-9_:-]*@", pointer) is None
        )
        declared = {record.pointer for record in records if record.pointer}
        for record in records:
            previous_level = 0
            for line in record.lines:
                parsed = parse_gedcom_line(line)
                if parsed.level > previous_level + 1:
                    findings.append(
                        _quality_finding(
                            "LEVEL_SKIP",
                            "high",
                            "structural",
                            "GEDCOM hierarchy skips a level",
                            f"Record {record.pointer or record.tag} jumps to level "
                            f"{parsed.level} at tag {parsed.tag}.",
                            "Repair the level numbering before importing.",
                            people=((record.pointer,) if record.tag == "INDI" else ()),
                            evidence=(f"level {parsed.level} {parsed.tag}",),
                            source_files=(source_name,),
                            generations=generations,
                        )
                    )
                previous_level = parsed.level
                if len(line.encode("utf-8")) > 255:
                    findings.append(
                        _quality_finding(
                            "LONG_LINE",
                            "medium",
                            "structural",
                            "Line exceeds 255 UTF-8 bytes",
                            f"Record {record.pointer or record.tag} has an overlong line.",
                            "Wrap text with CONC/CONT for broad importer compatibility.",
                            evidence=(f"{parsed.tag}: {len(line.encode('utf-8'))} bytes",),
                            source_files=(source_name,),
                            generations=generations,
                        )
                    )
            references = _exact_pointer_references(record.lines)
            findings.extend(
                _quality_finding(
                    "DANGLING_REFERENCE",
                    "high",
                    "structural",
                    "Dangling GEDCOM reference",
                    f"{record.pointer or record.tag} references undefined {dangling}.",
                    "Restore the referenced record or remove the broken edge.",
                    people=((record.pointer,) if record.tag == "INDI" else ()),
                    families=((record.pointer,) if record.tag == "FAM" else ()),
                    evidence=(dangling,),
                    source_files=(source_name,),
                    generations=generations,
                )
                for dangling in sorted(references - declared)
            )
    return findings


def analyze_quality(
    people: Sequence[IndividualRecord],
    source_records: Sequence[GedcomRecord],
    sources: Sequence[ParsedSource],
    root_pointer: str,
    *,
    pointer_map: Mapping[str, str] | None = None,
    merge_decisions: Sequence[MergeDecision] = (),
    output_file: str = "",
) -> QualityReport:
    """Analyze a merged tree without mutating genealogy or merge decisions.

    The analysis prioritizes direct ancestors but also checks every surviving
    person.  Missing values are never treated as contradictions, and a missing
    death date becomes actionable only when a birth year indicates age 120 or
    older.  Duplicate findings are recommendations, never merge commands.

    Args:
        people: Surviving, relationship-enriched people.
        source_records: Globally namespaced source records.
        sources: Parsed source documents used for structural diagnostics.
        root_pointer: Canonical report root.
        pointer_map: Optional duplicate-to-survivor mappings.
        merge_decisions: Optional audit entries from :func:`merge_records`.
        output_file: Planned merged GEDCOM path shown in the report.

    Returns:
        An immutable deterministic report model.

    Raises:
        ValueError: The root pointer is not a surviving person.

    Privacy effects:
        This function performs no network or filesystem writes.

    Mutation guarantees:
        Input records and relationships are not changed.
    """
    by_pointer = {person.pointer: person for person in people}
    if root_pointer not in by_pointer:
        raise ValueError(f"Quality root person not found: {root_pointer}")
    mapping = pointer_map or {}
    generations, cycles = ancestor_generations(root_pointer, source_records, mapping)
    parents, _children, spouses, families = _family_graph(source_records, mapping)
    findings: list[QualityFinding] = []
    current_year = dt.datetime.now(dt.UTC).year
    for person in people:
        cancellation_checkpoint()
        person_files = _record_source_files(person)
        common: _QualityFindingContext = {
            "people": (person.pointer,),
            "source_files": person_files,
            "generations": generations,
        }
        if not person.full_name:
            findings.append(
                _quality_finding(
                    "MISSING_NAME",
                    "high",
                    "person",
                    "Person has no name",
                    f"{person.pointer} has no usable NAME value.",
                    "Add a sourced NAME or an explicit unknown-name convention.",
                    **common,
                )
            )
        for tag, label in (("BIRT", "birth"), ("DEAT", "death")):
            facts = person.facts.get(tag, ())
            findings.extend(
                _quality_finding(
                    "INVALID_DATE",
                    "high",
                    "chronology",
                    f"Invalid {label} date",
                    f"{person.full_name or person.pointer} has {fact.date!r}.",
                    "Verify the source and encode a valid GEDCOM date.",
                    evidence=(tag, fact.date),
                    **common,
                )
                for fact in facts
                if fact.date and not _valid_quality_date(fact.date)
            )
            distinct = {fact.summary() for fact in facts if fact.summary()}
            if len(distinct) > 1:
                findings.append(
                    _quality_finding(
                        "ALTERNATIVE_VITAL_EVENTS",
                        "medium",
                        "person",
                        f"Multiple {label} events retained",
                        f"{person.full_name or person.pointer} has conflicting or "
                        f"alternative {label} facts.",
                        "Compare citations; retain alternatives until one is disproved.",
                        evidence=tuple(sorted(distinct)),
                        **common,
                    )
                )
        if not person.birth_date:
            findings.append(
                _quality_finding(
                    "MISSING_BIRTH_DATE",
                    "medium",
                    "person",
                    "Missing birth date",
                    f"{person.full_name or person.pointer} has no birth date.",
                    "Research a birth, baptism, census, or age-based estimate.",
                    **common,
                )
            )
        if not person.birth_place:
            findings.append(
                _quality_finding(
                    "MISSING_BIRTH_PLACE",
                    "medium",
                    "person",
                    "Missing birth place",
                    f"{person.full_name or person.pointer} has no birth place.",
                    "Research and cite the smallest defensible jurisdiction.",
                    **common,
                )
            )
        if (
            not person.death_date
            and person.birth_year is not None
            and current_year - person.birth_year >= 120
        ):
            findings.append(
                _quality_finding(
                    "MISSING_DEATH_DATE",
                    "medium",
                    "person",
                    "Likely missing death date",
                    f"Birth year {person.birth_year} implies age at least 120.",
                    "Research death, burial, probate, obituary, or cemetery records.",
                    evidence=(str(person.birth_year),),
                    **common,
                )
            )
        if person.death_date and not person.death_place:
            findings.append(
                _quality_finding(
                    "MISSING_DEATH_PLACE",
                    "low",
                    "person",
                    "Missing death place",
                    f"{person.full_name or person.pointer} has a death date but no place.",
                    "Research a death certificate, obituary, or burial record.",
                    **common,
                )
            )
        if not _has_source_citation(person):
            findings.append(
                _quality_finding(
                    "MISSING_CITATION",
                    "medium",
                    "citation",
                    "No source citation",
                    f"No SOUR structure is retained for {person.full_name or person.pointer}.",
                    "Add citations to the specific facts they support.",
                    **common,
                )
            )
        if not person.family_references:
            findings.append(
                _quality_finding(
                    "MISSING_RELATIONSHIPS",
                    "low",
                    "relationship",
                    "No family relationships",
                    "No FAMC or FAMS edge is retained.",
                    "Confirm whether this person is intentionally unlinked.",
                    **common,
                )
            )
        if not any(tag == "FAMC" for tag, _ in person.family_references):
            findings.append(
                _quality_finding(
                    "MISSING_PARENT_LINK",
                    "medium" if person.pointer == root_pointer else "low",
                    "relationship",
                    "No parent-family link",
                    f"{person.full_name or person.pointer} has no FAMC reference.",
                    "Confirm whether the parents are unknown or link a verified parent family.",
                    **common,
                )
            )
        findings.extend(
            _quality_finding(
                "INCOMPLETE_OCCUPATION",
                "low",
                "person",
                "Incomplete occupation",
                "An OCCU fact has no occupation value.",
                "Add the occupation text and supporting citation.",
                **common,
            )
            for fact in person.occupations
            if not fact.value
        )
        findings.extend(
            _quality_finding(
                "INCOMPLETE_RESIDENCE",
                "low",
                "person",
                "Incomplete residence",
                "A RESI fact is missing a date or place.",
                "Add the known date/place without fabricating precision.",
                evidence=(fact.summary(),),
                **common,
            )
            for fact in person.residences
            if not fact.date or not fact.place
        )
        if (
            person.birth_year is not None
            and person.death_year is not None
            and person.birth_year > person.death_year
        ):
            findings.append(
                _quality_finding(
                    "BIRTH_AFTER_DEATH",
                    "critical",
                    "chronology",
                    "Birth occurs after death",
                    f"Birth {person.birth_year} is after death {person.death_year}.",
                    "Verify both events and their person attribution immediately.",
                    evidence=(str(person.birth_year), str(person.death_year)),
                    **common,
                )
            )
        if (
            person.birth_year is not None
            and person.death_year is not None
            and person.death_year - person.birth_year > 120
        ):
            findings.append(
                _quality_finding(
                    "IMPLAUSIBLE_LIFESPAN",
                    "high",
                    "chronology",
                    "Implausible lifespan",
                    f"The recorded lifespan is {person.death_year - person.birth_year} years.",
                    "Check for transcription errors or combined identities.",
                    **common,
                )
            )

    for child_pointer, parent_pointers in parents.items():
        cancellation_checkpoint()
        child = by_pointer.get(child_pointer)
        if child is None or child.birth_year is None:
            continue
        for parent_pointer in parent_pointers:
            parent = by_pointer.get(parent_pointer)
            if parent is None or parent.birth_year is None:
                continue
            age = child.birth_year - parent.birth_year
            if age < 12 or age > 80:
                findings.append(
                    _quality_finding(
                        "PARENT_CHILD_CHRONOLOGY",
                        "high",
                        "chronology",
                        "Implausible parent-child chronology",
                        f"{parent.full_name or parent.pointer} would be age {age} "
                        f"at {child.full_name or child.pointer}'s birth.",
                        "Verify the relationship and both birth dates.",
                        people=(parent.pointer, child.pointer),
                        evidence=(str(age),),
                        source_files=_record_source_files(parent) + _record_source_files(child),
                        generations=generations,
                    )
                )
    source_by_family = {record.pointer: record for record in source_records if record.tag == "FAM"}
    for family_pointer, roles in families.items():
        cancellation_checkpoint()
        members = set(roles.get("HUSB", ()) + roles.get("WIFE", ()) + roles.get("CHIL", ()))
        family_record = source_by_family.get(family_pointer)
        family_source = (family_record.source_file,) if family_record is not None else ()
        if not members:
            findings.append(
                _quality_finding(
                    "EMPTY_FAMILY",
                    "high",
                    "relationship",
                    "Empty family record",
                    f"{family_pointer} has no HUSB, WIFE, or CHIL members.",
                    "Restore family members or remove the empty family record.",
                    families=(family_pointer,),
                    source_files=family_source,
                    generations=generations,
                )
            )
        for role in ("HUSB", "WIFE", "CHIL"):
            expected_tag = "FAMC" if role == "CHIL" else "FAMS"
            for person_pointer in roles.get(role, ()):
                linked_person = by_pointer.get(person_pointer)
                if linked_person is None:
                    continue
                linked_families = {
                    family for tag, family in linked_person.family_references if tag == expected_tag
                }
                if family_pointer not in linked_families:
                    findings.append(
                        _quality_finding(
                            "NONRECIPROCAL_FAMILY_REFERENCE",
                            "medium",
                            "relationship",
                            "Nonreciprocal family reference",
                            f"{family_pointer} lists {person_pointer} as {role}, but "
                            f"the person has no matching {expected_tag} reference.",
                            "Add the reciprocal person-to-family edge after verification.",
                            people=(person_pointer,),
                            families=(family_pointer,),
                            evidence=(role, expected_tag),
                            source_files=family_source + _record_source_files(linked_person),
                            generations=generations,
                        )
                    )
        if family_record is not None:
            spouse_people = [
                by_pointer[pointer]
                for pointer in roles.get("HUSB", ()) + roles.get("WIFE", ())
                if pointer in by_pointer
            ]
            for block in _top_level_blocks(family_record.lines):
                first = parse_gedcom_line(block[0])
                if first.tag != "MARR":
                    continue
                marriage = _fact_from_block(block)
                marriage_year = _extract_year(marriage.date)
                if marriage.date and not _valid_quality_date(marriage.date):
                    findings.append(
                        _quality_finding(
                            "INVALID_MARRIAGE_DATE",
                            "high",
                            "chronology",
                            "Invalid marriage date",
                            f"{family_pointer} has marriage date {marriage.date!r}.",
                            "Verify and encode a valid GEDCOM marriage date.",
                            families=(family_pointer,),
                            evidence=(marriage.date,),
                            source_files=family_source,
                            generations=generations,
                        )
                    )
                if marriage_year is None:
                    continue
                for spouse in spouse_people:
                    if spouse.birth_year is not None:
                        marriage_age = marriage_year - spouse.birth_year
                        if marriage_age < 12:
                            findings.append(
                                _quality_finding(
                                    "MARRIAGE_BEFORE_MATURITY",
                                    "high",
                                    "chronology",
                                    "Marriage precedes plausible maturity",
                                    f"{spouse.full_name or spouse.pointer} would be "
                                    f"age {marriage_age} at marriage.",
                                    "Verify the marriage, birth date, and family identity.",
                                    people=(spouse.pointer,),
                                    families=(family_pointer,),
                                    evidence=(str(marriage_year), str(marriage_age)),
                                    source_files=family_source + _record_source_files(spouse),
                                    generations=generations,
                                )
                            )
                    if spouse.death_year is not None and marriage_year > spouse.death_year:
                        findings.append(
                            _quality_finding(
                                "MARRIAGE_AFTER_DEATH",
                                "critical",
                                "chronology",
                                "Marriage occurs after death",
                                f"{family_pointer}'s marriage is after "
                                f"{spouse.full_name or spouse.pointer}'s death.",
                                "Verify the marriage, death event, and family identity.",
                                people=(spouse.pointer,),
                                families=(family_pointer,),
                                evidence=(str(marriage_year), str(spouse.death_year)),
                                source_files=family_source + _record_source_files(spouse),
                                generations=generations,
                            )
                        )
    for person in people:
        for tag, family_pointer in person.family_references:
            linked_roles = families.get(family_pointer)
            if linked_roles is None:
                continue
            expected_roles = ("CHIL",) if tag == "FAMC" else ("HUSB", "WIFE")
            listed = any(person.pointer in linked_roles.get(role, ()) for role in expected_roles)
            if not listed:
                findings.append(
                    _quality_finding(
                        "NONRECIPROCAL_PERSON_REFERENCE",
                        "medium",
                        "relationship",
                        "Person points to a family that omits them",
                        f"{person.pointer}.{tag} references {family_pointer}, but the "
                        "family does not list that person in the expected role.",
                        "Verify both records and add only the correct reciprocal edge.",
                        people=(person.pointer,),
                        families=(family_pointer,),
                        evidence=(tag,),
                        source_files=_record_source_files(person),
                        generations=generations,
                    )
                )
    if cycles:
        findings.append(
            _quality_finding(
                "ANCESTRY_CYCLE",
                "critical",
                "relationship",
                "Ancestry cycle detected",
                "A person is reachable as their own direct ancestor.",
                "Inspect parent-family links and remove only the erroneous edge.",
                people=tuple(sorted(cycles)),
                evidence=tuple(sorted(cycles)),
                generations=generations,
            )
        )
    for left, right, assessment in _quality_duplicate_pairs(people):
        evidence = (
            f"score: {assessment.score:.2f}",
            f"compared: {', '.join(assessment.compared_fields)}",
            f"conflicts: {', '.join(assessment.conflicts) or 'none'}",
            f"relatives: {', '.join(left.partner_names + right.partner_names) or 'none'}",
        )
        findings.append(
            _quality_finding(
                "POSSIBLE_DUPLICATE",
                "high",
                "duplicate",
                "High-confidence possible duplicate",
                f"{left.full_name or left.pointer} ({left.pointer}) and "
                f"{right.full_name or right.pointer} ({right.pointer}) score "
                f"{assessment.score:.2f}.",
                "Compare original images, citations, relatives, and conflicting "
                "facts manually; this report never merges the pair.",
                people=(left.pointer, right.pointer),
                evidence=evidence,
                source_files=_record_source_files(left) + _record_source_files(right),
                generations=generations,
            )
        )
    findings.extend(_analyze_married_names(people, spouses, parents, families, generations))
    findings.extend(_analyze_source_structure(sources, generations))
    findings.sort(
        key=lambda finding: (
            QUALITY_SEVERITY_ORDER[finding.severity],
            not finding.direct_ancestor,
            finding.generation if finding.generation is not None else 10_000,
            _actionability_rank(finding),
            finding.finding_id,
        )
    )
    child_parentage: dict[str, dict[str, str]] = defaultdict(dict)
    for person in people:
        for block in _top_level_blocks(person.raw_lines):
            first = parse_gedcom_line(block[0])
            if first.tag != "FAMC":
                continue
            pedi = next(
                (
                    parse_gedcom_line(line).value.strip().casefold()
                    for line in block[1:]
                    if parse_gedcom_line(line).tag == "PEDI"
                ),
                "birth/unspecified",
            )
            family_pointer = first.value.strip()
            if family_pointer:
                child_parentage[person.pointer][family_pointer] = pedi
    ancestor_parentage: dict[str, set[str]] = defaultdict(set)
    for child_pointer, parent_pointers in parents.items():
        child_generation = generations.get(child_pointer)
        if child_generation is None:
            continue
        for family_pointer, roles in families.items():
            if child_pointer not in roles.get("CHIL", ()):
                continue
            relationship = child_parentage.get(child_pointer, {}).get(
                family_pointer, "birth/unspecified"
            )
            family_parents = set(roles.get("HUSB", ()) + roles.get("WIFE", ()))
            for parent_pointer in parent_pointers & family_parents:
                if generations.get(parent_pointer) == child_generation + 1:
                    ancestor_parentage[parent_pointer].add(relationship)
    ancestor_relationships = tuple(
        (
            pointer,
            generation,
            "self"
            if generation == 0
            else ", ".join(sorted(ancestor_parentage.get(pointer, {"birth/unspecified"}))),
        )
        for pointer, generation in sorted(generations.items(), key=lambda item: (item[1], item[0]))
    )
    return QualityReport(
        root_pointer=root_pointer,
        root_name=by_pointer[root_pointer].full_name,
        input_files=tuple(str(source.path) for source in sources),
        output_file=output_file,
        findings=tuple(findings),
        merge_decisions=tuple(merge_decisions),
        ancestor_relationships=ancestor_relationships,
    )


def _markdown(value: object) -> str:
    """Escape text for Markdown tables and collapse untrusted newlines."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )


def _render_findings(findings: Sequence[QualityFinding]) -> list[str]:
    """Render findings in a stable compact Markdown table."""
    if not findings:
        return ["No findings in this section.", ""]
    lines = [
        "| Severity | ID | Person/family | Recommendation |",
        "|---|---|---|---|",
    ]
    for finding in findings:
        targets = ", ".join(finding.person_pointers + finding.family_pointers) or "Tree"
        generation = f"; generation {finding.generation}" if finding.generation is not None else ""
        detail = f"{finding.title}: {finding.description} {finding.recommendation}"
        if finding.evidence:
            detail += f" Evidence: {'; '.join(finding.evidence)}."
        if finding.source_files:
            detail += (
                " Sources: " + ", ".join(Path(path).name for path in finding.source_files) + "."
            )
        if finding.ai_why:
            detail += f" AI context: {finding.ai_why}"
        if finding.ai_research:
            detail += " AI research suggestions: " + "; ".join(finding.ai_research)
        lines.append(
            f"| {_markdown(finding.severity.upper())} | "
            f"`{_markdown(finding.finding_id)}` | {_markdown(targets + generation)} | "
            f"{_markdown(detail)} |"
        )
    lines.append("")
    return lines


def render_quality_report(report: QualityReport) -> str:
    """Render the immutable quality model as deterministic Markdown.

    Args:
        report: Complete report model, optionally carrying bounded AI context.

    Returns:
        UTF-8 Markdown ending in one newline.

    Mutation guarantees:
        The report and its findings are not changed.
    """
    counts: defaultdict[str, int] = defaultdict(int)
    for finding in report.findings:
        counts[finding.severity] += 1
    lines = [
        "# GEDCOM Merge Quality Report",
        "",
        "## Run configuration and privacy status",
        "",
        f"- Quality root: `{_markdown(report.root_pointer)}` "
        f"({_markdown(report.root_name or 'unnamed')})",
        f"- Output GEDCOM: `{_markdown(report.output_file)}`",
        f"- Inputs: {', '.join(f'`{_markdown(path)}`' for path in report.input_files)}",
        f"- Privacy: {_markdown(report.privacy_status)}",
        "- AI refinement: "
        + _markdown(report.ai_backend if report.ai_refined else "disabled or unavailable"),
        "",
        "## Executive summary",
        "",
        f"{len(report.findings)} findings: "
        + ", ".join(f"{counts[level]} {level}" for level in ("critical", "high", "medium", "low"))
        + ". This report is advisory and made no GEDCOM changes.",
        "",
        "## Top 25 actions",
        "",
    ]
    lines.extend(_render_findings(report.findings[:QUALITY_AI_LIMIT]))
    lines.extend(["## Direct ancestors by generation", ""])
    lines.extend(
        [
            "| Generation | Pointer | Parentage (`PEDI`) |",
            "|---:|---|---|",
        ]
    )
    for pointer, generation, relationship in report.ancestor_relationships:
        lines.append(f"| {generation} | `{_markdown(pointer)}` | {_markdown(relationship)} |")
    lines.append("")
    direct = sorted(
        (finding for finding in report.findings if finding.direct_ancestor),
        key=lambda item: (item.generation or 0, item.finding_id),
    )
    lines.extend(_render_findings(direct))
    sections = (
        ("High-confidence possible duplicates", "duplicate"),
        ("Possible married-name-as-primary issues", "married-name"),
        ("General tree quality", "general"),
        ("Source and structural diagnostics", "structural"),
    )
    for title, category in sections:
        lines.extend([f"## {title}", ""])
        selected = (
            [finding for finding in report.findings if finding.category == category]
            if category != "general"
            else [
                finding
                for finding in report.findings
                if finding.category not in {"duplicate", "married-name", "structural"}
            ]
        )
        lines.extend(_render_findings(selected))
    lines.extend(["## Merge decisions", ""])
    if report.merge_decisions:
        lines.extend(
            [
                "| Pair | Score | Disposition | Evidence/conflicts | Route |",
                "|---|---:|---|---|---|",
            ]
        )
        for decision in report.merge_decisions:
            evidence = ", ".join(decision.compared_fields) or "none"
            conflicts = ", ".join(decision.conflicts) or "none"
            route = "/".join(value for value in (decision.provider, decision.model) if value)
            lines.append(
                f"| `{_markdown(decision.left_pointer)}` / "
                f"`{_markdown(decision.right_pointer)}` | {decision.score:.2f} | "
                f"{_markdown(decision.disposition)} | "
                f"{_markdown(evidence)}; conflicts: {_markdown(conflicts)} | "
                f"{_markdown(route)} |"
            )
        lines.append("")
    else:
        lines.extend(["No duplicate merge decisions were required.", ""])
    return "\n".join(lines).rstrip() + "\n"


def _quality_response_schema() -> dict[str, object]:
    """Return the strict, provider-neutral quality annotation schema."""
    return {
        "type": "object",
        "properties": {
            "annotations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "finding_id": {"type": "string"},
                        "why_this_matters": {"type": "string"},
                        "research_suggestions": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "finding_id",
                        "why_this_matters",
                        "research_suggestions",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["annotations"],
        "additionalProperties": False,
    }


def _build_quality_prompt(report: QualityReport) -> str:
    """Build one bounded prompt containing only deterministic top findings."""
    payload = [
        {
            "finding_id": finding.finding_id,
            "severity": finding.severity,
            "title": finding.title[:200],
            "description": finding.description[:400],
            "evidence": [value[:160] for value in finding.evidence[:4]],
            "recommendation": finding.recommendation[:400],
        }
        for finding in report.findings[:QUALITY_AI_LIMIT]
    ]
    return (
        "Explain the deterministic genealogy quality findings below. Return "
        "one annotation per supplied finding ID. You may add only why the "
        "finding matters and cautious research suggestions. Do not change or "
        "question severity, suppress findings, assert identities, invent "
        "people or names, or introduce facts absent from the evidence. Keep "
        "each field concise.\n\n" + json.dumps(payload, ensure_ascii=False)
    )


def _quality_annotations_from_payload(
    value: Mapping[str, object],
    allowed_ids: set[str],
) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Validate advisory fields while ignoring unknown deterministic IDs."""
    raw_annotations = value.get("annotations")
    if not isinstance(raw_annotations, list):
        raise ValueError("Quality annotations must be a list")
    annotations: dict[str, tuple[str, tuple[str, ...]]] = {}
    for item in raw_annotations[:QUALITY_AI_LIMIT]:
        if not isinstance(item, dict):
            raise ValueError("Each quality annotation must be an object")
        finding_id = str(item.get("finding_id", ""))
        if finding_id not in allowed_ids or finding_id in annotations:
            continue
        why = str(item.get("why_this_matters", ""))[:MAX_AI_TEXT].strip()
        raw_suggestions = item.get("research_suggestions", [])
        if not isinstance(raw_suggestions, list):
            raise ValueError("Research suggestions must be a list")
        suggestions = tuple(
            str(suggestion)[:500].strip()
            for suggestion in raw_suggestions[:5]
            if str(suggestion).strip()
        )
        annotations[finding_id] = (why, suggestions)
    return annotations


def refine_quality_report_with_ai(
    report: QualityReport,
    resolver: QualityResolver | None,
) -> QualityReport:
    """Apply bounded provider annotations without changing deterministic authority."""
    if resolver is None or not report.findings:
        return report
    resolution = resolver(report)
    findings = tuple(
        dataclasses.replace(
            finding,
            ai_why=resolution.annotations.get(finding.finding_id, ("", ()))[0],
            ai_research=resolution.annotations.get(finding.finding_id, ("", ()))[1],
        )
        for finding in report.findings
    )
    privacy = (
        f"Bounded top-25 finding summaries sent to {resolution.provider_id}/{resolution.model}"
        if resolution.remote
        else f"Local provider refinement via {resolution.provider_id}/{resolution.model}"
    )
    return dataclasses.replace(
        report,
        findings=findings,
        ai_backend=f"{resolution.provider_id}/{resolution.model}",
        ai_refined=True,
        privacy_status=privacy,
    )


build_quality_prompt = _build_quality_prompt
quality_annotations_from_payload = _quality_annotations_from_payload
quality_duplicate_pairs = _quality_duplicate_pairs
quality_response_schema = _quality_response_schema


def write_quality_report(report: QualityReport, output_path: str | Path) -> Any:
    """Preserve the historical writer import through the serialization façade."""
    from ancestryllm.gedcom.serialization import write_quality_report as _write_quality_report

    return _write_quality_report(report, output_path)


__all__ = [
    "QUALITY_AI_LIMIT",
    "QualityFinding",
    "QualityReport",
    "analyze_quality",
    "ancestor_generations",
    "build_quality_prompt",
    "quality_annotations_from_payload",
    "quality_duplicate_pairs",
    "quality_response_schema",
    "refine_quality_report_with_ai",
    "render_quality_report",
    "write_quality_report",
]
