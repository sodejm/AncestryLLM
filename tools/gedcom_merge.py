"""Merge GEDCOM 5.5-style files with conservative identity adjudication.

The CLI reads two or more GEDCOM files and atomically writes one master file.
Source fact blocks remain the data-fidelity authority, although xrefs, dates,
headers, ordering, and line wrapping may be normalized.  A rooted export
intentionally omits people outside the selected connected component.

Deterministic scoring and optional AI may decide identity and choose a summary
value, but conflicting source blocks remain in the output.  Missing facts are
unknown rather than negative evidence, and remote prompts exclude notes,
citations, media, and government identifiers.  See tools/README.md for the CLI
contract, privacy boundaries, setup, and examples.  Private underscore-prefixed
helpers are not a stable library API.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import difflib
import hashlib
import json
import logging
import os
import re
import tempfile
import urllib.error
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional, Sequence

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional in minimal installations
    load_dotenv = None

if load_dotenv is not None:
    # Search from the caller/script location upward.  Existing process
    # variables win, so deployment secrets are never overwritten by .env.
    load_dotenv(override=False)

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
QUALITY_DUPLICATE_THRESHOLD = 90
QUALITY_AI_LIMIT = 25
AI_CONFIDENCE_AUTO_ACCEPT = 0.85
MAX_AI_TEXT = 2_000
XREF_RE = re.compile(r"@[A-Za-z0-9_:-]+@")
SUPPORTED_GEDCOM_VERSIONS = ("5.5.5", "5.5.1")
REMOTE_CREDIT_POLICIES = ("required", "best-effort", "off")
QUALITY_SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}
DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL") or "gpt-5.4-mini"
DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL") or "gemini-3.5-flash"
DEFAULT_OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL") or "openrouter/auto"
DEFAULT_OPENROUTER_MODELS = (
    "openai/gpt-5*",
    "google/gemini-*",
)
# Standard individual facts that can corroborate identity without exposing
# free-form notes, source text, or government identifiers to an AI provider.
# BIRT and DEAT are scored separately but remain here so alternative events
# participate in comparison rather than being overwritten by one summary value.
IDENTITY_FACT_TAGS = frozenset({
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
})
FAMILY_IDENTITY_FACT_TAGS = frozenset({
    "ANUL",
    "DIV",
    "DIVF",
    "ENGA",
    "MARB",
    "MARC",
    "MARL",
    "MARR",
    "MARS",
})
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
KNOWN_COUNTRY_NAMES = frozenset(COUNTRY_ALIASES.values()) | frozenset({
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
})
LINE_RE = re.compile(r"^(?P<level>[0-9]{1,2})(?:\s+(?P<xref>@[^@\s]+@))?\s+"
                     r"(?P<tag>[A-Za-z0-9_]+)(?:\s+(?P<value>.*))?$")


class GedcomParseError(ValueError):
    """Raised when an input line cannot be interpreted as a GEDCOM line."""


class RemoteCreditError(RuntimeError):
    """Raised when a remote provider cannot pass the configured credit gate."""


@dataclass(frozen=True, slots=True)
class CreditStatus:
    """Result of a provider preflight that never contains genealogy data.

    ``checked`` is true only for an account-level balance.  A numeric
    ``remaining_usd`` can still be merely a per-key cap when ``checked`` is
    false.  This distinction prevents an authentication, key-limit, or model
    probe from being misrepresented as a real account credit check.
    """

    provider: str
    checked: bool
    remaining_usd: Optional[float]
    detail: str


@dataclass(frozen=True, slots=True)
class GedcomLine:
    """Parsed metadata for one original GEDCOM line."""

    level: int
    xref: str
    tag: str
    value: str
    raw: str


def parse_gedcom_line(line: str, line_number: int = 0) -> GedcomLine:
    """Parse one GEDCOM line without evaluating its contents.

    Args:
        line: A physical GEDCOM line, with or without its newline terminator.
        line_number: Optional one-based source location used in error messages.

    Returns:
        Parsed level, xref, uppercase tag, value, and untouched logical text.

    Raises:
        GedcomParseError: The line has an invalid level or GEDCOM grammar.
    """
    raw = line.rstrip("\r\n").lstrip("\ufeff")
    if not re.match(r"^(?:0|[1-9][0-9]?)(?:\s|$)", raw):
        raise GedcomParseError(f"Invalid GEDCOM level {line_number}: {raw!r}")
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

    Args:
        path: UTF-8/UTF-8-BOM or UTF-16 GEDCOM file to stream.

    Yields:
        Complete level-zero records in source order.

    Raises:
        OSError: The file cannot be opened or read.
        UnicodeError: The declared/sensed text cannot be decoded strictly.
        GedcomParseError: A nonblank input line is structurally invalid.
    """
    file_path = Path(path).resolve()
    current: list[str] = []
    sequence = 0
    with file_path.open("rb") as binary_handle:
        prefix = binary_handle.read(4)
    if prefix.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    else:
        encoding = "utf-8-sig"
    with file_path.open("r", encoding=encoding, errors="strict") as handle:
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
        "ALIA", "ASSO", "CHIL", "FAMC", "FAMS", "HUSB", "WIFE",
        "OBJE", "NOTE", "REPO", "SOUR", "SUBM", "SNOTE", "WITN",
    }
    if not parsed.xref and parsed.tag not in reference_tags:
        return line
    if not parsed.xref and not XREF_RE.fullmatch(parsed.value.strip()):
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
            "1 SUBM @U1@",
            "1 GEDC",
            f"2 VERS {version}",
            "1 CHAR UTF-8",
        ]

    blocks: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    single_value_tags = {
        "SOUR", "DEST", "DATE", "SUBM", "FILE", "COPR", "LANG",
        "PLAC",
    }
    seen_tags: set[str] = set()
    for header in headers:
        for block in _top_level_blocks(header.lines):
            tag = parse_gedcom_line(block[0]).tag
            if tag in {"GEDC", "CHAR"}:
                continue
            key = tuple(block)
            if (
                key not in seen
                and not (tag in single_value_tags and tag in seen_tags)
            ):
                blocks.append(block)
                seen.add(key)
                seen_tags.add(tag)
    if "SUBM" not in seen_tags:
        blocks.append(["1 SUBM @U1@"])

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
    parsed_lines = [
        parse_gedcom_line(line, number)
        for number, line in enumerate(lines, 1)
    ]
    if parsed_lines[0].level != 0 or parsed_lines[0].tag != "HEAD":
        raise GedcomParseError("GEDCOM must start with 0 HEAD")
    if parsed_lines[-1].level != 0 or parsed_lines[-1].tag != "TRLR":
        raise GedcomParseError("GEDCOM must end with 0 TRLR")
    pointers: set[str] = set()
    if sum(parsed.tag == "HEAD" for parsed in parsed_lines if parsed.level == 0) != 1:
        raise GedcomParseError("GEDCOM must contain exactly one 0 HEAD record")
    if sum(parsed.tag == "TRLR" for parsed in parsed_lines if parsed.level == 0) != 1:
        raise GedcomParseError("GEDCOM must contain exactly one 0 TRLR record")
    previous_level = 0
    head_version = ""
    head_charset = ""
    in_gedc = False
    for parsed in parsed_lines:
        if len(parsed.raw.encode("utf-8")) > 255:
            raise GedcomParseError("GEDCOM line exceeds the 255-byte limit")
        if len(parsed.tag) > 31:
            raise GedcomParseError(
                f"GEDCOM tag is longer than 31 characters: {parsed.tag}"
            )
        tag_index = 2 if parsed.xref else 1
        raw_tag = parsed.raw.split()[tag_index]
        if raw_tag != parsed.tag:
            raise GedcomParseError(f"GEDCOM tags must be uppercase: {raw_tag}")
        if parsed.xref:
            if parsed.level != 0:
                raise GedcomParseError("xref IDs may only introduce level-zero records")
            if (
                len(parsed.xref) > 22
                or not re.fullmatch(r"@[A-Za-z_][A-Za-z0-9_:-]*@", parsed.xref)
            ):
                raise GedcomParseError(f"Invalid GEDCOM xref ID: {parsed.xref}")
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


def _canonical_gedcom_line(line: str) -> str:
    """Emit one line with canonical ASCII level/tag spelling."""
    parsed = parse_gedcom_line(line)
    prefix = f"{parsed.level} "
    if parsed.xref:
        prefix += f"{parsed.xref} "
    prefix += parsed.tag
    return f"{prefix} {parsed.value}" if parsed.value else prefix


def _take_utf8_prefix(value: str, limit: int) -> tuple[str, str]:
    """Take the largest character-safe UTF-8 prefix within ``limit`` bytes."""
    used = 0
    end = 0
    for index, character in enumerate(value):
        size = len(character.encode("utf-8"))
        if used + size > limit:
            break
        used += size
        end = index + 1
    if end == 0 and value:
        raise GedcomParseError("GEDCOM continuation limit cannot hold one character")
    return value[:end], value[end:]


def _wrap_long_gedcom_lines(lines: Sequence[str]) -> list[str]:
    """Wrap long text values using standard level+1 CONC continuations."""
    wrapped: list[str] = []
    for line in lines:
        parsed = parse_gedcom_line(line)
        canonical = _canonical_gedcom_line(line)
        if len(canonical.encode("utf-8")) <= 255 or not parsed.value:
            wrapped.append(canonical)
            continue
        if parsed.level >= 99:
            raise GedcomParseError("Cannot wrap a value below GEDCOM level 99")
        prefix = canonical[: len(canonical) - len(parsed.value)]
        remaining = parsed.value
        first_limit = 255 - len(prefix.encode("utf-8"))
        first, remaining = _take_utf8_prefix(remaining, first_limit)
        wrapped.append(prefix + first)
        continuation_prefix = f"{parsed.level + 1} CONC "
        continuation_limit = 255 - len(continuation_prefix.encode("utf-8"))
        while remaining:
            chunk, remaining = _take_utf8_prefix(
                remaining, continuation_limit
            )
            wrapped.append(continuation_prefix + chunk)
    return wrapped


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


def _ensure_submitter_record(
    header_lines: list[str],
    source_records: Sequence[GedcomRecord],
) -> list[str]:
    """Ensure HEAD.SUBM points to a real SUBM record without pointer clashes."""
    submitter_values = [
        parse_gedcom_line(block[0]).value.strip()
        for block in _top_level_blocks(header_lines)
        if parse_gedcom_line(block[0]).tag == "SUBM"
    ]
    source_pointers = {record.pointer for record in source_records if record.pointer}
    source_submitters = {
        record.pointer for record in source_records if record.tag == "SUBM"
    }
    requested = submitter_values[0] if submitter_values else "@U1@"
    if requested in source_submitters:
        return []
    candidate = requested
    if candidate in source_pointers:
        suffix = 1
        candidate = f"@U1_{suffix}@"
        while candidate in source_pointers:
            suffix += 1
            candidate = f"@U1_{suffix}@"
        for index, line in enumerate(header_lines):
            parsed = parse_gedcom_line(line)
            if parsed.level == 1 and parsed.tag == "SUBM":
                header_lines[index] = f"1 SUBM {candidate}"
                break
    return [f"0 {candidate} SUBM", "1 NAME Gedcom Merge Tool"]


