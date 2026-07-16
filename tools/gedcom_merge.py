"""Loss-minimising GEDCOM merge tool with optional AI adjudication.

The tool intentionally keeps a small, lossless representation of each GEDCOM
record instead of serialising a third-party object model.  ``python-gedcom``
is used as a standards-aware validation/semantic parser when installed, while
the original GEDCOM lines remain the source of truth for output.  This matters
because GEDCOM files commonly contain vendor tags, nested source citations,
media links, notes, and custom facts that a convenient object model may not
round-trip.

AI is a decision aid, not an authority over the data.  An AI response can say
that two people are the same and can suggest which value should be displayed
as the canonical summary value.  The merge still retains both conflicting
facts as separate GEDCOM blocks, so a bad model response cannot destroy
evidence.

Examples::

    python tools/gedcom_merge.py a.ged b.ged -o master.ged --ai-backend none
    python tools/gedcom_merge.py *.ged --ai-backend ollama --auto
    OPENAI_API_KEY=... python tools/gedcom_merge.py a.ged b.ged \
        --ai-backend openai --openai-model gpt-4.1-mini --auto

Recommended installation::

    python3.12 -m venv .venv
    . .venv/bin/activate
    python -m pip install -r requirements.txt

The parser accepts GEDCOM 5.5-style files.  It does not execute anything from
an input file and it never writes credentials to the output.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import difflib
import json
import logging
import os
import re
import tempfile
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

try:
    from rapidfuzz import fuzz as _rapidfuzz
except ImportError:  # pragma: no cover - exercised in minimal installations
    _rapidfuzz = None

log = logging.getLogger(__name__)

GEDCOM_MONTHS: tuple[str, ...] = (
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
)
DATE_QUALIFIERS: dict[str, str] = {
    "about": "ABT", "abt": "ABT", "approximately": "ABT",
    "circa": "ABT", "ca": "ABT", "ca.": "ABT", "c.": "ABT",
    "before": "BEF", "bef": "BEF", "after": "AFT", "aft": "AFT",
    "estimated": "EST", "est": "EST", "calculated": "CAL", "cal": "CAL",
}
DEFAULT_SIMILARITY_THRESHOLD = 78
AI_CONFIDENCE_AUTO_ACCEPT = 0.85
MAX_AI_TEXT = 2_000
XREF_RE = re.compile(r"@[A-Za-z0-9_:-]+@")
SUPPORTED_GEDCOM_VERSIONS = ("5.5.5", "5.5.1")
LINE_RE = re.compile(r"^(?P<level>\d+)(?:\s+(?P<xref>@[^@\s]+@))?\s+"
                     r"(?P<tag>[A-Za-z0-9_]+)(?:\s+(?P<value>.*))?$")


class GedcomParseError(ValueError):
    """Raised when an input line cannot be interpreted as a GEDCOM line."""


@dataclass(frozen=True, slots=True)
class GedcomLine:
    """Parsed metadata for one original GEDCOM line."""

    level: int
    xref: str
    tag: str
    value: str
    raw: str


def parse_gedcom_line(line: str, line_number: int = 0) -> GedcomLine:
    """Parse one GEDCOM line without evaluating its contents."""
    raw = line.rstrip("\r\n").lstrip("\ufeff")
    match = LINE_RE.fullmatch(raw)
    if not match:
        raise GedcomParseError(f"Invalid GEDCOM line {line_number}: {raw!r}")
    level = int(match.group("level"))
    return GedcomLine(
        level=level,
        xref=match.group("xref") or "",
        tag=match.group("tag").upper(),
        value=match.group("value") or "",
        raw=raw,
    )


@dataclass
class GedcomRecord:
    """A complete level-zero record, kept as lines for round-trip fidelity."""

    lines: list[str]
    source_file: str
    sequence: int

    @property
    def header(self) -> GedcomLine:
        """Return parsed metadata for the level-zero line."""
        return parse_gedcom_line(self.lines[0])

    @property
    def pointer(self) -> str:
        """Return this record's xref, if it has one."""
        return self.header.xref

    @property
    def tag(self) -> str:
        """Return the record type, such as ``INDI`` or ``FAM``."""
        return self.header.tag


def iter_gedcom_records(path: str | Path) -> Iterator[GedcomRecord]:
    """Yield level-zero GEDCOM records one at a time.

    Only one record is accumulated at a time.  This avoids the common mistake
    of loading a complete parse tree for every source before any work starts.
    The deduplication index necessarily retains person summaries, but arbitrary
    non-person records are not duplicated in the person index.
    """
    file_path = Path(path).resolve()
    current: list[str] = []
    sequence = 0
    with file_path.open("r", encoding="utf-8-sig", errors="strict") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            line = raw_line.rstrip("\r\n")
            if not line.strip():
                continue
            parsed = parse_gedcom_line(line, line_number)
            if parsed.level == 0 and current:
                yield GedcomRecord(current, str(file_path), sequence)
                sequence += 1
                current = []
            current.append(line)
        if current:
            yield GedcomRecord(current, str(file_path), sequence)


