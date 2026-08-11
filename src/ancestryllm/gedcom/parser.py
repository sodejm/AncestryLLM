"""Bounded GEDCOM file ingress and document parsing.

The pure physical-line parser lives in :mod:`ancestryllm.gedcom.model`.  This
module owns the path-backed adapter that enforces ingress limits, validates a
document envelope when requested, and assigns collision-free global xrefs.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

from ancestryllm.core.cancellation import cancellation_checkpoint
from ancestryllm.core.errors import FileIngressError
from ancestryllm.core.ingress import FileIngressPolicy, FileKind, FileSnapshot
from ancestryllm.gedcom.graph import _exact_pointer_references, _rewrite_xrefs
from ancestryllm.gedcom.identity import _normalise_record_dates
from ancestryllm.gedcom.model import (
    GedcomLine,
    GedcomParseError,
    GedcomRecord,
    ParsedSource,
    parse_gedcom_line,
)
from ancestryllm.gedcom.validator import validate_gedcom_555

log = logging.getLogger(__name__)


def iter_gedcom_records(
    path: str | Path,
    ingress: FileIngressPolicy | None = None,
    expected: FileSnapshot | None = None,
) -> Iterator[GedcomRecord]:
    """Yield level-zero GEDCOM records while enforcing bounded file ingress."""
    file_path = Path(path).expanduser().absolute()
    policy = ingress or FileIngressPolicy()
    current: list[str] = []
    sequence = 0
    record_bytes = 0
    record_lines = 0
    record_nesting = 0
    saw_content = False
    for line_number, item in enumerate(
        policy.iter_text_line_items(
            file_path,
            FileKind.GEDCOM,
            count_lines_as_records=False,
            expected=expected,
        ),
        1,
    ):
        cancellation_checkpoint()
        line = item.text.rstrip("\r\n")
        if not line.strip():
            continue
        saw_content = True
        parsed = parse_gedcom_line(line, line_number)
        if parsed.level == 0 and current:
            yield GedcomRecord(current, str(file_path), sequence)
            sequence += 1
            current = []
            record_bytes = 0
            record_lines = 0
            record_nesting = 0
        record_bytes += item.byte_count
        record_lines += 1
        record_nesting = max(record_nesting, parsed.level)
        policy.validate_record(
            FileKind.GEDCOM,
            count=sequence + 1,
            byte_count=record_bytes,
            nesting=record_nesting,
            collection_items=record_lines,
        )
        current.append(line)
    if current:
        yield GedcomRecord(current, str(file_path), sequence)
    if not saw_content:
        raise FileIngressError(
            "FILE_INPUT_EMPTY",
            "The gedcom input must contain at least one record.",
            details={"input_class": FileKind.GEDCOM.value},
        )


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


def _validate_input_document_structure(records: Sequence[GedcomRecord]) -> None:
    """Reject ambiguous document envelopes and duplicate record identifiers."""
    if not records or records[0].tag != "HEAD":
        raise GedcomParseError("GEDCOM input must begin with exactly one HEAD record")
    if records[-1].tag != "TRLR":
        raise GedcomParseError("GEDCOM input must end with exactly one TRLR record")

    head_count = 0
    trailer_count = 0
    declared: set[str] = set()
    for record in records:
        cancellation_checkpoint()
        head_count += record.tag == "HEAD"
        trailer_count += record.tag == "TRLR"
        if not record.pointer:
            continue
        if record.pointer in declared:
            raise GedcomParseError("GEDCOM input contains a duplicate record identifier")
        declared.add(record.pointer)

    if head_count != 1:
        raise GedcomParseError("GEDCOM input must contain exactly one HEAD record")
    if trailer_count != 1:
        raise GedcomParseError("GEDCOM input must contain exactly one TRLR record")


def load_sources(
    paths: Sequence[str | Path],
    ingress: FileIngressPolicy | None = None,
    expected: Mapping[Path, FileSnapshot] | None = None,
    *,
    validate_structure: bool = False,
) -> list[ParsedSource]:
    """Load sources after allocating collision-free global xrefs.

    Undefined references are namespaced as well as declared records so a
    dangling pointer in one source cannot bind to a record in another.
    """
    used: set[str] = set()
    sources: list[ParsedSource] = []
    policy = ingress or FileIngressPolicy()
    for source_number, raw_path in enumerate(paths, 1):
        cancellation_checkpoint()
        path = Path(raw_path).expanduser().absolute()
        try:
            original_records = list(iter_gedcom_records(path, policy, (expected or {}).get(path)))
        except GedcomParseError as exc:
            raise GedcomParseError(f"{path}: {exc}") from exc
        if validate_structure:
            _validate_input_document_structure(original_records)
        pointer_map: dict[str, str] = {}
        all_xrefs = {record.pointer for record in original_records if record.pointer}
        for record in original_records:
            cancellation_checkpoint()
            all_xrefs.update(_exact_pointer_references(record.lines))
        for record in original_records:
            cancellation_checkpoint()
            if record.pointer:
                pointer_map[record.pointer] = _unique_pointer(record.pointer, used, source_number)
        for xref in sorted(all_xrefs - pointer_map.keys()):
            cancellation_checkpoint()
            pointer_map[xref] = _unique_pointer(xref, used, source_number)
        rewritten: list[GedcomRecord] = []
        for record in original_records:
            cancellation_checkpoint()
            lines = _normalise_record_dates(
                [_rewrite_xrefs(line, pointer_map) for line in record.lines]
            )
            rewritten.append(GedcomRecord(lines, str(path), record.sequence))
        sources.append(ParsedSource(path, rewritten, pointer_map))
        log.info("Loaded %d records from %s", len(rewritten), path.name)
    return sources


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