def resolve_root_person(
    requested: str,
    records: Sequence[IndividualRecord],
    source_pointer_maps: Sequence[dict[str, str]],
    merged_pointer_map: dict[str, str],
) -> str:
    """Resolve a pointer or unique full name to a canonical person pointer.

    Args:
        requested: Current/source xref or case-insensitive full name.
        records: Surviving merged people.
        source_pointer_maps: Per-file original-to-global xref mappings.
        merged_pointer_map: Duplicate-to-canonical xref mappings.

    Returns:
        The canonical pointer used by the rooted export.

    Raises:
        ValueError: The person is absent or the supplied name is ambiguous.
    """
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
    """Return the complete family-connected component around one person.

    The traversal follows spouse/partner and parent/child family membership in
    both directions.  It intentionally includes collateral relatives connected
    through retained family records; unrelated components are omitted.
    """
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
    """Load sources after allocating collision-free global xrefs.

    Undefined references are namespaced as well as declared records.  This
    prevents a dangling pointer in one file from binding accidentally to a
    similarly named record in another file.

    Args:
        paths: GEDCOM files in deterministic source-priority order.

    Returns:
        Parsed documents with rewritten records and original-to-global maps.

    Raises:
        FileNotFoundError: A source path is not a regular file.
        OSError: A source cannot be read.
        GedcomParseError: A source contains invalid GEDCOM line structure.
    """
    used: set[str] = set()
    sources: list[ParsedSource] = []
    for source_number, raw_path in enumerate(paths, 1):
        path = Path(raw_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"GEDCOM file not found: {path}")
        _load_python_gedcom(path)
        try:
            original_records = list(iter_gedcom_records(path))
        except GedcomParseError as exc:
            raise GedcomParseError(f"{path}: {exc}") from exc
        pointer_map: dict[str, str] = {}
        all_xrefs = {
            xref
            for record in original_records
            for line in record.lines
            for xref in XREF_RE.findall(line)
        }
        for record in original_records:
            if record.pointer:
                pointer_map[record.pointer] = _unique_pointer(
                    record.pointer, used, source_number
                )
        # Namespace undefined references too.  Otherwise an undefined
        # @I99@ in source A could be accidentally rebound to a defined @I99@
        # in source B during the merge.
        for xref in sorted(all_xrefs - pointer_map.keys()):
            pointer_map[xref] = _unique_pointer(xref, used, source_number)
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
            "family events" in compared
            and len(relative_anchors.intersection(compared)) >= 2
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
    generation: Optional[int] = None
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
    ancestor_relationships: tuple[tuple[str, int, str], ...] = field(
        default_factory=tuple
    )
    ai_backend: str = "none"
    ai_refined: bool = False
    privacy_status: str = "Local deterministic analysis only"


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

    @property
    def birth_country(self) -> str:
        """Return the best explicit or inferred country of birth."""
        return self.birth_countries[0] if self.birth_countries else ""

    @property
    def birth_countries(self) -> tuple[str, ...]:
        """Return every distinct explicit or inferred birth country."""
        countries = tuple(
            fact.effective_country
            for fact in self.facts.get("BIRT", ())
            if fact.effective_country
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
            fact.effective_country
            for fact in self.facts.get("DEAT", ())
            if fact.effective_country
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
        """Return a prompt/logging summary without dumping sensitive notes."""
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
        for continuation_line in block[index + 1:]:
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
                pedigree_by_person_family[
                    (person.pointer, first.value.strip())
                ] = pedigree

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
        for child in known_children:
            pedigree = pedigree_by_person_family.get(
                (child.pointer, family.pointer),
                "",
            )
            parents[child.pointer].extend(
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


def load_gedcom(path: str | Path) -> list[IndividualRecord]:
    """Load only INDI summaries from one GEDCOM file.

    ``load_sources`` should be preferred by the CLI because it globally
    disambiguates pointers and retains family/source records for output.
    This compatibility helper is useful for callers and tests.
    """
    source_records = list(iter_gedcom_records(path))
    people = [
        _individual_from_record(
            dataclasses.replace(record, lines=_normalise_record_dates(record.lines))
        )
        for record in source_records
        if record.tag == "INDI"
    ]
    return enrich_relationship_context(people, source_records)


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
            (
                (index, comparator(item, larger[index]))
                for index in available
            ),
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
        country_score = (
            100.0
            if left.effective_country == right.effective_country
            else 0.0
        )
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
    left_names = tuple(
        value for value in (left.name, *left.alternate_names) if value
    )
    right_names = tuple(
        value for value in (right.name, *right.alternate_names) if value
    )
    if left_names and right_names:
        components.append((
            _collection_similarity(left_names, right_names, _text_similarity),
            0.50,
        ))
    if left.birth_date and right.birth_date:
        components.append(
            (_date_similarity(left.birth_date, right.birth_date), 0.20)
        )
    if left.death_date and right.death_date:
        components.append(
            (_date_similarity(left.death_date, right.death_date), 0.10)
        )
    if left.birth_place and right.birth_place:
        components.append(
            (_text_similarity(left.birth_place, right.birth_place), 0.10)
        )
    if left.death_place and right.death_place:
        components.append(
            (_text_similarity(left.death_place, right.death_place), 0.05)
        )
    left_birth_country = _country_from_place(left.birth_place)
    right_birth_country = _country_from_place(right.birth_place)
    if left_birth_country and right_birth_country:
        components.append((
            100.0 if left_birth_country == right_birth_country else 0.0,
            0.05,
        ))
    if left.relationship and right.relationship:
        relationship_score = (
            100.0
            if left.relationship.casefold() == right.relationship.casefold()
            else 20.0
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
        fact.effective_country
        for fact in record.facts.get(tag, ())
        if fact.effective_country
    )
    if values:
        return tuple(dict.fromkeys(values))
    return (fallback,) if fallback else ()


def _event_years(record: IndividualRecord, tag: str, fallback: str) -> set[int]:
    """Return every year represented by an event's source alternatives."""
    values = _event_values(record, tag, "date", fallback)
    return {
        int(year)
        for value in values
        for year in re.findall(r"\b\d{4}\b", value)
    }


def _sets_are_distant(left: set[int], right: set[int], years: int = 5) -> bool:
    """Return whether every cross-set year pairing exceeds a tolerance."""
    return bool(left and right) and all(
        abs(left_year - right_year) > years
        for left_year in left
        for right_year in right
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

    left_names = tuple(
        name for name in (a.full_name, *a.alternate_names) if name
    )
    right_names = tuple(
        name for name in (b.full_name, *b.alternate_names) if name
    )
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

    collection_components = (
        ("occupation", a.occupations, b.occupations, _fact_similarity, 5.0),
        ("residence", a.residences, b.residences, _fact_similarity, 7.0),
        ("family events", a.marriages, b.marriages, _fact_similarity, 8.0),
        ("partners", a.partners, b.partners, _relative_similarity, 12.0),
        ("parents", a.parents, b.parents, _relative_similarity, 10.0),
        ("children", a.children, b.children, _relative_similarity, 8.0),
    )
    collection_scores: dict[str, float] = {}
    for label, left_values, right_values, comparator, weight in collection_components:
        if left_values and right_values:
            collection_score = _collection_similarity(
                left_values,
                right_values,
                comparator,
            )
            collection_scores[label] = collection_score
            add(label, collection_score, weight)

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
        score = sum(
            component_score * weight
            for _, component_score, weight in components
        ) / evidence_weight
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


def _blocking_keys(record: IndividualRecord) -> set[tuple[str, ...]]:
    """Create inexpensive person, event, and family candidate keys.

    Broad initial keys protect spelling variants and sparse records.  Relative
    keys allow a person with no life dates to be found through a documented
    partner, parent, or child.  Dated/placed event keys allow unnamed but
    documented people to be compared without placing every anonymous record in
    one quadratic bucket.
    """
    year = record.birth_year
    buckets = {year // 5} if year is not None else {"?"}
    keys: set[tuple[str, ...]] = set()
    names = [record.full_name, *record.alternate_names]
    for display_name in names:
        if not display_name:
            continue
        given_name, surname = _name_parts(display_name)
        normalised_surname = re.sub(
            r"[^a-z0-9]",
            "",
            surname.casefold(),
        )
        normalised_given = re.sub(
            r"[^a-z0-9]",
            "",
            given_name.casefold(),
        )
        surname_initial = normalised_surname[:1] or "?"
        given_initial = normalised_given[:1] or "?"
        for bucket in buckets:
            keys.add((
                "sn",
                surname_initial,
                "gn",
                given_initial,
                "y",
                str(bucket),
            ))
            keys.add(("sn", surname_initial, "y", str(bucket)))
            keys.add(("gn", given_initial, "y", str(bucket)))
        keys.add(("name", surname_initial, given_initial))

    for label, date_value, place, countries in (
        (
            "birth",
            record.birth_date,
            record.birth_place,
            record.birth_countries,
        ),
        (
            "death",
            record.death_date,
            record.death_place,
            record.death_countries,
        ),
    ):
        event_year = _extract_year(date_value)
        normalised_place = re.sub(r"[^a-z0-9]", "", place.casefold())[:18]
        if event_year is not None:
            keys.add((label, "year", str(event_year)))
            for country in countries:
                keys.add((label, "year-country", str(event_year), country))
        if normalised_place:
            keys.add((label, "place", normalised_place))

    for role, relatives in (
        ("partner", record.partners),
        ("parent", record.parents),
        ("child", record.children),
    ):
        for relative in relatives:
            relative_name = re.sub(
                r"[^a-z0-9]",
                "",
                relative.name.casefold(),
            )
            if relative_name:
                keys.add((role, relative_name[:18]))
                relative_year = _extract_year(relative.birth_date)
                if relative_year is not None:
                    keys.add((role, relative_name[:12], str(relative_year)))
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
            "confidence": {"type": "number"},
            "reasoning": {"type": "string"},
            "preferred_values": {
                "type": "object",
                "properties": {
                    name: {"type": "string"} for name in fact_fields
                },
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


def _get_remote_json(
    url: str,
    bearer_token: str,
    timeout: float,
) -> dict[str, object]:
    """GET JSON metadata without sending any GEDCOM or person information."""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {bearer_token}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RemoteCreditError(
            f"Credit preflight returned HTTP {exc.code}: {detail}"
        ) from exc
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RemoteCreditError(f"Credit preflight failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise RemoteCreditError("Credit preflight returned a non-object response")
    return payload


def check_openrouter_credit(
    api_key: str,
    management_key: Optional[str] = None,
    timeout: float = 15.0,
) -> CreditStatus:
    """Check OpenRouter credit without disclosing any genealogy data.

    A management key can read the account-level purchased and consumed credit
    totals.  A normal inference key can read its own configured remaining
    limit.  If that key is unlimited, the normal-key endpoint cannot prove an
    account balance, so strict policy requires a management key.
    """
    if management_key:
        payload = _get_remote_json(
            "https://openrouter.ai/api/v1/credits",
            management_key,
            timeout,
        )
        data = payload.get("data", {})
        if not isinstance(data, dict):
            data = {}
        try:
            purchased = float(data["total_credits"])
            used = float(data["total_usage"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RemoteCreditError(
                "OpenRouter credit response omitted numeric totals"
            ) from exc
        return CreditStatus(
            provider="openrouter",
            checked=True,
            remaining_usd=purchased - used,
            detail="account balance from the OpenRouter credits endpoint",
        )

    payload = _get_remote_json(
        "https://openrouter.ai/api/v1/key",
        api_key,
        timeout,
    )
    data = payload.get("data", {})
    if not isinstance(data, dict):
        data = {}
    remaining = data.get("limit_remaining")
    if remaining is None:
        return CreditStatus(
            provider="openrouter",
            checked=False,
            remaining_usd=None,
            detail=(
                "the API key is unlimited; set OPENROUTER_MANAGEMENT_KEY "
                "to verify the account balance"
            ),
        )
    try:
        numeric_remaining = max(0.0, float(remaining))
    except (TypeError, ValueError) as exc:
        raise RemoteCreditError(
            "OpenRouter key response omitted a numeric remaining limit"
        ) from exc
    return CreditStatus(
        provider="openrouter",
        checked=False,
        remaining_usd=numeric_remaining,
        detail=(
            "remaining limit reported for the OpenRouter API key; this is "
            "not the account credit balance"
        ),
    )


def ensure_remote_credit(
    provider: str,
    *,
    api_key: Optional[str] = None,
    policy: str = "required",
    minimum_credit_usd: float = 0.01,
    management_key: Optional[str] = None,
    timeout: float = 15.0,
) -> CreditStatus:
    """Enforce a no-person-data remote credit preflight.

    OpenRouter publishes credit endpoints.  OpenAI and Gemini currently expose
    usage/billing dashboards (and some administrative cost reporting), but a
    normal inference key has no documented endpoint for a reliable remaining
    prepaid balance.  Under ``required`` policy those direct providers are
    therefore blocked before the decision prompt is built or transmitted.
    ``best-effort`` is an explicit operator acknowledgement of that limitation.
    """
    if policy not in REMOTE_CREDIT_POLICIES:
        raise ValueError(f"Unknown remote credit policy: {policy}")
    if minimum_credit_usd < 0:
        raise ValueError("minimum remote credit must not be negative")
    if policy == "off":
        return CreditStatus(provider, False, None, "credit check disabled")
    if provider == "openrouter":
        if not api_key:
            raise RemoteCreditError("OPENROUTER_API_KEY is not set")
        status = check_openrouter_credit(
            api_key,
            management_key=management_key,
            timeout=timeout,
        )
    else:
        status = CreditStatus(
            provider=provider,
            checked=False,
            remaining_usd=None,
            detail=(
                f"{provider} does not expose a documented remaining-credit "
                "endpoint to a normal inference API key"
            ),
        )
    if status.remaining_usd is not None:
        if status.remaining_usd < minimum_credit_usd:
            raise RemoteCreditError(
                f"{provider} has ${status.remaining_usd:.4f} available; "
                f"at least ${minimum_credit_usd:.4f} is required"
            )
        if status.checked:
            log.info(
                "%s credit preflight passed: $%.4f available (%s)",
                provider,
                status.remaining_usd,
                status.detail,
            )
            return status
    if policy == "required":
        raise RemoteCreditError(
            f"Cannot verify {provider} credits: {status.detail}. Use "
            "--credit-check best-effort only if you accept this limitation."
        )
    log.warning(
        "Credit preflight is best-effort for %s: %s",
        provider,
        status.detail,
    )
    return status


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
    verdict = _parse_ai_response(str(body.get("response", "")))
    verdict.update({"_provider": "ollama", "_model": model})
    return verdict


def ai_resolve_openai(
    a: IndividualRecord,
    b: IndividualRecord,
    api_key: Optional[str] = None,
    model: str = DEFAULT_OPENAI_MODEL,
    reasoning_effort: str = "low",
    credit_policy: str = "required",
    minimum_credit_usd: float = 0.01,
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
    ensure_remote_credit(
        "openai",
        api_key=key,
        policy=credit_policy,
        minimum_credit_usd=minimum_credit_usd,
    )
    try:
        client = OpenAI(api_key=key)
        request: dict[str, object] = {
            "model": model,
            "instructions": "Return only the JSON object requested by the user.",
            "input": _build_dedup_prompt(a, b),
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "dedup_decision",
                    "strict": True,
                    "schema": _dedup_response_schema(),
                },
            },
        }
        if reasoning_effort != "none":
            request["reasoning"] = {"effort": reasoning_effort}
        response = client.responses.create(**request)
        verdict = _parse_ai_response(response.output_text)
        verdict.update({
            "_provider": "openai",
            "_model": str(getattr(response, "model", None) or model),
        })
        return verdict
    except Exception as exc:  # noqa: BLE001 - SDK exception types vary by version
        raise RuntimeError(f"OpenAI request failed: {exc}") from exc


def ai_resolve_gemini(
    a: IndividualRecord,
    b: IndividualRecord,
    api_key: Optional[str] = None,
    model: str = DEFAULT_GEMINI_MODEL,
    credit_policy: str = "required",
    minimum_credit_usd: float = 0.01,
    **_: object,
) -> dict[str, object]:
    """Call Gemini with ``google-genai``, retaining a legacy SDK fallback."""
    key = (
        api_key
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
    )
    if not key:
        raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is not set")
    ensure_remote_credit(
        "gemini",
        api_key=key,
        policy=credit_policy,
        minimum_credit_usd=minimum_credit_usd,
    )
    try:
        from google import genai as google_genai
    except ImportError:
        google_genai = None
    try:
        if google_genai is not None:
            client = google_genai.Client(api_key=key)
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=_build_dedup_prompt(a, b),
                    config={
                        "temperature": 0,
                        "response_mime_type": "application/json",
                        "response_json_schema": _dedup_response_schema(),
                    },
                )
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
        else:  # pragma: no cover - temporary migration compatibility
            try:
                import google.generativeai as legacy_genai
            except ImportError as exc:
                raise RuntimeError(
                    "Install the optional 'google-genai' package"
                ) from exc
            legacy_genai.configure(api_key=key)
            response = legacy_genai.GenerativeModel(
                model,
                generation_config={"response_mime_type": "application/json"},
            ).generate_content(_build_dedup_prompt(a, b))
        verdict = _parse_ai_response(str(response.text))
        verdict.update({"_provider": "gemini", "_model": model})
        return verdict
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Gemini request failed: {exc}") from exc


def _openrouter_message_text(content: object) -> str:
    """Normalise SDK message content across current and future response types."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict):
            text_value = item.get("text", "")
        else:
            text_value = getattr(item, "text", "")
        if text_value:
            parts.append(str(text_value))
    return "".join(parts)


def ai_resolve_openrouter(
    a: IndividualRecord,
    b: IndividualRecord,
    api_key: Optional[str] = None,
    model: str = DEFAULT_OPENROUTER_MODEL,
    allowed_models: Optional[Sequence[str]] = None,
    cost_quality_tradeoff: int = 7,
    zero_data_retention: bool = True,
    credit_policy: str = "required",
    minimum_credit_usd: float = 0.01,
    credit_timeout: float = 15.0,
    **_: object,
) -> dict[str, object]:
    """Use OpenRouter, optionally delegating selection to its Auto Router."""
    try:
        from openrouter import OpenRouter
    except ImportError as exc:
        raise RuntimeError(
            "Install the optional 'openrouter' package for this backend"
        ) from exc
    key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    if not 0 <= cost_quality_tradeoff <= 10:
        raise ValueError("OpenRouter cost-quality tradeoff must be from 0 to 10")
    ensure_remote_credit(
        "openrouter",
        api_key=key,
        management_key=os.environ.get("OPENROUTER_MANAGEMENT_KEY"),
        policy=credit_policy,
        minimum_credit_usd=minimum_credit_usd,
        timeout=credit_timeout,
    )
    request: dict[str, object] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "Return only the requested valid JSON object.",
            },
            {"role": "user", "content": _build_dedup_prompt(a, b)},
        ],
        "provider": {
            "data_collection": "deny",
            "require_parameters": True,
            **({"zdr": True} if zero_data_retention else {}),
        },
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "dedup_decision",
                "strict": True,
                "schema": _dedup_response_schema(),
            },
        },
        "temperature": 0,
    }
    if model == "openrouter/auto":
        request["plugins"] = [{
            "id": "auto-router",
            "allowed_models": list(allowed_models or DEFAULT_OPENROUTER_MODELS),
            "cost_quality_tradeoff": cost_quality_tradeoff,
        }]
    try:
        with OpenRouter(api_key=key) as client:
            response = client.chat.send(**request)
        choices = getattr(response, "choices", None)
        if not choices:
            raise RuntimeError("OpenRouter returned no choices")
        choice = choices[0]
        choice_error = getattr(choice, "error", None)
        if choice_error:
            raise RuntimeError(f"OpenRouter choice error: {choice_error}")
        if getattr(choice, "finish_reason", None) == "error":
            raise RuntimeError("OpenRouter choice finished with an error")
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None)
        response_text = _openrouter_message_text(content)
        if not response_text.strip():
            raise RuntimeError("OpenRouter returned an empty decision")
        verdict = _parse_ai_response(response_text)
        verdict.update({
            "_provider": "openrouter",
            "_model": str(getattr(response, "model", None) or model),
        })
        return verdict
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"OpenRouter request failed: {exc}") from exc


def ai_resolve_auto(
    a: IndividualRecord,
    b: IndividualRecord,
    openai_model: str = DEFAULT_OPENAI_MODEL,
    gemini_model: str = DEFAULT_GEMINI_MODEL,
    openrouter_model: str = DEFAULT_OPENROUTER_MODEL,
    ollama_model: str = "llama3.1",
    ollama_url: str = "http://localhost:11434",
    reasoning_effort: str = "low",
    allowed_models: Optional[Sequence[str]] = None,
    cost_quality_tradeoff: int = 7,
    zero_data_retention: bool = True,
    credit_policy: str = "required",
    minimum_credit_usd: float = 0.01,
    **_: object,
) -> dict[str, object]:
    """Choose a funded remote route, falling back locally without data loss.

    OpenRouter Auto Router is preferred when configured because it can make a
    current server-side cost/quality decision.  A failed *credit preflight*
    may safely fall through because no person data has yet been sent.  Once an
    inference request is attempted, errors are not retried at another remote
    provider; that avoids sending the same genealogy data to multiple parties.
    """
    candidates: list[tuple[str, Any]] = []
    if os.environ.get("OPENROUTER_API_KEY"):
        candidates.append((
            "openrouter",
            lambda: ai_resolve_openrouter(
                a,
                b,
                model=openrouter_model,
                allowed_models=allowed_models,
                cost_quality_tradeoff=cost_quality_tradeoff,
                zero_data_retention=zero_data_retention,
                credit_policy=credit_policy,
                minimum_credit_usd=minimum_credit_usd,
            ),
        ))
    if os.environ.get("OPENAI_API_KEY"):
        candidates.append((
            "openai",
            lambda: ai_resolve_openai(
                a,
                b,
                model=openai_model,
                reasoning_effort=reasoning_effort,
                credit_policy=credit_policy,
                minimum_credit_usd=minimum_credit_usd,
            ),
        ))
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        candidates.append((
            "gemini",
            lambda: ai_resolve_gemini(
                a,
                b,
                model=gemini_model,
                credit_policy=credit_policy,
                minimum_credit_usd=minimum_credit_usd,
            ),
        ))
    for provider, resolver in candidates:
        try:
            return resolver()
        except RemoteCreditError as exc:
            log.warning(
                "Skipping unfunded/unverifiable %s route: %s",
                provider,
                exc,
            )
    log.info("No verified remote route available; using local Ollama")
    return ai_resolve_ollama(
        a,
        b,
        model=ollama_model,
        base_url=ollama_url,
    )


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
    if backend == "openrouter":
        return ai_resolve_openrouter(a, b, **kwargs)
    if backend == "auto":
        return ai_resolve_auto(a, b, **kwargs)
    raise ValueError(f"Unknown AI backend: {backend}")


def _field_value(record: IndividualRecord, field_name: str) -> str:
    """Read a mergeable summary field by name."""
    return str(getattr(record, field_name, ""))


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
    for tag, values in secondary.extra_fields.items():
        target = merged_extra.setdefault(tag, [])
        target.extend(value for value in values if value not in target)
    merged_facts: dict[str, tuple[GenealogicalFact, ...]] = {
        tag: tuple(values) for tag, values in primary.facts.items()
    }
    for tag, values in secondary.facts.items():
        merged_facts[tag] = tuple(
            dict.fromkeys(merged_facts.get(tag, ()) + tuple(values))
        )
    first_lines = (
        primary.raw_lines
        or _record_to_gedcom_lines(primary).rstrip("\n").splitlines()
    )
    second_lines = (
        secondary.raw_lines
        or _record_to_gedcom_lines(secondary).rstrip("\n").splitlines()
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
        family_links=tuple(
            dict.fromkeys(primary.family_links + secondary.family_links)
        ),
        family_references=tuple(dict.fromkeys(
            primary.family_references + secondary.family_references
        )),
        alternate_names=tuple(dict.fromkeys(
            primary.alternate_names
            + secondary.alternate_names
            + (
                (secondary.full_name,)
                if secondary.full_name
                and secondary.full_name != primary.full_name
                else ()
            )
        )),
        names=tuple(dict.fromkeys(primary.names + secondary.names)),
        source_files=tuple(dict.fromkeys(
            (primary.source_files or (primary.source_file,))
            + (secondary.source_files or (secondary.source_file,))
        )),
        facts=merged_facts,
        marriages=tuple(dict.fromkeys(primary.marriages + secondary.marriages)),
        partners=tuple(dict.fromkeys(primary.partners + secondary.partners)),
        parents=tuple(dict.fromkeys(primary.parents + secondary.parents)),
        children=tuple(dict.fromkeys(primary.children + secondary.children)),
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
    decisions: Optional[list[MergeDecision]] = None,
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
        ai_backend: Resolver name, including ``none`` for deterministic only.
        auto: Skip low-confidence operator confirmation when true.
        ai_kwargs: Provider-specific options forwarded to the resolver.
        pointer_map: Optional mutable map populated with survivor pointers.
        decisions: Optional mutable audit sink.  Entries describe considered
            pairs but do not affect merge behavior.

    Returns:
        Canonical people in stable source order.

    Raises:
        ValueError: The threshold is outside 0 through 100.
    """
    if not 0 <= threshold <= 100:
        raise ValueError("similarity threshold must be between 0 and 100")
    kwargs = ai_kwargs or {}
    by_pointer = {record.pointer: record for record in all_records}
    parent = {record.pointer: record.pointer for record in all_records}
    cluster_members = {
        record.pointer: [record]
        for record in all_records
    }

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
                    "Retaining candidate cluster %s/%s; a source member "
                    "conflicts on %s",
                    root_left,
                    root_right,
                    ", ".join(sorted(pairwise_conflicts)),
                )
                if decisions is not None:
                    decisions.append(MergeDecision(
                        left_pointer=root_left,
                        right_pointer=root_right,
                        score=score,
                        compared_fields=(),
                        conflicts=tuple(sorted(pairwise_conflicts)),
                        disposition="retained-cluster-conflict",
                        reasoning="A member of an existing cluster conflicts.",
                    ))
                continue
        first, second = by_pointer[root_left], by_pointer[root_right]
        verdict: dict[str, object]
        assessment = assess_similarity(first, second)
        if assessment.automatic_merge_safe:
            verdict = {
                "is_duplicate": True,
                "confidence": 1.0,
                "reasoning": (
                    "deterministic multi-field evidence: "
                    + ", ".join(assessment.compared_fields)
                ),
                "preferred_values": {},
            }
        else:
            if assessment.conflicts:
                log.info(
                    "Candidate %s/%s requires review; conflicts: %s",
                    root_left,
                    root_right,
                    ", ".join(assessment.conflicts),
                )
            verdict = _get_ai_verdict(first, second, ai_backend, kwargs)
            if verdict.get("_provider"):
                log.info(
                    "AI decision route: %s/%s",
                    verdict.get("_provider"),
                    verdict.get("_model", "unknown"),
                )
            confidence = float(verdict.get("confidence", 0.0))
            if not auto and confidence < AI_CONFIDENCE_AUTO_ACCEPT:
                verdict = dict(verdict)
                verdict["is_duplicate"] = prompt_operator(first, second)
            elif not bool(verdict.get("is_duplicate", False)):
                if decisions is not None:
                    decisions.append(MergeDecision(
                        left_pointer=root_left,
                        right_pointer=root_right,
                        score=score,
                        compared_fields=assessment.compared_fields,
                        conflicts=assessment.conflicts,
                        disposition="retained",
                        confidence=confidence,
                        provider=str(verdict.get("_provider", ai_backend)),
                        model=str(verdict.get("_model", "")),
                        reasoning=str(verdict.get("reasoning", "")),
                    ))
                continue
        if bool(verdict.get("is_duplicate", False)):
            merged = merge_two_records(first, second, verdict)
            parent[root_right] = root_left
            by_pointer[root_left] = merged
            cluster_members[root_left].extend(cluster_members.pop(root_right))
            log.info(
                "Merged %s <- %s (score %.1f)",
                root_left,
                root_right,
                score,
            )
            if decisions is not None:
                decisions.append(MergeDecision(
                    left_pointer=root_left,
                    right_pointer=root_right,
                    score=score,
                    compared_fields=assessment.compared_fields,
                    conflicts=assessment.conflicts,
                    disposition="merged",
                    confidence=float(verdict.get("confidence", 0.0)),
                    provider=str(verdict.get("_provider", "deterministic")),
                    model=str(verdict.get("_model", "")),
                    reasoning=str(verdict.get("reasoning", "")),
                ))
        elif decisions is not None:
            decisions.append(MergeDecision(
                left_pointer=root_left,
                right_pointer=root_right,
                score=score,
                compared_fields=assessment.compared_fields,
                conflicts=assessment.conflicts,
                disposition="retained-operator",
                confidence=float(verdict.get("confidence", 0.0)),
                provider=str(verdict.get("_provider", ai_backend)),
                model=str(verdict.get("_model", "")),
                reasoning=str(verdict.get("reasoning", "")),
            ))
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
        finding_id=_stable_finding_id(
            code, people, families, evidence, source_files
        ),
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
            role: tuple(dict.fromkeys(
                _canonical_pointer(pointer, pointer_map)
                for pointer in pointers
            ))
            for role, pointers in roles.items()
        }
        families[record.pointer] = canonical_roles
        parent_people = set(
            canonical_roles.get("HUSB", ()) + canonical_roles.get("WIFE", ())
        )
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
    pending = deque([(root_pointer, (root_pointer,))])
    while pending:
        child, path = pending.popleft()
        next_generation = generations[child] + 1
        for parent in sorted(parents.get(child, ())):
            if parent in path:
                cycles.update(path[path.index(parent):] + (parent,))
                continue
            if parent not in generations or next_generation < generations[parent]:
                generations[parent] = next_generation
                pending.append((parent, path + (parent,)))
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
    """Find report-only same-source and cross-source pairs scoring at least 90."""
    buckets: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for index, person in enumerate(people):
        for key in _blocking_keys(person):
            buckets[key].append(index)
    pairs: set[tuple[int, int]] = set()
    for indexes in buckets.values():
        for offset, left in enumerate(indexes):
            pairs.update(
                (min(left, right), max(left, right))
                for right in indexes[offset + 1:]
            )
    results: list[tuple[IndividualRecord, IndividualRecord, MatchAssessment]] = []
    for left, right in sorted(pairs):
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
    return tuple(dict.fromkeys(
        person.source_files or ((person.source_file,) if person.source_file else ())
    ))


def _analyze_married_names(
    people: Sequence[IndividualRecord],
    spouses: Mapping[str, set[str]],
    parents: Mapping[str, set[str]],
    families: Mapping[str, Mapping[str, tuple[str, ...]]],
    generations: Mapping[str, int],
) -> list[QualityFinding]:
    """Conservatively identify primary names that may be married forms."""
    by_pointer = {person.pointer: person for person in people}
    wife_roles = {
        pointer
        for roles in families.values()
        for pointer in roles.get("WIFE", ())
    }
    findings: list[QualityFinding] = []
    maiden_types = {"birth", "maiden", "birth name", "maiden name"}
    married_types = {"married", "married name"}
    for person in people:
        primary = next((name for name in person.names if name.is_primary), None)
        if primary is None:
            continue
        maiden_names = [
            name for name in person.names if name.name_type in maiden_types
        ]
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
                    evidence.extend((
                        f"spouse surnames: {', '.join(sorted(spouse_surnames))}",
                        f"parent surnames: {', '.join(sorted(parent_surnames))}",
                    ))
        if not severity:
            continue
        findings.append(_quality_finding(
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
        ))
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
            findings.append(_quality_finding(
                "DUPLICATE_HEAD", "high", "structural", "Duplicate HEAD records",
                f"The source contains {tags.count('HEAD')} HEAD records.",
                "Retain exactly one leading HEAD record.",
                source_files=(source_name,), generations=generations,
            ))
        if tags.count("TRLR") > 1:
            findings.append(_quality_finding(
                "DUPLICATE_TRLR", "high", "structural", "Duplicate TRLR records",
                f"The source contains {tags.count('TRLR')} TRLR records.",
                "Retain exactly one final TRLR record.",
                source_files=(source_name,), generations=generations,
            ))
        if not records or tags[0] != "HEAD":
            findings.append(_quality_finding(
                "MISSING_HEAD", "high", "structural", "Missing leading HEAD",
                "The source does not begin with a HEAD record.",
                "Export the source again or add a standards-compliant HEAD.",
                source_files=(source_name,), generations=generations,
            ))
        if not records or tags[-1] != "TRLR":
            findings.append(_quality_finding(
                "MISSING_TRLR", "high", "structural", "Missing final TRLR",
                "The source does not end with a TRLR record.",
                "Add one final `0 TRLR` record.", source_files=(source_name,),
                generations=generations,
            ))
        heads = [record for record in records if record.tag == "HEAD"]
        head_lines = heads[0].lines if heads else []
        parsed_head = [parse_gedcom_line(line) for line in head_lines]
        charset = next(
            (line.value for line in parsed_head if line.tag == "CHAR"), ""
        )
        versions = [
            parse_gedcom_line(line).value.strip()
            for block in _top_level_blocks(head_lines)
            if parse_gedcom_line(block[0]).tag == "GEDC"
            for line in block[1:]
            if parse_gedcom_line(line).tag == "VERS"
        ]
        if not charset:
            findings.append(_quality_finding(
                "MISSING_CHARSET", "medium", "structural", "Missing charset",
                "HEAD has no CHAR declaration.",
                "Declare `1 CHAR UTF-8` for portable output.",
                source_files=(source_name,), generations=generations,
            ))
        elif charset.upper() not in {"UTF-8", "UNICODE", "ANSEL", "ASCII"}:
            findings.append(_quality_finding(
                "PORTABILITY_CHARSET", "medium", "structural",
                "Potentially nonportable charset", f"HEAD.CHAR is {charset!r}.",
                "Convert the file to UTF-8 before exchanging it.",
                evidence=(charset,), source_files=(source_name,),
                generations=generations,
            ))
        if not versions:
            findings.append(_quality_finding(
                "MISSING_VERSION", "medium", "structural",
                "Missing GEDCOM version", "HEAD.GEDC.VERS was not found.",
                "Declare GEDCOM 5.5.5 (or intentional 5.5.1 compatibility).",
                source_files=(source_name,), generations=generations,
            ))
        elif versions[0] not in SUPPORTED_GEDCOM_VERSIONS:
            findings.append(_quality_finding(
                "PORTABILITY_VERSION", "medium", "structural",
                "Potentially unsupported GEDCOM version",
                f"HEAD.GEDC.VERS is {versions[0]!r}.",
                "Confirm the source version and test a 5.5.5 or deliberate "
                "5.5.1 export with the destination importer.",
                evidence=(versions[0],), source_files=(source_name,),
                generations=generations,
            ))
        pointers = [record.pointer for record in records if record.pointer]
        pointer_counts: dict[str, int] = defaultdict(int)
        for pointer in pointers:
            pointer_counts[pointer] += 1
        for duplicate in sorted(
            pointer for pointer, count in pointer_counts.items() if count > 1
        ):
            findings.append(_quality_finding(
                "DUPLICATE_XREF", "high", "structural", "Duplicate xref",
                f"The source declares {duplicate} more than once.",
                "Assign one unique xref to each level-zero record.",
                evidence=(duplicate,), source_files=(source_name,),
                generations=generations,
            ))
        for pointer in sorted(set(pointers)):
            if (
                len(pointer) > 22
                or not re.fullmatch(r"@[A-Za-z_][A-Za-z0-9_:-]*@", pointer)
            ):
                findings.append(_quality_finding(
                    "MALFORMED_XREF", "high", "structural", "Malformed xref",
                    f"{pointer!r} is not a portable GEDCOM 5.5.5 xref.",
                    "Replace it with a unique letter-led xref of at most 22 "
                    "characters.",
                    evidence=(pointer,), source_files=(source_name,),
                    generations=generations,
                ))
        declared = {record.pointer for record in records if record.pointer}
        for record in records:
            previous_level = 0
            for line in record.lines:
                parsed = parse_gedcom_line(line)
                if parsed.level > previous_level + 1:
                    findings.append(_quality_finding(
                        "LEVEL_SKIP", "high", "structural",
                        "GEDCOM hierarchy skips a level",
                        f"Record {record.pointer or record.tag} jumps to level "
                        f"{parsed.level} at tag {parsed.tag}.",
                        "Repair the level numbering before importing.",
                        people=((record.pointer,) if record.tag == "INDI" else ()),
                        evidence=(f"level {parsed.level} {parsed.tag}",),
                        source_files=(source_name,),
                        generations=generations,
                    ))
                previous_level = parsed.level
                if len(line.encode("utf-8")) > 255:
                    findings.append(_quality_finding(
                        "LONG_LINE", "medium", "structural",
                        "Line exceeds 255 UTF-8 bytes",
                        f"Record {record.pointer or record.tag} has an overlong line.",
                        "Wrap text with CONC/CONT for broad importer compatibility.",
                        evidence=(
                            f"{parsed.tag}: {len(line.encode('utf-8'))} bytes",
                        ),
                        source_files=(source_name,),
                        generations=generations,
                    ))
            references = {
                pointer
                for line in record.lines
                for pointer in XREF_RE.findall(parse_gedcom_line(line).value)
            }
            for dangling in sorted(references - declared):
                findings.append(_quality_finding(
                    "DANGLING_REFERENCE", "high", "structural",
                    "Dangling GEDCOM reference",
                    f"{record.pointer or record.tag} references undefined {dangling}.",
                    "Restore the referenced record or remove the broken edge.",
                    people=((record.pointer,) if record.tag == "INDI" else ()),
                    families=((record.pointer,) if record.tag == "FAM" else ()),
                    evidence=(dangling,), source_files=(source_name,),
                    generations=generations,
                ))
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
    generations, cycles = ancestor_generations(
        root_pointer, source_records, mapping
    )
    parents, children, spouses, families = _family_graph(source_records, mapping)
    findings: list[QualityFinding] = []
    current_year = dt.date.today().year
    for person in people:
        person_files = _record_source_files(person)
        common = {
            "people": (person.pointer,),
            "source_files": person_files,
            "generations": generations,
        }
        if not person.full_name:
            findings.append(_quality_finding(
                "MISSING_NAME", "high", "person", "Person has no name",
                f"{person.pointer} has no usable NAME value.",
                "Add a sourced NAME or an explicit unknown-name convention.",
                **common,
            ))
        for tag, label in (("BIRT", "birth"), ("DEAT", "death")):
            facts = person.facts.get(tag, ())
            for fact in facts:
                if fact.date and not _valid_quality_date(fact.date):
                    findings.append(_quality_finding(
                        "INVALID_DATE", "high", "chronology",
                        f"Invalid {label} date",
                        f"{person.full_name or person.pointer} has {fact.date!r}.",
                        "Verify the source and encode a valid GEDCOM date.",
                        evidence=(tag, fact.date), **common,
                    ))
            distinct = {fact.summary() for fact in facts if fact.summary()}
            if len(distinct) > 1:
                findings.append(_quality_finding(
                    "ALTERNATIVE_VITAL_EVENTS", "medium", "person",
                    f"Multiple {label} events retained",
                    f"{person.full_name or person.pointer} has conflicting or "
                    f"alternative {label} facts.",
                    "Compare citations; retain alternatives until one is disproved.",
                    evidence=tuple(sorted(distinct)), **common,
                ))
        if not person.birth_date:
            findings.append(_quality_finding(
                "MISSING_BIRTH_DATE", "medium", "person", "Missing birth date",
                f"{person.full_name or person.pointer} has no birth date.",
                "Research a birth, baptism, census, or age-based estimate.", **common,
            ))
        if not person.birth_place:
            findings.append(_quality_finding(
                "MISSING_BIRTH_PLACE", "medium", "person", "Missing birth place",
                f"{person.full_name or person.pointer} has no birth place.",
                "Research and cite the smallest defensible jurisdiction.", **common,
            ))
        if (
            not person.death_date
            and person.birth_year is not None
            and current_year - person.birth_year >= 120
        ):
            findings.append(_quality_finding(
                "MISSING_DEATH_DATE", "medium", "person", "Likely missing death date",
                f"Birth year {person.birth_year} implies age at least 120.",
                "Research death, burial, probate, obituary, or cemetery records.",
                evidence=(str(person.birth_year),), **common,
            ))
        if person.death_date and not person.death_place:
            findings.append(_quality_finding(
                "MISSING_DEATH_PLACE", "low", "person", "Missing death place",
                f"{person.full_name or person.pointer} has a death date but no place.",
                "Research a death certificate, obituary, or burial record.", **common,
            ))
        if not _has_source_citation(person):
            findings.append(_quality_finding(
                "MISSING_CITATION", "medium", "citation", "No source citation",
                "No SOUR structure is retained for "
                f"{person.full_name or person.pointer}.",
                "Add citations to the specific facts they support.", **common,
            ))
        if not person.family_references:
            findings.append(_quality_finding(
                "MISSING_RELATIONSHIPS", "low", "relationship",
                "No family relationships", "No FAMC or FAMS edge is retained.",
                "Confirm whether this person is intentionally unlinked.", **common,
            ))
        if not any(tag == "FAMC" for tag, _ in person.family_references):
            findings.append(_quality_finding(
                "MISSING_PARENT_LINK",
                "medium" if person.pointer == root_pointer else "low",
                "relationship",
                "No parent-family link",
                f"{person.full_name or person.pointer} has no FAMC reference.",
                "Confirm whether the parents are unknown or link a verified "
                "parent family.",
                **common,
            ))
        for fact in person.occupations:
            if not fact.value:
                findings.append(_quality_finding(
                    "INCOMPLETE_OCCUPATION", "low", "person",
                    "Incomplete occupation", "An OCCU fact has no occupation value.",
                    "Add the occupation text and supporting citation.", **common,
                ))
        for fact in person.residences:
            if not fact.date or not fact.place:
                findings.append(_quality_finding(
                    "INCOMPLETE_RESIDENCE", "low", "person",
                    "Incomplete residence",
                    "A RESI fact is missing a date or place.",
                    "Add the known date/place without fabricating precision.",
                    evidence=(fact.summary(),), **common,
                ))
        if (
            person.birth_year is not None
            and person.death_year is not None
            and person.birth_year > person.death_year
        ):
            findings.append(_quality_finding(
                "BIRTH_AFTER_DEATH", "critical", "chronology",
                "Birth occurs after death",
                f"Birth {person.birth_year} is after death {person.death_year}.",
                "Verify both events and their person attribution immediately.",
                evidence=(str(person.birth_year), str(person.death_year)), **common,
            ))
        if (
            person.birth_year is not None
            and person.death_year is not None
            and person.death_year - person.birth_year > 120
        ):
            findings.append(_quality_finding(
                "IMPLAUSIBLE_LIFESPAN", "high", "chronology",
                "Implausible lifespan",
                "The recorded lifespan is "
                f"{person.death_year - person.birth_year} years.",
                "Check for transcription errors or combined identities.", **common,
            ))

    for child_pointer, parent_pointers in parents.items():
        child = by_pointer.get(child_pointer)
        if child is None or child.birth_year is None:
            continue
        for parent_pointer in parent_pointers:
            parent = by_pointer.get(parent_pointer)
            if parent is None or parent.birth_year is None:
                continue
            age = child.birth_year - parent.birth_year
            if age < 12 or age > 80:
                findings.append(_quality_finding(
                    "PARENT_CHILD_CHRONOLOGY", "high", "chronology",
                    "Implausible parent-child chronology",
                    f"{parent.full_name or parent.pointer} would be age {age} "
                    f"at {child.full_name or child.pointer}'s birth.",
                    "Verify the relationship and both birth dates.",
                    people=(parent.pointer, child.pointer), evidence=(str(age),),
                    source_files=_record_source_files(parent)
                    + _record_source_files(child), generations=generations,
                ))
    source_by_family = {
        record.pointer: record for record in source_records if record.tag == "FAM"
    }
    for family_pointer, roles in families.items():
        members = set(
            roles.get("HUSB", ()) + roles.get("WIFE", ()) + roles.get("CHIL", ())
        )
        family_record = source_by_family.get(family_pointer)
        family_source = (
            (family_record.source_file,) if family_record is not None else ()
        )
        if not members:
            findings.append(_quality_finding(
                "EMPTY_FAMILY", "high", "relationship", "Empty family record",
                f"{family_pointer} has no HUSB, WIFE, or CHIL members.",
                "Restore family members or remove the empty family record.",
                families=(family_pointer,), source_files=family_source,
                generations=generations,
            ))
        for role in ("HUSB", "WIFE", "CHIL"):
            expected_tag = "FAMC" if role == "CHIL" else "FAMS"
            for person_pointer in roles.get(role, ()):
                person = by_pointer.get(person_pointer)
                if person is None:
                    continue
                linked_families = {
                    family for tag, family in person.family_references
                    if tag == expected_tag
                }
                if family_pointer not in linked_families:
                    findings.append(_quality_finding(
                        "NONRECIPROCAL_FAMILY_REFERENCE", "medium",
                        "relationship", "Nonreciprocal family reference",
                        f"{family_pointer} lists {person_pointer} as {role}, but "
                        f"the person has no matching {expected_tag} reference.",
                        "Add the reciprocal person-to-family edge after verification.",
                        people=(person_pointer,), families=(family_pointer,),
                        evidence=(role, expected_tag),
                        source_files=family_source + _record_source_files(person),
                        generations=generations,
                    ))
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
                    findings.append(_quality_finding(
                        "INVALID_MARRIAGE_DATE", "high", "chronology",
                        "Invalid marriage date",
                        f"{family_pointer} has marriage date {marriage.date!r}.",
                        "Verify and encode a valid GEDCOM marriage date.",
                        families=(family_pointer,), evidence=(marriage.date,),
                        source_files=family_source, generations=generations,
                    ))
                if marriage_year is None:
                    continue
                for spouse in spouse_people:
                    if spouse.birth_year is not None:
                        marriage_age = marriage_year - spouse.birth_year
                        if marriage_age < 12:
                            findings.append(_quality_finding(
                                "MARRIAGE_BEFORE_MATURITY", "high", "chronology",
                                "Marriage precedes plausible maturity",
                                f"{spouse.full_name or spouse.pointer} would be "
                                f"age {marriage_age} at marriage.",
                                "Verify the marriage, birth date, and family identity.",
                                people=(spouse.pointer,), families=(family_pointer,),
                                evidence=(str(marriage_year), str(marriage_age)),
                                source_files=family_source
                                + _record_source_files(spouse),
                                generations=generations,
                            ))
                    if (
                        spouse.death_year is not None
                        and marriage_year > spouse.death_year
                    ):
                        findings.append(_quality_finding(
                            "MARRIAGE_AFTER_DEATH", "critical", "chronology",
                            "Marriage occurs after death",
                            f"{family_pointer}'s marriage is after "
                            f"{spouse.full_name or spouse.pointer}'s death.",
                            "Verify the marriage, death event, and family identity.",
                            people=(spouse.pointer,), families=(family_pointer,),
                            evidence=(str(marriage_year), str(spouse.death_year)),
                            source_files=family_source + _record_source_files(spouse),
                            generations=generations,
                        ))
    for person in people:
        for tag, family_pointer in person.family_references:
            roles = families.get(family_pointer)
            if roles is None:
                continue
            expected_roles = ("CHIL",) if tag == "FAMC" else ("HUSB", "WIFE")
            listed = any(
                person.pointer in roles.get(role, ())
                for role in expected_roles
            )
            if not listed:
                findings.append(_quality_finding(
                    "NONRECIPROCAL_PERSON_REFERENCE", "medium", "relationship",
                    "Person points to a family that omits them",
                    f"{person.pointer}.{tag} references {family_pointer}, but the "
                    "family does not list that person in the expected role.",
                    "Verify both records and add only the correct reciprocal edge.",
                    people=(person.pointer,), families=(family_pointer,),
                    evidence=(tag,), source_files=_record_source_files(person),
                    generations=generations,
                ))
    if cycles:
        findings.append(_quality_finding(
            "ANCESTRY_CYCLE", "critical", "relationship",
            "Ancestry cycle detected",
            "A person is reachable as their own direct ancestor.",
            "Inspect parent-family links and remove only the erroneous edge.",
            people=tuple(sorted(cycles)), evidence=tuple(sorted(cycles)),
            generations=generations,
        ))
    for left, right, assessment in _quality_duplicate_pairs(people):
        evidence = (
            f"score: {assessment.score:.2f}",
            f"compared: {', '.join(assessment.compared_fields)}",
            f"conflicts: {', '.join(assessment.conflicts) or 'none'}",
            "relatives: "
            f"{', '.join(left.partner_names + right.partner_names) or 'none'}",
        )
        findings.append(_quality_finding(
            "POSSIBLE_DUPLICATE", "high", "duplicate",
            "High-confidence possible duplicate",
            f"{left.full_name or left.pointer} ({left.pointer}) and "
            f"{right.full_name or right.pointer} ({right.pointer}) score "
            f"{assessment.score:.2f}.",
            "Compare original images, citations, relatives, and conflicting "
            "facts manually; this report never merges the pair.",
            people=(left.pointer, right.pointer), evidence=evidence,
            source_files=_record_source_files(left) + _record_source_files(right),
            generations=generations,
        ))
    findings.extend(_analyze_married_names(
        people, spouses, parents, families, generations
    ))
    findings.extend(_analyze_source_structure(sources, generations))
    findings.sort(key=lambda finding: (
        QUALITY_SEVERITY_ORDER[finding.severity],
        not finding.direct_ancestor,
        finding.generation if finding.generation is not None else 10_000,
        _actionability_rank(finding),
        finding.finding_id,
    ))
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
            family_parents = set(
                roles.get("HUSB", ()) + roles.get("WIFE", ())
            )
            for parent_pointer in parent_pointers & family_parents:
                if generations.get(parent_pointer) == child_generation + 1:
                    ancestor_parentage[parent_pointer].add(relationship)
    ancestor_relationships = tuple(
        (
            pointer,
            generation,
            "self" if generation == 0 else ", ".join(sorted(
                ancestor_parentage.get(pointer, {"birth/unspecified"})
            )),
        )
        for pointer, generation in sorted(
            generations.items(), key=lambda item: (item[1], item[0])
        )
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
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace(
        "\r", " "
    ).replace("\n", " ").strip()


def _render_findings(findings: Sequence[QualityFinding]) -> list[str]:
    """Render findings in a stable compact Markdown table."""
    if not findings:
        return ["No findings in this section.", ""]
    lines = [
        "| Severity | ID | Person/family | Recommendation |",
        "|---|---|---|---|",
    ]
    for finding in findings:
        targets = ", ".join(
            finding.person_pointers + finding.family_pointers
        ) or "Tree"
        generation = (
            f"; generation {finding.generation}"
            if finding.generation is not None else ""
        )
        detail = f"{finding.title}: {finding.description} {finding.recommendation}"
        if finding.evidence:
            detail += f" Evidence: {'; '.join(finding.evidence)}."
        if finding.source_files:
            detail += " Sources: " + ", ".join(
                Path(path).name for path in finding.source_files
            ) + "."
        if finding.ai_why:
            detail += f" AI context: {finding.ai_why}"
        if finding.ai_research:
            detail += " AI research suggestions: " + "; ".join(
                finding.ai_research
            )
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
    counts = defaultdict(int)
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
        + _markdown(
            report.ai_backend
            if report.ai_refined
            else "disabled or unavailable"
        ),
        "",
        "## Executive summary",
        "",
        f"{len(report.findings)} findings: "
        + ", ".join(
            f"{counts[level]} {level}"
            for level in ("critical", "high", "medium", "low")
        )
        + ". This report is advisory and made no GEDCOM changes.",
        "",
        "## Top 25 actions",
        "",
    ]
    lines.extend(_render_findings(report.findings[:QUALITY_AI_LIMIT]))
    lines.extend(["## Direct ancestors by generation", ""])
    lines.extend([
        "| Generation | Pointer | Parentage (`PEDI`) |",
        "|---:|---|---|",
    ])
    for pointer, generation, relationship in report.ancestor_relationships:
        lines.append(
            f"| {generation} | `{_markdown(pointer)}` | "
            f"{_markdown(relationship)} |"
        )
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
                finding for finding in report.findings
                if finding.category not in {"duplicate", "married-name", "structural"}
            ]
        )
        lines.extend(_render_findings(selected))
    lines.extend(["## Merge decisions", ""])
    if report.merge_decisions:
        lines.extend([
            "| Pair | Score | Disposition | Evidence/conflicts | Route |",
            "|---|---:|---|---|---|",
        ])
        for decision in report.merge_decisions:
            evidence = ", ".join(decision.compared_fields) or "none"
            conflicts = ", ".join(decision.conflicts) or "none"
            route = "/".join(
                value for value in (decision.provider, decision.model) if value
            )
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


def write_quality_report(report: QualityReport, output_path: str | Path) -> None:
    """Atomically write a quality report without modifying genealogy data.

    Raises:
        OSError: The parent directory is absent or atomic replacement fails.
    """
    path = Path(output_path).resolve()
    if not path.parent.is_dir():
        raise OSError(f"Quality report directory does not exist: {path.parent}")
    payload = render_quality_report(report)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
    try:
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def write_quality_diagnostic(
    output_path: str | Path,
    source_path: str,
    error: BaseException,
) -> None:
    """Atomically write a syntax-failure report when ancestry cannot begin."""
    message = str(error)
    line_match = re.search(r"(?:line|level)\s+(\d+)", message, re.IGNORECASE)
    line_number = line_match.group(1) if line_match else "unknown"
    payload = "\n".join((
        "# GEDCOM Merge Diagnostic Report",
        "",
        "The merge was rejected before any output GEDCOM was written.",
        "",
        f"- Source path: `{_markdown(source_path)}`",
        f"- Line: {_markdown(line_number)}",
        f"- Parser error: {_markdown(message)}",
        "- Remediation: repair the malformed GEDCOM line, validate the source, "
        "and rerun the merge. No AI request was made.",
        "",
    ))
    path = Path(output_path).resolve()
    if not path.parent.is_dir():
        raise OSError(f"Quality report directory does not exist: {path.parent}")
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
    try:
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


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
        "each field concise.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _parse_quality_ai_response(
    response_text: str,
    allowed_ids: set[str],
) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Validate model annotations against deterministic finding IDs."""
    cleaned = re.sub(r"```(?:json)?", "", response_text, flags=re.IGNORECASE)
    try:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        value = json.loads(cleaned[start:end + 1])
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    if not isinstance(value, dict) or not isinstance(value.get("annotations"), list):
        return {}
    annotations: dict[str, tuple[str, tuple[str, ...]]] = {}
    for item in value["annotations"][:QUALITY_AI_LIMIT]:
        if not isinstance(item, dict):
            continue
        finding_id = str(item.get("finding_id", ""))
        if finding_id not in allowed_ids or finding_id in annotations:
            continue
        why = str(item.get("why_this_matters", ""))[:MAX_AI_TEXT].strip()
        raw_suggestions = item.get("research_suggestions", [])
        suggestions = tuple(
            str(suggestion)[:500].strip()
            for suggestion in (
                raw_suggestions if isinstance(raw_suggestions, list) else []
            )[:5]
            if str(suggestion).strip()
        )
        annotations[finding_id] = (why, suggestions)
    return annotations


def ai_refine_quality_ollama(
    report: QualityReport,
    model: str = "llama3.1",
    base_url: str = "http://localhost:11434",
    timeout: float = 60.0,
    **_: object,
) -> tuple[dict[str, tuple[str, tuple[str, ...]]], str, str]:
    """Request one local Ollama annotation pass for the top findings."""
    payload = json.dumps({
        "model": model,
        "prompt": _build_quality_prompt(report),
        "stream": False,
        "format": _quality_response_schema(),
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
        raise RuntimeError(f"Ollama quality request failed: {exc}") from exc
    allowed = {finding.finding_id for finding in report.findings[:QUALITY_AI_LIMIT]}
    annotations = _parse_quality_ai_response(str(body.get("response", "")), allowed)
    return annotations, "ollama", model


def ai_refine_quality_openai(
    report: QualityReport,
    api_key: Optional[str] = None,
    model: str = DEFAULT_OPENAI_MODEL,
    reasoning_effort: str = "low",
    credit_policy: str = "required",
    minimum_credit_usd: float = 0.01,
    **_: object,
) -> tuple[dict[str, tuple[str, tuple[str, ...]]], str, str]:
    """Use schema-constrained OpenAI Responses output after credit preflight."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install the optional 'openai' package") from exc
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    ensure_remote_credit(
        "openai", api_key=key, policy=credit_policy,
        minimum_credit_usd=minimum_credit_usd,
    )
    request: dict[str, object] = {
        "model": model,
        "instructions": "Return only the requested JSON object.",
        "input": _build_quality_prompt(report),
        "store": False,
        "text": {"format": {
            "type": "json_schema", "name": "quality_annotations",
            "strict": True, "schema": _quality_response_schema(),
        }},
    }
    if reasoning_effort != "none":
        request["reasoning"] = {"effort": reasoning_effort}
    try:
        response = OpenAI(api_key=key).responses.create(**request)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"OpenAI quality request failed: {exc}") from exc
    allowed = {finding.finding_id for finding in report.findings[:QUALITY_AI_LIMIT]}
    annotations = _parse_quality_ai_response(response.output_text, allowed)
    used_model = str(getattr(response, "model", None) or model)
    return annotations, "openai", used_model