@dataclass
class ParsedSource:
    """All source records after pointer names have been made globally unique."""

    path: Path
    records: list[GedcomRecord]
    pointer_map: dict[str, str]


def _unique_pointer(original: str, used: set[str], source_number: int) -> str:
    """Return a collision-free pointer while retaining the original when safe."""
    if original not in used:
        used.add(original)
        return original
    match = re.fullmatch(r"@([A-Za-z_]+)(\d+)@", original)
    prefix = match.group(1) if match else "X"
    counter = 1
    candidate = f"@{prefix}{source_number}_{counter}@"
    while candidate in used:
        counter += 1
        candidate = f"@{prefix}{source_number}_{counter}@"
    used.add(candidate)
    return candidate


def _rewrite_xrefs(line: str, pointer_map: dict[str, str]) -> str:
    """Rewrite exact reference fields, never arbitrary note text."""
    parsed = parse_gedcom_line(line)
    reference_tags = {
        "ASSO", "CHIL", "FAMC", "FAMS", "HUSB", "WIFE", "OBJE",
        "NOTE", "REPO", "SOUR", "SUBM",
    }
    if not parsed.xref and parsed.tag not in reference_tags:
        return line
    return XREF_RE.sub(
        lambda match: pointer_map.get(match.group(), match.group()),
        line,
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
        if (parsed.level == event_level + 1 and parsed.tag == "DATE"
                and event_tag in {"BIRT", "DEAT"}):
            normalised = normalise_gedcom_date(parsed.value)
            if normalised != parsed.value and normalised.strip():
                output.append(f"{parsed.level} DATE {normalised}")
                output.append(f"{parsed.level} _ORIGDATE {parsed.value}")
                continue
        output.append(line)
    return output


def _normalise_header_lines(
    headers: Sequence[GedcomRecord],
    version: str,
) -> list[str]:
    """Create one compliant HEAD while retaining distinct source metadata."""
    if not headers:
        return [
            "0 HEAD",
            "1 SOUR GedcomMergeTool",
            "1 GEDC",
            f"2 VERS {version}",
            "1 CHAR UTF-8",
        ]

    blocks: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for header in headers:
        for block in _top_level_blocks(header.lines):
            tag = parse_gedcom_line(block[0]).tag
            if tag in {"GEDC", "CHAR"}:
                continue
            key = tuple(block)
            if key not in seen:
                blocks.append(block)
                seen.add(key)

    result = ["0 HEAD"]
    for block in blocks:
        result.extend(block)
    result.extend(["1 GEDC", f"2 VERS {version}", "1 CHAR UTF-8"])
    return result


def validate_gedcom_555(lines: Sequence[str]) -> None:
    """Validate the structural requirements emitted for GEDCOM 5.5.5.

    This deliberately validates the generic grammar and referential shape,
    rather than trying to hard-code every vendor extension.  Custom tags are
    allowed by GEDCOM and are retained; a destination service may ignore them.
    """
    if not lines:
        raise GedcomParseError("GEDCOM output is empty")
    parsed_lines = [parse_gedcom_line(line, number) for number, line in enumerate(lines, 1)]
    if parsed_lines[0].level != 0 or parsed_lines[0].tag != "HEAD":
        raise GedcomParseError("GEDCOM must start with 0 HEAD")
    if parsed_lines[-1].level != 0 or parsed_lines[-1].tag != "TRLR":
        raise GedcomParseError("GEDCOM must end with 0 TRLR")
    pointers: set[str] = set()
    previous_level = 0
    head_version = ""
    head_charset = ""
    in_gedc = False
    for parsed in parsed_lines:
        if len(parsed.tag) > 15:
            raise GedcomParseError(f"GEDCOM tag is longer than 15 characters: {parsed.tag}")
        if parsed.xref:
            if parsed.level != 0:
                raise GedcomParseError("xref IDs may only introduce level-zero records")
            if parsed.xref in pointers:
                raise GedcomParseError(f"Duplicate GEDCOM xref ID: {parsed.xref}")
            pointers.add(parsed.xref)
        if parsed.level > previous_level + 1:
            raise GedcomParseError("GEDCOM levels may not skip a level")
        previous_level = parsed.level
        if parsed.level == 1:
            in_gedc = parsed.tag == "GEDC"
            if parsed.tag == "CHAR":
                head_charset = parsed.value.strip().upper()
        elif parsed.level == 2 and in_gedc and parsed.tag == "VERS":
            head_version = parsed.value.strip()
    if head_version != "5.5.5":
        raise GedcomParseError(
            f"Expected HEAD.GEDC.VERS 5.5.5, found {head_version or '(missing)'}"
        )
    if head_charset != "UTF-8":
        raise GedcomParseError(
            f"Expected HEAD.CHAR UTF-8, found {head_charset or '(missing)'}"
        )


def _family_members(source_records: Iterable[GedcomRecord]) -> dict[str, set[str]]:
    """Return family xref -> member-person xrefs for root traversal."""
    result: dict[str, set[str]] = {}
    for record in source_records:
        if record.tag != "FAM" or not record.pointer:
            continue
        members: set[str] = set()
        for block in _top_level_blocks(record.lines):
            first = parse_gedcom_line(block[0])
            if first.tag in {"HUSB", "WIFE", "CHIL"} and first.value:
                members.update(XREF_RE.findall(first.value))
        result[record.pointer] = members
    return result


def resolve_root_person(
    requested: str,
    records: Sequence[IndividualRecord],
    source_pointer_maps: Sequence[dict[str, str]],
    merged_pointer_map: dict[str, str],
) -> str:
    """Resolve a pointer or unique full name to a canonical person pointer."""
    requested = requested.strip()
    pointers = {record.pointer for record in records}
    if requested in pointers:
        return requested
    mapped = {
        merged_pointer_map.get(pointer_map[requested], pointer_map[requested])
        for pointer_map in source_pointer_maps
        if requested in pointer_map
    }
    mapped &= pointers
    if len(mapped) == 1:
        return mapped.pop()
    name_matches = {
        record.pointer
        for record in records
        if record.full_name.casefold() == requested.casefold()
    }
    if len(name_matches) == 1:
        return name_matches.pop()
    if not name_matches and not mapped:
        raise ValueError(f"Root person not found: {requested}")
    raise ValueError(
        f"Root person is ambiguous: {requested!r}; use a unique GEDCOM pointer"
    )


def connected_tree_pointers(
    root_pointer: str,
    people: Sequence[IndividualRecord],
    source_records: Iterable[GedcomRecord],
    merged_pointer_map: Optional[dict[str, str]] = None,
) -> tuple[set[str], set[str]]:
    """Return all people and families in the root person's connected tree."""
    family_members = _family_members(source_records)
    if merged_pointer_map:
        family_members = {
            family: {
                merged_pointer_map.get(member, member) for member in members
            }
            for family, members in family_members.items()
        }
    person_to_families: dict[str, set[str]] = defaultdict(set)
    for family_pointer, members in family_members.items():
        for member in members:
            person_to_families[member].add(family_pointer)
    keep_people: set[str] = set()
    keep_families: set[str] = set()
    pending = [root_pointer]
    known_people = {person.pointer for person in people}
    while pending:
        pointer = pending.pop()
        if pointer in keep_people or pointer not in known_people:
            continue
        keep_people.add(pointer)
        for family_pointer in person_to_families.get(pointer, set()):
            if family_pointer in keep_families:
                continue
            keep_families.add(family_pointer)
            pending.extend(family_members.get(family_pointer, set()))
    return keep_people, keep_families


def _load_python_gedcom(path: Path) -> None:
    """Run python-gedcom's parser as an additional standards-aware check.

    The raw parser remains authoritative for unknown-tag preservation.  This
    optional check provides useful warnings and exercises the trusted parser
    without making a fragile DOM the serialization source.
    """
    try:
        from gedcom.parser import Parser
    except ImportError:
        log.debug("python-gedcom is not installed; using lossless line parser")
        return
    try:
        parser = Parser()
        parser.parse_file(str(path), strict=False)
    except Exception as exc:  # noqa: BLE001 - source files may be vendor-specific
        log.warning("python-gedcom validation warning for %s: %s", path, exc)


def load_sources(paths: Sequence[str | Path]) -> list[ParsedSource]:
    """Read sources twice: first for xref allocation, then for record content."""
    used: set[str] = set()
    sources: list[ParsedSource] = []
    for source_number, raw_path in enumerate(paths, 1):
        path = Path(raw_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"GEDCOM file not found: {path}")
        _load_python_gedcom(path)
        original_records = list(iter_gedcom_records(path))
        pointer_map: dict[str, str] = {}
        for record in original_records:
            if record.pointer:
                pointer_map[record.pointer] = _unique_pointer(
                    record.pointer, used, source_number
                )
        rewritten: list[GedcomRecord] = []
        for record in original_records:
            lines = _normalise_record_dates(
                [_rewrite_xrefs(line, pointer_map) for line in record.lines]
            )
            rewritten.append(GedcomRecord(lines, str(path), record.sequence))
        sources.append(ParsedSource(path, rewritten, pointer_map))
        log.info("Loaded %d records from %s", len(rewritten), path.name)
    return sources


def normalise_gedcom_date(raw_date: str) -> str:
    """Return a best-effort GEDCOM date while leaving unknown text unchanged."""
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
            original = original[len(gedcom_prefix):].strip()
            break
        if upper == prefix.upper() or upper.startswith(prefix.upper() + " "):
            qualifier = gedcom_prefix
            original = original[len(prefix):].strip()
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
        day, month, year = full_match.groups()
        try:
            dt.date(int(year), GEDCOM_MONTHS.index(month.upper()) + 1, int(day))
        except ValueError:
            return raw_date
        result = f"{int(day):02d} {month.upper()} {year}"
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
                result = (
                    f"{parsed.day:02d} {GEDCOM_MONTHS[parsed.month - 1]} "
                    f"{parsed.year}"
                )
                return f"{qualifier} {result}".strip()
            except ValueError:
                continue
        log.debug("Could not normalise date %r", raw_date)
        return raw_date


def _extract_year(date_value: str) -> Optional[int]:
    """Extract the first four-digit year from a date or return ``None``."""
    match = re.search(r"\b(\d{4})\b", date_value or "")
    return int(match.group(1)) if match else None


@dataclass
class IndividualRecord:
    """Deduplication summary plus the complete underlying INDI record."""

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

    @property
    def full_name(self) -> str:
        """Return the compact display name."""
        return " ".join(
            part.strip()
            for part in (self.given_name, self.surname)
            if part.strip()
        )

    @property
    def birth_year(self) -> Optional[int]:
        """Return the birth year, if present."""
        return _extract_year(self.birth_date)

    @property
    def death_year(self) -> Optional[int]:
        """Return the death year, if present."""
        return _extract_year(self.death_date)

    def summary(self) -> str:
        """Return a prompt/logging summary without dumping sensitive notes."""
        parts = [f"[{self.pointer}] {self.full_name or '(unknown)'}"]
        if self.birth_date:
            parts.append(f"b. {self.birth_date}")
        if self.birth_place:
            parts.append(f"b.place={self.birth_place}")
        if self.death_date:
            parts.append(f"d. {self.death_date}")
        if self.death_place:
            parts.append(f"d.place={self.death_place}")
        if self.gender:
            parts.append(f"sex={self.gender}")
        if self.family_links:
            parts.append(f"family-links={len(self.family_links)}")
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


def _individual_from_record(record: GedcomRecord) -> IndividualRecord:
    """Build a summary from a lossless INDI record."""
    lines = _normalise_record_dates(record.lines)
    name = surname = birth_date = birth_place = death_date = death_place = gender = ""
    family_links: list[str] = []
    extra: dict[str, list[str]] = defaultdict(list)
    for block in _top_level_blocks(lines):
        first = parse_gedcom_line(block[0])
        if first.tag == "NAME":
            name, surname = _name_parts(first.value)
        elif first.tag == "SEX":
            gender = first.value.strip().upper()
        elif first.tag in {"FAMS", "FAMC"}:
            if first.value.strip():
                family_links.append(first.value.strip())
        elif first.tag in {"BIRT", "DEAT"}:
            date_value = place_value = ""
            for line in block[1:]:
                child = parse_gedcom_line(line)
                if child.level == first.level + 1 and child.tag == "DATE":
                    date_value = normalise_gedcom_date(child.value)
                elif child.level == first.level + 1 and child.tag == "PLAC":
                    place_value = child.value.strip()
            if first.tag == "BIRT":
                birth_date, birth_place = date_value, place_value
            else:
                death_date, death_place = date_value, place_value
        else:
            extra[first.tag].append("\n".join(block) + "\n")
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
    )


