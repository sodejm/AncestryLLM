"""Loss-minimizing GEDCOM rendering and atomic artifact staging.

This module owns the document-to-text boundary.  It retains source fact
blocks, normalizes only the supported envelope details, validates GEDCOM 5.5.5
output, and stages artifacts through the shared atomic publication contract.
"""

from __future__ import annotations

import logging
import re
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ancestryllm.core.publication import StagedFileToken

from ancestryllm.core.cancellation import cancellation_checkpoint
from ancestryllm.gedcom.artifact_publication import stage_text_atomically
from ancestryllm.gedcom.graph import (
    _ROOTED_AUXILIARY_RECORD_TAGS,
    _rewrite_xrefs,
    _rooted_auxiliary_pointer_closure,
)
from ancestryllm.gedcom.identity import (
    IndividualRecord,
    _record_to_gedcom_lines,
    _top_level_blocks,
)
from ancestryllm.gedcom.model import (
    GedcomRecord,
    ParsedSource,
    parse_gedcom_line,
)
from ancestryllm.gedcom.quality import QualityReport, _markdown, render_quality_report
from ancestryllm.gedcom.serializer import (
    SUPPORTED_GEDCOM_VERSIONS,
    wrap_long_gedcom_lines,
)
from ancestryllm.gedcom.validator import validate_gedcom_555 as validate_document_555

log = logging.getLogger(__name__)


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
        "SOUR",
        "DEST",
        "DATE",
        "SUBM",
        "FILE",
        "COPR",
        "LANG",
        "PLAC",
    }
    seen_tags: set[str] = set()
    for header in headers:
        for block in _top_level_blocks(header.lines):
            tag = parse_gedcom_line(block[0]).tag
            if tag in {"GEDC", "CHAR"}:
                continue
            key = tuple(block)
            if key not in seen and not (tag in single_value_tags and tag in seen_tags):
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
    """Validate rendered GEDCOM 5.5.5 text at the publication boundary."""
    validate_document_555(lines, checkpoint=cancellation_checkpoint)


def _wrap_long_gedcom_lines(lines: Sequence[str]) -> list[str]:
    """Wrap rendered physical lines with cancellation checkpoints."""
    return wrap_long_gedcom_lines(lines, checkpoint=cancellation_checkpoint)


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
    source_submitters = {record.pointer for record in source_records if record.tag == "SUBM"}
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


def write_quality_report(
    report: QualityReport,
    output_path: str | Path,
) -> StagedFileToken:
    """Atomically stage a quality report without modifying genealogy data."""
    cancellation_checkpoint()
    path = Path(output_path).resolve()
    if not path.parent.is_dir():
        raise OSError(f"Quality report directory does not exist: {path.parent}")
    return stage_text_atomically(path, render_quality_report(report))


def write_quality_diagnostic(
    output_path: str | Path,
    source_path: str,
    error: BaseException,
) -> None:
    """Atomically write a syntax-failure report when ancestry cannot begin."""
    cancellation_checkpoint()
    message = str(error)
    line_match = re.search(r"(?:line|level)\s+(\d+)", message, re.IGNORECASE)
    line_number = line_match.group(1) if line_match else "unknown"
    payload = "\n".join(
        (
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
        )
    )
    path = Path(output_path).resolve()
    if not path.parent.is_dir():
        raise OSError(f"Quality report directory does not exist: {path.parent}")
    stage_text_atomically(path, payload)