def ai_refine_quality_gemini(
    report: QualityReport,
    api_key: Optional[str] = None,
    model: str = DEFAULT_GEMINI_MODEL,
    credit_policy: str = "required",
    minimum_credit_usd: float = 0.01,
    **_: object,
) -> tuple[dict[str, tuple[str, tuple[str, ...]]], str, str]:
    """Use Google Gen AI structured JSON after the configured credit gate."""
    key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get(
        "GOOGLE_API_KEY"
    )
    if not key:
        raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is not set")
    ensure_remote_credit(
        "gemini", api_key=key, policy=credit_policy,
        minimum_credit_usd=minimum_credit_usd,
    )
    try:
        from google import genai as google_genai
    except ImportError as exc:
        raise RuntimeError("Install the optional 'google-genai' package") from exc
    client = google_genai.Client(api_key=key)
    try:
        response = client.models.generate_content(
            model=model,
            contents=_build_quality_prompt(report),
            config={
                "temperature": 0,
                "response_mime_type": "application/json",
                "response_json_schema": _quality_response_schema(),
            },
        )
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    allowed = {finding.finding_id for finding in report.findings[:QUALITY_AI_LIMIT]}
    return _parse_quality_ai_response(str(response.text), allowed), "gemini", model


def ai_refine_quality_openrouter(
    report: QualityReport,
    api_key: Optional[str] = None,
    model: str = DEFAULT_OPENROUTER_MODEL,
    allowed_models: Optional[Sequence[str]] = None,
    cost_quality_tradeoff: int = 7,
    zero_data_retention: bool = True,
    credit_policy: str = "required",
    minimum_credit_usd: float = 0.01,
    credit_timeout: float = 15.0,
    **_: object,
) -> tuple[dict[str, tuple[str, tuple[str, ...]]], str, str]:
    """Use OpenRouter structured output after a no-genealogy credit check."""
    try:
        from openrouter import OpenRouter
    except ImportError as exc:
        raise RuntimeError("Install the optional 'openrouter' package") from exc
    key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    ensure_remote_credit(
        "openrouter", api_key=key,
        management_key=os.environ.get("OPENROUTER_MANAGEMENT_KEY"),
        policy=credit_policy, minimum_credit_usd=minimum_credit_usd,
        timeout=credit_timeout,
    )
    request: dict[str, object] = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Return only valid requested JSON."},
            {"role": "user", "content": _build_quality_prompt(report)},
        ],
        "provider": {
            "data_collection": "deny", "require_parameters": True,
            **({"zdr": True} if zero_data_retention else {}),
        },
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "quality_annotations", "strict": True,
                "schema": _quality_response_schema(),
            },
        },
        "temperature": 0,
    }
    if model == "openrouter/auto":
        request["plugins"] = [{
            "id": "auto-router",
            "allowed_models": list(allowed_models or DEFAULT_OPENROUTER_MODELS),
            "cost_quality_tradeoff": cost_quality_tradeoff,
        }]
    with OpenRouter(api_key=key) as client:
        response = client.chat.send(**request)
    choices = getattr(response, "choices", None)
    if not choices:
        raise RuntimeError("OpenRouter returned no quality annotation choices")
    content = getattr(getattr(choices[0], "message", None), "content", None)
    allowed = {finding.finding_id for finding in report.findings[:QUALITY_AI_LIMIT]}
    annotations = _parse_quality_ai_response(
        _openrouter_message_text(content), allowed
    )
    return annotations, "openrouter", str(getattr(response, "model", None) or model)