def load_gedcom(path: str | Path) -> list[IndividualRecord]:
    """Load only INDI summaries from one GEDCOM file.

    ``load_sources`` should be preferred by the CLI because it globally
    disambiguates pointers and retains family/source records for output.
    This compatibility helper is useful for callers and tests.
    """
    return [
        _individual_from_record(
            dataclasses.replace(record, lines=_normalise_record_dates(record.lines))
        )
        for record in iter_gedcom_records(path)
        if record.tag == "INDI"
    ]


def _text_similarity(left: str, right: str) -> float:
    """Return a 0--100 case-insensitive token similarity."""
    a = " ".join(re.findall(r"[\w]+", left.casefold()))
    b = " ".join(re.findall(r"[\w]+", right.casefold()))
    if _rapidfuzz is not None:
        return float(_rapidfuzz.token_sort_ratio(a, b))
    return difflib.SequenceMatcher(None, a, b).ratio() * 100


def _year_similarity(left: Optional[int], right: Optional[int]) -> float:
    """Score year agreement while giving missing values neutral credit."""
    if left is None and right is None:
        return 80.0
    if left is None or right is None:
        return 60.0
    difference = abs(left - right)
    if difference == 0:
        return 100.0
    if difference <= 5:
        return max(0.0, 100.0 - difference * 20)
    return 0.0