def write_gedcom(
    records: list[IndividualRecord],
    output_path: str | Path,
    source_parsers: Optional[list[Any]] = None,
    source_documents: Optional[list[ParsedSource]] = None,
    pointer_map: Optional[dict[str, str]] = None,
    include_individuals: Optional[set[str]] = None,
    include_families: Optional[set[str]] = None,
    gedcom_version: str = "5.5.5",
) -> StagedFileToken:
    """Render and stage a loss-minimizing GEDCOM artifact."""
    cancellation_checkpoint()
    if gedcom_version not in SUPPORTED_GEDCOM_VERSIONS:
        raise ValueError(
            f"Unsupported GEDCOM version {gedcom_version}; choose from {SUPPORTED_GEDCOM_VERSIONS}"
        )
    out_path = Path(output_path).resolve()
    if out_path.parent and not out_path.parent.exists():
        raise OSError(f"Output directory does not exist: {out_path.parent}")
    lines: list[str] = []
    synthetic_submitter: list[str] = []
    if source_documents:
        pointer_rewrites = pointer_map or {}
        all_source_records = [record for source in source_documents for record in source.records]
        heads = [record for record in all_source_records if record.tag == "HEAD"]
        header_lines = _normalise_header_lines(heads, gedcom_version)
        header_lines = [_rewrite_xrefs(line, pointer_rewrites) for line in header_lines]
        synthetic_submitter = _ensure_submitter_record(header_lines, all_source_records)
        lines.extend(header_lines)
        non_people = [
            record for record in all_source_records if record.tag not in {"HEAD", "TRLR", "INDI"}
        ]
        ordered_records = (
            [record for record in non_people if record.tag == "SUBM"]
            + [record for record in non_people if record.tag == "FAM"]
            + [record for record in non_people if record.tag not in {"SUBM", "FAM"}]
        )
        survivor_lines = {
            record.pointer: record.raw_lines for record in records if record.raw_lines
        }
        person_lines: list[str] = []
        for record in records:
            cancellation_checkpoint()
            if include_individuals is not None and record.pointer not in include_individuals:
                continue
            source_lines = survivor_lines.get(record.pointer) or (
                _record_to_gedcom_lines(record).rstrip("\n").splitlines()
            )
            person_lines.extend(_rewrite_xrefs(line, pointer_rewrites) for line in source_lines)
        rooted_export = include_individuals is not None or include_families is not None
        family_dependency_lines: list[str] = []
        if rooted_export:
            for source_record in ordered_records:
                cancellation_checkpoint()
                if source_record.tag != "FAM":
                    continue
                if include_families is not None and source_record.pointer not in include_families:
                    continue
                family_dependency_lines.extend(
                    _rewrite_xrefs(line, pointer_rewrites) for line in source_record.lines
                )
            retained_auxiliary = _rooted_auxiliary_pointer_closure(
                chain(header_lines, person_lines, family_dependency_lines),
                all_source_records,
                pointer_rewrites,
            )
        else:
            retained_auxiliary = set()
        subm_lines: list[str] = []
        family_lines: list[str] = []
        other_lines: list[str] = []
        subm_lines.extend(synthetic_submitter)
        for source_record in ordered_records:
            cancellation_checkpoint()
            if (
                source_record.tag == "FAM"
                and include_families is not None
                and source_record.pointer not in include_families
            ):
                continue
            rewritten_lines = [
                _rewrite_xrefs(line, pointer_rewrites) for line in source_record.lines
            ]
            if rooted_export and source_record.tag != "FAM":
                rewritten_pointer = parse_gedcom_line(rewritten_lines[0]).xref
                if (
                    source_record.tag not in _ROOTED_AUXILIARY_RECORD_TAGS
                    or rewritten_pointer not in retained_auxiliary
                ):
                    continue
            target = (
                subm_lines
                if source_record.tag == "SUBM"
                else family_lines
                if source_record.tag == "FAM"
                else other_lines
            )
            target.extend(rewritten_lines)
        lines = lines[: len(header_lines)] + subm_lines + person_lines + family_lines + other_lines
    elif source_parsers:
        header_records: list[GedcomRecord] = []
        legacy_other_lines: list[str] = []
        for parser in source_parsers:
            cancellation_checkpoint()
            for element in parser.get_root_child_elements():
                tag = element.get_tag()
                text = element.to_gedcom_string(recursive=True)
                record_lines = text.rstrip("\n").splitlines()
                if tag == "HEAD" and not header_records:
                    header_records.append(GedcomRecord(record_lines, "", 0))
                elif tag not in {"HEAD", "TRLR", "INDI"}:
                    legacy_other_lines.extend(record_lines)
        header_lines = _normalise_header_lines(header_records, gedcom_version)
        synthetic_submitter = _ensure_submitter_record(header_lines, [])
        lines.extend(header_lines)
        lines.extend(synthetic_submitter)
        lines.extend(legacy_other_lines)
        for record in records:
            cancellation_checkpoint()
            if include_individuals is not None and record.pointer not in include_individuals:
                continue
            lines.extend(_record_to_gedcom_lines(record).rstrip("\n").splitlines())
    else:
        header_lines = _normalise_header_lines([], gedcom_version)
        synthetic_submitter = _ensure_submitter_record(header_lines, [])
        lines.extend(header_lines)
        lines.extend(synthetic_submitter)
        for record in records:
            cancellation_checkpoint()
            if include_individuals is not None and record.pointer not in include_individuals:
                continue
            lines.extend(_record_to_gedcom_lines(record).rstrip("\n").splitlines())
    lines.append("0 TRLR")
    lines = _wrap_long_gedcom_lines(lines)
    if gedcom_version == "5.5.5":
        validate_gedcom_555(lines)
    payload = "\n".join(lines) + "\n"
    token = stage_text_atomically(out_path, payload)
    log.info("Wrote %d individuals to %s", len(records), out_path)
    return token


__all__ = [
    "SUPPORTED_GEDCOM_VERSIONS",
    "validate_gedcom_555",
    "wrap_long_gedcom_lines",
    "write_gedcom",
    "write_quality_diagnostic",
    "write_quality_report",
]