def refine_quality_report_with_ai(
    report: QualityReport,
    backend: str,
    ai_kwargs: Optional[dict[str, object]] = None,
) -> QualityReport:
    """Add bounded model explanations while preserving deterministic authority.

    Provider failure, an empty response, unknown finding IDs, or explicit
    ``none`` mode returns the original report unchanged.  For remote providers,
    the provider's existing credit and privacy controls run before the prompt
    is constructed or transmitted.

    Args:
        report: Deterministic report to annotate.
        backend: ``ollama``, ``openai``, ``gemini``, ``openrouter``, ``auto``,
            or ``none``.
        ai_kwargs: Provider options already validated by the CLI.

    Returns:
        A replacement immutable report, or the original object on failure.

    Privacy effects:
        A successful non-Ollama route sends only the top 25 bounded finding
        summaries to the selected provider after its configured preflight.

    Mutation guarantees:
        Severity, evidence, identity, order, and recommendations are unchanged.
    """
    if backend == "none" or not report.findings:
        return report
    kwargs = dict(ai_kwargs or {})
    QualityResolver = Callable[
        ..., tuple[dict[str, tuple[str, tuple[str, ...]]], str, str]
    ]
    resolvers: dict[str, QualityResolver] = {
        "ollama": ai_refine_quality_ollama,
        "openai": ai_refine_quality_openai,
        "gemini": ai_refine_quality_gemini,
        "openrouter": ai_refine_quality_openrouter,
    }
    try:
        if backend == "auto":
            # Reuse the same future-facing preference order as identity
            # adjudication, while avoiding retries after genealogy is sent.
            if os.environ.get("OPENROUTER_API_KEY"):
                resolver = ai_refine_quality_openrouter
                kwargs = {
                    "model": kwargs.get("openrouter_model", DEFAULT_OPENROUTER_MODEL),
                    "allowed_models": kwargs.get("allowed_models"),
                    "cost_quality_tradeoff": kwargs.get("cost_quality_tradeoff", 7),
                    "zero_data_retention": kwargs.get("zero_data_retention", True),
                    "credit_policy": kwargs.get("credit_policy", "required"),
                    "minimum_credit_usd": kwargs.get("minimum_credit_usd", 0.01),
                }
            elif os.environ.get("OPENAI_API_KEY"):
                resolver = ai_refine_quality_openai
                kwargs = {
                    "model": kwargs.get("openai_model", DEFAULT_OPENAI_MODEL),
                    "reasoning_effort": kwargs.get("reasoning_effort", "low"),
                    "credit_policy": kwargs.get("credit_policy", "required"),
                    "minimum_credit_usd": kwargs.get("minimum_credit_usd", 0.01),
                }
            elif os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
                resolver = ai_refine_quality_gemini
                kwargs = {
                    "model": kwargs.get("gemini_model", DEFAULT_GEMINI_MODEL),
                    "credit_policy": kwargs.get("credit_policy", "required"),
                    "minimum_credit_usd": kwargs.get("minimum_credit_usd", 0.01),
                }
            else:
                resolver = ai_refine_quality_ollama
                kwargs = {
                    "model": kwargs.get("ollama_model", "llama3.1"),
                    "base_url": kwargs.get("ollama_url", "http://localhost:11434"),
                }
        else:
            resolver = resolvers[backend]
        annotations, provider, model = resolver(report, **kwargs)
        if not annotations:
            return report
        findings = tuple(
            dataclasses.replace(
                finding,
                ai_why=annotations.get(finding.finding_id, ("", ()))[0],
                ai_research=annotations.get(finding.finding_id, ("", ()))[1],
            )
            for finding in report.findings
        )
        privacy = (
            "Local Ollama refinement; no remote transmission"
            if provider == "ollama"
            else f"Bounded top-25 finding summaries sent to {provider}/{model}"
        )
        return dataclasses.replace(
            report, findings=findings, ai_backend=f"{provider}/{model}",
            ai_refined=True, privacy_status=privacy,
        )
    except Exception as exc:  # noqa: BLE001 - provider SDK errors vary by version
        log.warning("Quality AI refinement unavailable; report unchanged: %s", exc)
        return report


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
    """Write an atomic master file while preserving source fact blocks.

    Xrefs, headers, order, dates, and line wrapping may be normalized.  Rooted
    exports intentionally omit unrelated people and families.  The older
    ``source_parsers`` path remains for synthetic/unit callers; DOM elements
    are copied only when they expose ``to_gedcom_string``.

    Args:
        records: Surviving merged people.
        output_path: Destination, which must not be an input file.
        source_parsers: Legacy parser objects for compatibility tests.
        source_documents: Preferred source-preserving parsed documents.
        pointer_map: Duplicate-to-canonical xref rewrites.
        include_individuals: Optional rooted person allowlist.
        include_families: Optional rooted family allowlist.
        gedcom_version: ``5.5.5`` or compatibility mode ``5.5.1``.

    Raises:
        ValueError: The requested version is unsupported.
        OSError: The destination directory or atomic replacement fails.
        GedcomParseError: Emitted 5.5.5 structure fails validation.
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
    synthetic_submitter: list[str] = []
    if source_documents:
        all_source_records = [
            record
            for source in source_documents
            for record in source.records
        ]
        heads = [record for record in all_source_records if record.tag == "HEAD"]
        header_lines = _normalise_header_lines(heads, gedcom_version)
        header_lines = [
            _rewrite_xrefs(line, pointer_map or {}) for line in header_lines
        ]
        synthetic_submitter = _ensure_submitter_record(
            header_lines, all_source_records
        )
        lines.extend(header_lines)
        non_people = [
            record
            for record in all_source_records
            if record.tag not in {"HEAD", "TRLR", "INDI"}
        ]
        ordered_records = (
            [record for record in non_people if record.tag == "SUBM"]
            + [record for record in non_people if record.tag == "FAM"]
            + [
                record
                for record in non_people
                if record.tag not in {"SUBM", "FAM"}
            ]
        )
        survivor_lines = {
            record.pointer: record.raw_lines
            for record in records
            if record.raw_lines
        }
        person_lines: list[str] = []
        for record in records:
            if (
                include_individuals is not None
                and record.pointer not in include_individuals
            ):
                continue
            source_lines = survivor_lines.get(record.pointer) or (
                _record_to_gedcom_lines(record).rstrip("\n").splitlines()
            )
            person_lines.extend(
                _rewrite_xrefs(line, pointer_map or {})
                for line in source_lines
            )
        # Reorder the standard root records into the conventional sequence:
        # HEAD, SUBM, INDI, FAM, then NOTE/OBJE/REPO/SOUR/etc.  This is more
        # interoperable with older importers while preserving every line.
        subm_lines = []
        family_lines = []
        other_lines = []
        subm_lines.extend(synthetic_submitter)
        for record in ordered_records:
            if (
                record.tag == "FAM"
                and include_families is not None
                and record.pointer not in include_families
            ):
                continue
            target = (
                subm_lines if record.tag == "SUBM"
                else family_lines if record.tag == "FAM"
                else other_lines
            )
            target.extend(
                _rewrite_xrefs(line, pointer_map or {})
                for line in record.lines
            )
        lines = (
            lines[:len(header_lines)]
            + subm_lines
            + person_lines
            + family_lines
            + other_lines
        )
    elif source_parsers:
        # Compatibility path for callers of the previous DOM-based API.
        # New CLI calls use source_documents so unknown lines are retained.
        header_records: list[GedcomRecord] = []
        other_lines: list[str] = []
        for parser in source_parsers:
            for element in parser.get_root_child_elements():
                tag = element.get_tag()
                text = element.to_gedcom_string(recursive=True)
                record_lines = text.rstrip("\n").splitlines()
                if tag == "HEAD" and not header_records:
                    header_records.append(GedcomRecord(record_lines, "", 0))
                elif tag not in {"HEAD", "TRLR", "INDI"}:
                    other_lines.extend(record_lines)
        header_lines = _normalise_header_lines(header_records, gedcom_version)
        synthetic_submitter = _ensure_submitter_record(header_lines, [])
        lines.extend(header_lines)
        lines.extend(synthetic_submitter)
        lines.extend(other_lines)
        for record in records:
            if (
                include_individuals is not None
                and record.pointer not in include_individuals
            ):
                continue
            lines.extend(_record_to_gedcom_lines(record).rstrip("\n").splitlines())
    else:
        header_lines = _normalise_header_lines([], gedcom_version)
        synthetic_submitter = _ensure_submitter_record(header_lines, [])
        lines.extend(header_lines)
        lines.extend(synthetic_submitter)
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
    lines = _wrap_long_gedcom_lines(lines)
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
        choices=(
            "none",
            "ollama",
            "openai",
            "gemini",
            "openrouter",
            "auto",
        ),
        default=os.getenv("GEDCOM_AI_BACKEND") or "ollama",
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
        "--quality-root-person",
        help=(
            "Person pointer or unique full name used only to prioritize the "
            "quality report. Defaults to --root-person."
        ),
    )
    parser.add_argument(
        "--quality-report",
        metavar="PATH",
        help="Markdown report path; default is <output-stem>.quality.md.",
    )
    parser.add_argument(
        "--no-quality-report",
        action="store_true",
        help="Disable the advisory quality report and its root requirement.",
    )
    parser.add_argument(
        "--quality-ai",
        action="store_true",
        help="Add bounded AI context to the top 25 deterministic findings.",
    )
    parser.add_argument(
        "--gedcom-version",
        choices=SUPPORTED_GEDCOM_VERSIONS,
        default="5.5.5",
        help="Output version; 5.5.1 is a compatibility fallback.",
    )
    parser.add_argument(
        "--ollama-model",
        default=os.getenv("OLLAMA_MODEL") or "llama3.1",
    )
    parser.add_argument(
        "--ollama-url",
        default=os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434",
    )
    parser.add_argument(
        "--openai-model",
        default=DEFAULT_OPENAI_MODEL,
    )
    parser.add_argument(
        "--gemini-model",
        default=DEFAULT_GEMINI_MODEL,
    )
    parser.add_argument(
        "--openrouter-model",
        default=DEFAULT_OPENROUTER_MODEL,
    )
    parser.add_argument(
        "--openrouter-allowed-model",
        action="append",
        dest="openrouter_allowed_models",
        metavar="PATTERN",
        help=(
            "Allowed OpenRouter Auto Router model pattern; repeat as needed. "
            "Defaults to OpenAI GPT-5 and Google Gemini families."
        ),
    )
    parser.add_argument(
        "--openrouter-cost-quality",
        type=int,
        default=os.getenv("OPENROUTER_COST_QUALITY") or "7",
        metavar="0..10",
        help="OpenRouter Auto Router tradeoff: 0 quality, 10 cost savings.",
    )
    parser.add_argument(
        "--openrouter-zdr",
        action=argparse.BooleanOptionalAction,
        default=(os.getenv("OPENROUTER_ZDR") or "true").casefold()
        in {"1", "true", "yes"},
        help="Require an OpenRouter zero-data-retention route.",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high", "xhigh"),
        default=os.getenv("AI_REASONING_EFFORT", "low"),
    )
    parser.add_argument(
        "--credit-check",
        choices=REMOTE_CREDIT_POLICIES,
        default=os.getenv("REMOTE_CREDIT_CHECK", "required"),
        help=(
            "Credit policy before sending person data. 'required' is safest; "
            "direct OpenAI/Gemini need explicit 'best-effort' because their "
            "normal API keys cannot query remaining prepaid balance."
        ),
    )
    parser.add_argument(
        "--minimum-credit-usd",
        type=float,
        default=os.getenv("MINIMUM_REMOTE_CREDIT_USD") or "0.01",
        help="Minimum verified OpenRouter account balance.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Run the merge command and return a shell exit code.

    The command writes the merged GEDCOM atomically, then writes the advisory
    Markdown report atomically.  A syntax failure writes only a diagnostic
    report.  Optional quality AI failures are nonfatal and cannot modify the
    deterministic report or GEDCOM.

    Args:
        argv: Optional command-line arguments excluding the program name.

    Returns:
        Zero on success and one for input, validation, or output failure.
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    if len(args.input_files) < 2:
        parser.error("At least two input GEDCOM files are required")
    if not 0 <= args.openrouter_cost_quality <= 10:
        parser.error("--openrouter-cost-quality must be between 0 and 10")
    if args.minimum_credit_usd < 0:
        parser.error("--minimum-credit-usd must not be negative")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    paths = [Path(path).resolve() for path in args.input_files]
    output = Path(args.output).resolve()
    quality_enabled = not args.no_quality_report
    quality_path = (
        Path(args.quality_report).resolve()
        if args.quality_report
        else output.with_suffix(".quality.md")
    )
    quality_requested_root = args.quality_root_person or args.root_person
    try:
        if output in paths:
            raise ValueError("Output path must not overwrite an input file")
        if quality_enabled and quality_path in paths:
            raise ValueError("Quality report path must not overwrite an input file")
        if quality_enabled and quality_path == output:
            raise ValueError("Quality report path must differ from GEDCOM output")
        sources = load_sources(paths)
        if quality_enabled and not quality_requested_root:
            raise ValueError(
                "quality reporting requires --quality-root-person or "
                "--root-person; use --no-quality-report to disable reporting"
            )
        all_records = [
            _individual_from_record(record)
            for source in sources
            for record in source.records
            if record.tag == "INDI"
        ]
        all_source_records = [
            record for source in sources for record in source.records
        ]
        all_records = enrich_relationship_context(
            all_records,
            all_source_records,
        )
        kwargs: dict[str, object] = {}
        if args.ai_backend == "ollama":
            kwargs = {"model": args.ollama_model, "base_url": args.ollama_url}
        elif args.ai_backend == "openai":
            kwargs = {
                "model": args.openai_model,
                "reasoning_effort": args.reasoning_effort,
                "credit_policy": args.credit_check,
                "minimum_credit_usd": args.minimum_credit_usd,
            }
        elif args.ai_backend == "gemini":
            kwargs = {
                "model": args.gemini_model,
                "credit_policy": args.credit_check,
                "minimum_credit_usd": args.minimum_credit_usd,
            }
        elif args.ai_backend == "openrouter":
            kwargs = {
                "model": args.openrouter_model,
                "allowed_models": args.openrouter_allowed_models,
                "cost_quality_tradeoff": args.openrouter_cost_quality,
                "zero_data_retention": args.openrouter_zdr,
                "credit_policy": args.credit_check,
                "minimum_credit_usd": args.minimum_credit_usd,
            }
        elif args.ai_backend == "auto":
            kwargs = {
                "openai_model": args.openai_model,
                "gemini_model": args.gemini_model,
                "openrouter_model": args.openrouter_model,
                "ollama_model": args.ollama_model,
                "ollama_url": args.ollama_url,
                "reasoning_effort": args.reasoning_effort,
                "allowed_models": args.openrouter_allowed_models,
                "cost_quality_tradeoff": args.openrouter_cost_quality,
                "zero_data_retention": args.openrouter_zdr,
                "credit_policy": args.credit_check,
                "minimum_credit_usd": args.minimum_credit_usd,
            }
        pointer_map: dict[str, str] = {}
        merge_decisions: list[MergeDecision] = []
        merged = merge_records(
            all_records,
            args.similarity_threshold,
            args.ai_backend,
            args.auto,
            kwargs,
            pointer_map,
            merge_decisions,
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
        quality_report: Optional[QualityReport] = None
        if quality_enabled:
            quality_root_pointer = resolve_root_person(
                quality_requested_root,
                merged,
                [source.pointer_map for source in sources],
                pointer_map,
            )
            quality_report = analyze_quality(
                merged,
                all_source_records,
                sources,
                quality_root_pointer,
                pointer_map=pointer_map,
                merge_decisions=merge_decisions,
                output_file=str(output),
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
        if quality_report is not None:
            if args.quality_ai:
                quality_report = refine_quality_report_with_ai(
                    quality_report,
                    backend=args.ai_backend,
                    ai_kwargs=kwargs,
                )
            write_quality_report(quality_report, quality_path)
        print(
            f"Merge complete: {len(all_records)} individuals -> "
            f"{len(merged)} in {output}"
        )
        if quality_report is not None:
            print(f"Quality report: {quality_path}")
        return 0
    except GedcomParseError as exc:
        if quality_enabled:
            source_path = next(
                (str(path) for path in paths if str(path) in str(exc)),
                str(paths[0]) if paths else "unknown",
            )
            try:
                write_quality_diagnostic(quality_path, source_path, exc)
            except OSError as report_exc:
                log.error("Could not write diagnostic report: %s", report_exc)
        log.error("Merge failed: %s", exc)
        return 1
    except (OSError, ValueError) as exc:
        log.error("Merge failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