def similarity_score(a: IndividualRecord, b: IndividualRecord) -> float:
    """Return a conservative composite score in the range 0--100."""
    name_score = (
        _text_similarity(a.full_name, b.full_name)
        if a.full_name and b.full_name
        else 50.0
    )
    if a.gender and b.gender:
        gender_score = 100.0 if a.gender == b.gender else 0.0
    else:
        gender_score = 100.0
    if a.family_links and b.family_links:
        relationship_score = (
            100.0
            if len(a.family_links) == len(b.family_links)
            else 60.0
        )
    else:
        relationship_score = 100.0
    score = (
        name_score * 0.60
        + _year_similarity(a.birth_year, b.birth_year) * 0.18
        + _year_similarity(a.death_year, b.death_year) * 0.10
        + gender_score * 0.07
        + relationship_score * 0.05
    )
    return round(max(0.0, min(100.0, score)), 2)


def _blocking_keys(record: IndividualRecord) -> set[tuple[str, ...]]:
    """Create several inexpensive keys so candidate generation is sub-quadratic."""
    surname = re.sub(r"[^a-z0-9]", "", record.surname.casefold())
    given = re.sub(r"[^a-z0-9]", "", record.given_name.casefold())
    surname_initial = surname[:1] or "?"
    given_initial = given[:1] or "?"
    year = record.birth_year
    buckets = {year // 5} if year is not None else {"?"}
    keys: set[tuple[str, ...]] = set()
    for bucket in buckets:
        keys.add(("sn", surname_initial, "gn", given_initial, "y", str(bucket)))
        keys.add(("sn", surname_initial, "y", str(bucket)))
        keys.add(("gn", given_initial, "y", str(bucket)))
    keys.add(("name", surname_initial, given_initial))
    return keys


def find_duplicate_candidates(
    records: list[IndividualRecord],
    threshold: int = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[tuple[int, int, float]]:
    """Find cross-file candidates using blocking before fuzzy comparison."""
    buckets: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        for key in _blocking_keys(record):
            buckets[key].append(index)
    pairs: set[tuple[int, int]] = set()
    for indexes in buckets.values():
        for position, left in enumerate(indexes):
            for right in indexes[position + 1:]:
                if records[left].source_file != records[right].source_file:
                    pairs.add((min(left, right), max(left, right)))
    candidates: list[tuple[int, int, float]] = []
    for left, right in pairs:
        score = similarity_score(records[left], records[right])
        if score >= threshold:
            candidates.append((left, right, score))
    return sorted(candidates, key=lambda item: item[2], reverse=True)


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


def _parse_ai_response(response_text: str) -> dict[str, object]:
    """Parse and clamp an AI response to a safe, typed decision structure."""
    cleaned = re.sub(r"```(?:json)?", "", response_text, flags=re.IGNORECASE).strip()
    try:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        data = json.loads(cleaned[start:end + 1]) if start >= 0 and end > start else {}
    except (json.JSONDecodeError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    preferred = data.get("preferred_values", {})
    if not isinstance(preferred, dict):
        preferred = {}
    allowed = {
        "given_name", "surname", "birth_date", "birth_place",
        "death_date", "death_place", "gender",
    }
    preferred_values = {
        key: str(value)
        for key, value in preferred.items()
        if key in allowed and value
    }
    return {
        "is_duplicate": data.get("is_duplicate") is True,
        "confidence": confidence,
        "reasoning": str(data.get("reasoning", ""))[:MAX_AI_TEXT],
        "preferred_values": preferred_values,
    }


def ai_resolve_ollama(
    a: IndividualRecord,
    b: IndividualRecord,
    model: str = "llama3.1",
    base_url: str = "http://localhost:11434",
    timeout: float = 60.0,
    **_: object,
) -> dict[str, object]:
    """Call Ollama's local HTTP API without executing model-produced code."""
    payload = json.dumps({
        "model": model,
        "prompt": _build_dedup_prompt(a, b),
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Ollama request failed: {exc}") from exc
    return _parse_ai_response(str(body.get("response", "")))


def ai_resolve_openai(
    a: IndividualRecord,
    b: IndividualRecord,
    api_key: Optional[str] = None,
    model: str = "gpt-4.1-mini",
    **_: object,
) -> dict[str, object]:
    """Call the OpenAI Responses API using an environment-provided key."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "Install the optional 'openai' package for this backend"
        ) from exc
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    schema = {
        "type": "object",
        "properties": {
            "is_duplicate": {"type": "boolean"},
            "confidence": {"type": "number"},
            "reasoning": {"type": "string"},
            "preferred_values": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["is_duplicate", "confidence", "reasoning", "preferred_values"],
        "additionalProperties": False,
    }
    try:
        client = OpenAI(api_key=key)
        response = client.responses.create(
            model=model,
            instructions="Return only the JSON object requested by the user.",
            input=_build_dedup_prompt(a, b),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "dedup_decision",
                    "strict": True,
                    "schema": schema,
                },
            },
        )
        return _parse_ai_response(response.output_text)
    except Exception as exc:  # noqa: BLE001 - SDK exception types vary by version
        raise RuntimeError(f"OpenAI request failed: {exc}") from exc


def ai_resolve_gemini(
    a: IndividualRecord,
    b: IndividualRecord,
    api_key: Optional[str] = None,
    model: str = "gemini-2.0-flash",
    **_: object,
) -> dict[str, object]:
    """Compatibility resolver for the optional Google Gemini SDK."""
    try:
        import google.generativeai as genai
    except ImportError as exc:
        raise RuntimeError(
            "Install the optional 'google-generativeai' package"
        ) from exc
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    genai.configure(api_key=key)
    try:
        response = genai.GenerativeModel(
            model,
            generation_config={"response_mime_type": "application/json"},
        ).generate_content(_build_dedup_prompt(a, b))
        return _parse_ai_response(response.text)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Gemini request failed: {exc}") from exc


def ai_resolve(
    a: IndividualRecord,
    b: IndividualRecord,
    backend: str = "ollama",
    **kwargs: object,
) -> dict[str, object]:
    """Dispatch to an AI backend; ``none`` is an explicit offline mode."""
    if backend == "none":
        return {
            "is_duplicate": False,
            "confidence": 0.0,
            "reasoning": "AI disabled",
            "preferred_values": {},
        }
    if backend == "ollama":
        return ai_resolve_ollama(a, b, **kwargs)
    if backend == "openai":
        return ai_resolve_openai(a, b, **kwargs)
    if backend == "gemini":
        return ai_resolve_gemini(a, b, **kwargs)
    raise ValueError(f"Unknown AI backend: {backend}")


def _field_value(record: IndividualRecord, field_name: str) -> str:
    """Read a mergeable summary field by name."""
    return str(getattr(record, field_name, ""))


def merge_two_records(
    primary: IndividualRecord,
    secondary: IndividualRecord,
    ai_verdict: Optional[dict[str, object]] = None,
) -> IndividualRecord:
    """Merge summaries and complete raw blocks without deleting conflicts."""
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
        return first

    merged_extra = {tag: list(values) for tag, values in primary.extra_fields.items()}
    for tag, values in secondary.extra_fields.items():
        target = merged_extra.setdefault(tag, [])
        target.extend(value for value in values if value not in target)
    first_lines = (
        primary.raw_lines
        or _record_to_gedcom_lines(primary).rstrip("\n").splitlines()
    )
    second_lines = (
        secondary.raw_lines
        or _record_to_gedcom_lines(secondary).rstrip("\n").splitlines()
    )
    merged_lines = list(first_lines)
    existing_blocks = (
        {tuple(block) for block in _top_level_blocks(merged_lines)}
        if len(merged_lines) > 1
        else set()
    )
    for block in _top_level_blocks(second_lines):
        block_key = tuple(block)
        if block_key not in existing_blocks:
            merged_lines.extend(block)
            existing_blocks.add(block_key)
    return dataclasses.replace(
        primary,
        given_name=choose("given_name"),
        surname=choose("surname"),
        birth_date=choose("birth_date"),
        birth_place=choose("birth_place"),
        death_date=choose("death_date"),
        death_place=choose("death_place"),
        gender=choose("gender"),
        family_links=tuple(
            dict.fromkeys(primary.family_links + secondary.family_links)
        ),
        extra_fields=merged_extra,
        raw_lines=merged_lines,
    )


def prompt_operator(a: IndividualRecord, b: IndividualRecord) -> bool:
    """Ask for confirmation, defaulting to no on EOF or invalid input."""
    print(f"\nPotential duplicate:\n  A: {a.summary()}\n  B: {b.summary()}")
    try:
        answer = input("Same person? [y/N]: ").strip().casefold()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def _get_ai_verdict(
    a: IndividualRecord,
    b: IndividualRecord,
    backend: str,
    kwargs: dict[str, object],
) -> dict[str, object]:
    """Return a safe no-merge verdict if an optional backend is unavailable."""
    try:
        return ai_resolve(a, b, backend=backend, **kwargs)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "AI backend %s unavailable; retaining both records: %s",
            backend,
            exc,
        )
        return {
            "is_duplicate": False,
            "confidence": 0.0,
            "reasoning": str(exc),
            "preferred_values": {},
        }


def merge_records(
    all_records: list[IndividualRecord],
    threshold: int = DEFAULT_SIMILARITY_THRESHOLD,
    ai_backend: str = "ollama",
    auto: bool = False,
    ai_kwargs: Optional[dict[str, object]] = None,
    pointer_map: Optional[dict[str, str]] = None,
) -> list[IndividualRecord]:
    """Deduplicate with union-find-style canonical pointers and safe fallbacks."""
    if not 0 <= threshold <= 100:
        raise ValueError("similarity threshold must be between 0 and 100")
    kwargs = ai_kwargs or {}
    by_pointer = {record.pointer: record for record in all_records}
    parent = {record.pointer: record.pointer for record in all_records}

    def find(pointer: str) -> str:
        while parent[pointer] != pointer:
            parent[pointer] = parent[parent[pointer]]
            pointer = parent[pointer]
        return pointer

    candidates = find_duplicate_candidates(all_records, threshold)
    log.info("Found %d candidate pairs", len(candidates))
    for left, right, score in candidates:
        root_left = find(all_records[left].pointer)
        root_right = find(all_records[right].pointer)
        if root_left == root_right:
            continue
        first, second = by_pointer[root_left], by_pointer[root_right]
        verdict: dict[str, object]
        if score >= 95:
            verdict = {
                "is_duplicate": True,
                "confidence": 1.0,
                "reasoning": "deterministic high score",
                "preferred_values": {},
            }
        else:
            verdict = _get_ai_verdict(first, second, ai_backend, kwargs)
            confidence = float(verdict.get("confidence", 0.0))
            if not auto and confidence < AI_CONFIDENCE_AUTO_ACCEPT:
                verdict = dict(verdict)
                verdict["is_duplicate"] = prompt_operator(first, second)
            elif not bool(verdict.get("is_duplicate", False)):
                continue
        if bool(verdict.get("is_duplicate", False)):
            merged = merge_two_records(first, second, verdict)
            parent[root_right] = root_left
            by_pointer[root_left] = merged
            log.info(
                "Merged %s <- %s (score %.1f)",
                root_left,
                root_right,
                score,
            )
    result: list[IndividualRecord] = []
    seen: set[str] = set()
    for record in all_records:
        root = find(record.pointer)
        if root not in seen:
            result.append(by_pointer[root])
            seen.add(root)
    if pointer_map is not None:
        pointer_map.update(
            {record.pointer: find(record.pointer) for record in all_records}
        )
    log.info("Merge complete: %d -> %d individuals", len(all_records), len(result))
    return result


def _record_to_gedcom_lines(record: IndividualRecord) -> str:
    """Serialize a synthetic summary for backwards-compatible callers."""
    lines = [f"0 {record.pointer} INDI\n"]
    name = (
        f"{record.given_name} /{record.surname}/".strip()
        if record.surname
        else record.given_name
    )
    if name:
        lines.append(f"1 NAME {name}\n")
    if record.gender:
        lines.append(f"1 SEX {record.gender}\n")
    events = (
        ("BIRT", record.birth_date, record.birth_place),
        ("DEAT", record.death_date, record.death_place),
    )
    for tag, date_value, place in events:
        if date_value or place:
            lines.append(f"1 {tag}\n")
            if date_value:
                lines.append(f"2 DATE {date_value}\n")
            if place:
                lines.append(f"2 PLAC {place}\n")
    for values in record.extra_fields.values():
        lines.extend(
            value if value.endswith("\n") else value + "\n"
            for value in values
        )
    return "".join(lines)


def write_gedcom(
    records: list[IndividualRecord],
    output_path: str | Path,
    source_parsers: Optional[list[Any]] = None,
    source_documents: Optional[list[ParsedSource]] = None,
    pointer_map: Optional[dict[str, str]] = None,
    include_individuals: Optional[set[str]] = None,
    include_families: Optional[set[str]] = None,
    gedcom_version: str = "5.5.5",
) -> None:
    """Write an atomic master file, preserving all source root records.

    ``source_documents`` is the lossless path used by this module.  The older
    ``source_parsers`` argument remains accepted for synthetic/unit callers;
    DOM elements are copied only when they expose ``to_gedcom_string``.
    """
    if gedcom_version not in SUPPORTED_GEDCOM_VERSIONS:
        raise ValueError(
            f"Unsupported GEDCOM version {gedcom_version}; "
            f"choose from {SUPPORTED_GEDCOM_VERSIONS}"
        )
    out_path = Path(output_path).resolve()
    if out_path.parent and not out_path.parent.exists():
        raise OSError(f"Output directory does not exist: {out_path.parent}")
    lines: list[str] = []
    if source_documents:
        all_source_records = [
            record
            for source in source_documents
            for record in source.records
        ]
        heads = [record for record in all_source_records if record.tag == "HEAD"]
        header_lines = _normalise_header_lines(heads, gedcom_version)
        lines.extend(
            _rewrite_xrefs(line, pointer_map or {}) for line in header_lines
        )
        for record in all_source_records:
            if record.tag not in {"HEAD", "TRLR", "INDI"}:
                if (
                    record.tag == "FAM"
                    and include_families is not None
                    and record.pointer not in include_families
                ):
                    continue
                lines.extend(
                    _rewrite_xrefs(line, pointer_map or {})
                    for line in record.lines
                )
        survivor_lines = {
            record.pointer: record.raw_lines
            for record in records
            if record.raw_lines
        }
        for record in records:
            if (
                include_individuals is not None
                and record.pointer not in include_individuals
            ):
                continue
            source_lines = survivor_lines.get(record.pointer) or (
                _record_to_gedcom_lines(record).rstrip("\n").splitlines()
            )
            lines.extend(
                _rewrite_xrefs(line, pointer_map or {})
                for line in source_lines
            )
    elif source_parsers:
        # Compatibility path for callers of the previous DOM-based API.
        # New CLI calls use source_documents so unknown lines are retained.
        for parser in source_parsers:
            for element in parser.get_root_child_elements():
                tag = element.get_tag()
                text = element.to_gedcom_string(recursive=True)
                if tag == "HEAD" and not lines:
                    lines.extend(text.rstrip("\n").splitlines())
                elif tag not in {"HEAD", "TRLR", "INDI"}:
                    lines.extend(text.rstrip("\n").splitlines())
        for record in records:
            if (
                include_individuals is not None
                and record.pointer not in include_individuals
            ):
                continue
            lines.extend(_record_to_gedcom_lines(record).rstrip("\n").splitlines())
    else:
        lines.extend(_normalise_header_lines([], gedcom_version))
        for record in records:
            if (
                include_individuals is not None
                and record.pointer not in include_individuals
            ):
                continue
            lines.extend(
                _record_to_gedcom_lines(record).rstrip("\n").splitlines()
            )
    lines.append("0 TRLR")
    if gedcom_version == "5.5.5":
        validate_gedcom_555(lines)
    payload = "\n".join(lines) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=out_path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
    try:
        os.replace(temporary, out_path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    log.info("Wrote %d individuals to %s", len(records), out_path)


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input_files", nargs="+", metavar="FILE")
    parser.add_argument("-o", "--output", default="merged.ged")
    parser.add_argument(
        "--ai-backend",
        choices=("none", "ollama", "openai", "gemini"),
        default="ollama",
    )
    parser.add_argument(
        "--similarity-threshold",
        type=int,
        default=DEFAULT_SIMILARITY_THRESHOLD,
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Do not ask interactive questions.",
    )
    parser.add_argument(
        "--root-person",
        help=(
            "Export only the connected tree containing this GEDCOM pointer "
            "(for example @I1@) or unique full name."
        ),
    )
    parser.add_argument(
        "--gedcom-version",
        choices=SUPPORTED_GEDCOM_VERSIONS,
        default="5.5.5",
        help="Output version; 5.5.1 is a compatibility fallback.",
    )
    parser.add_argument("--ollama-model", default=os.getenv("OLLAMA_MODEL", "llama3.1"))
    parser.add_argument(
        "--ollama-url",
        default=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
    )
    parser.add_argument(
        "--openai-model",
        default=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
    )
    parser.add_argument(
        "--gemini-model",
        default=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Run the merge command and return a shell exit code."""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    if len(args.input_files) < 2:
        parser.error("At least two input GEDCOM files are required")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        paths = [Path(path).resolve() for path in args.input_files]
        output = Path(args.output).resolve()
        if output in paths:
            raise ValueError("Output path must not overwrite an input file")
        sources = load_sources(paths)
        all_records = [
            _individual_from_record(record)
            for source in sources
            for record in source.records
            if record.tag == "INDI"
        ]
        kwargs: dict[str, object] = {}
        if args.ai_backend == "ollama":
            kwargs = {"model": args.ollama_model, "base_url": args.ollama_url}
        elif args.ai_backend == "openai":
            kwargs = {"model": args.openai_model}
        elif args.ai_backend == "gemini":
            kwargs = {"model": args.gemini_model}
        pointer_map: dict[str, str] = {}
        merged = merge_records(
            all_records,
            args.similarity_threshold,
            args.ai_backend,
            args.auto,
            kwargs,
            pointer_map,
        )
        include_individuals: Optional[set[str]] = None
        include_families: Optional[set[str]] = None
        if args.root_person:
            root_pointer = resolve_root_person(
                args.root_person,
                merged,
                [source.pointer_map for source in sources],
                pointer_map,
            )
            all_source_records = [
                record
                for source in sources
                for record in source.records
            ]
            include_individuals, include_families = connected_tree_pointers(
                root_pointer,
                merged,
                all_source_records,
                pointer_map,
            )
            log.info(
                "Rooted export at %s: %d people, %d families",
                root_pointer,
                len(include_individuals),
                len(include_families),
            )
        write_gedcom(
            merged,
            output,
            source_documents=sources,
            pointer_map=pointer_map,
            include_individuals=include_individuals,
            include_families=include_families,
            gedcom_version=args.gedcom_version,
        )
        print(
            f"Merge complete: {len(all_records)} individuals -> "
            f"{len(merged)} in {output}"
        )
        return 0
    except (OSError, ValueError, GedcomParseError) as exc:
        log.error("Merge failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
