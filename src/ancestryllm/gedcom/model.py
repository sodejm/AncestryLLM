"""Pure GEDCOM document values and physical-line parsing.

This module deliberately depends only on the Python standard library. Path
ingress, cancellation, application error mapping, and artifact publication
belong to adapters outside the document kernel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_LINE_RE = re.compile(
    r"^(?P<level>[0-9]{1,2})(?:\s+(?P<xref>@[^@\s]+@))?\s+"
    r"(?P<tag>[A-Za-z0-9_]+)(?:\s+(?P<value>.*))?$"
)


class GedcomParseError(ValueError):
    """Raised when GEDCOM text cannot be interpreted without data loss."""


@dataclass(frozen=True, slots=True)
class GedcomDocument:
    """Transport-neutral GEDCOM content before any artifact publication."""

    version: str
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GedcomLine:
    """Parsed metadata for one original GEDCOM line."""

    level: int
    xref: str
    tag: str
    value: str
    raw: str


def parse_gedcom_line(line: str, line_number: int = 0) -> GedcomLine:
    """Parse one physical GEDCOM line without evaluating its contents."""
    raw = line.rstrip("\r\n").lstrip("\ufeff")
    if not re.match(r"^(?:0|[1-9][0-9]?)(?:\s|$)", raw):
        raise GedcomParseError(f"Invalid GEDCOM level {line_number}: {raw!r}")
    match = _LINE_RE.fullmatch(raw)
    if not match:
        raise GedcomParseError(f"Invalid GEDCOM line {line_number}: {raw!r}")
    return GedcomLine(
        level=int(match.group("level")),
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


@dataclass
class ParsedSource:
    """Source records after pointer names have been made globally unique."""

    path: Path
    records: list[GedcomRecord]
    pointer_map: dict[str, str]


__all__ = [
    "GedcomDocument",
    "GedcomLine",
    "GedcomParseError",
    "GedcomRecord",
    "ParsedSource",
    "parse_gedcom_line",
]
