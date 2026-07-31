"""Supported GEDCOM document parsing façade."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from ancestryllm.gedcom.model import (
    GedcomLine,
    GedcomParseError,
    GedcomRecord,
    ParsedSource,
    parse_gedcom_line,
)
from ancestryllm.gedcom.validator import validate_gedcom_555


def iter_gedcom_records(
    path: str | Path,
    ingress: Any | None = None,
    expected: Any | None = None,
) -> Iterator[GedcomRecord]:
    """Stream records through the bounded file-ingress compatibility adapter.

    Verified callers: ``gedcom.service``, ``gedcom.incremental``, and existing
    parser-façade tests. Retire the private-engine gateway after file-ingress
    orchestration has a permanent application boundary.
    """
    from ancestryllm.gedcom.engine import iter_gedcom_records as _iter_records

    return _iter_records(path, ingress, expected)


def load_sources(
    paths: Sequence[str | Path],
    ingress: Any | None = None,
    expected: Mapping[Path, Any] | None = None,
    *,
    validate_structure: bool = False,
) -> list[ParsedSource]:
    """Load path-backed sources through the bounded compatibility adapter.

    Verified callers: ``gedcom.service`` and ``gedcom.incremental``. Retire the
    private-engine gateway after ingress orchestration is extracted from the
    document kernel.
    """
    from ancestryllm.gedcom.engine import load_sources as _load_sources

    return _load_sources(
        paths,
        ingress,
        expected,
        validate_structure=validate_structure,
    )


__all__ = [
    "GedcomLine",
    "GedcomParseError",
    "GedcomRecord",
    "ParsedSource",
    "iter_gedcom_records",
    "load_sources",
    "parse_gedcom_line",
    "validate_gedcom_555",
]
