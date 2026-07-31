"""Internal loss-minimizing GEDCOM compatibility engine.

Application commands use :mod:`ancestryllm.gedcom.service`; this module owns
the low-level parser and publication mechanics used by the supported façades.
Source fact blocks remain the data-fidelity authority, although xrefs, dates,
headers, ordering, and line wrapping may be normalized.  A rooted export
intentionally omits people outside the selected connected component.

Deterministic scoring and optional AI may decide identity and choose a summary
value, but conflicting source blocks remain in the output.  Missing facts are
unknown rather than negative evidence, and remote prompts exclude notes,
citations, media, and government identifiers. Public callers use
``ancestryllm.gedcom.service``; symbols in this module are not a stable API.
"""

from __future__ import annotations

import logging
import os
import re
from itertools import chain
from pathlib import Path
from typing import (
    Any,
    Iterator,
    Mapping,
    Optional,
    Sequence,
)

from ancestryllm.core.cancellation import cancellation_checkpoint
from ancestryllm.core.errors import FileIngressError
from ancestryllm.core.ingress import FileIngressPolicy, FileKind, FileSnapshot
from ancestryllm.core.publication import (
    StagedFileToken,
    claim_staged_path,
    cleanup_staged_path,
    is_staging_path,
    publish_staged_bundle,
    staging_path,
    write_staged_text,
)

# Compatibility re-exports remain for the verified ``gedcom.incremental``
# caller and characterization tests. CORE-24 (#166) owns their retirement.
from ancestryllm.gedcom.model import (
    GedcomParseError,
    GedcomRecord,
    ParsedSource,
    parse_gedcom_line,
)
from ancestryllm.gedcom.serializer import (
    SUPPORTED_GEDCOM_VERSIONS,
    wrap_long_gedcom_lines,
)
from ancestryllm.gedcom.validator import validate_gedcom_555 as validate_document_555

from ancestryllm.gedcom.graph import (
    _ROOTED_AUXILIARY_RECORD_TAGS,
    _exact_pointer_references,
    _rooted_auxiliary_pointer_closure,
    _rewrite_xrefs,
    connected_tree_pointers as connected_tree_pointers,
    resolve_root_person as resolve_root_person,
)
from ancestryllm.gedcom.identity import (
    AI_CONFIDENCE_AUTO_ACCEPT as AI_CONFIDENCE_AUTO_ACCEPT,
    COUNTRY_ALIASES as COUNTRY_ALIASES,
    DATE_QUALIFIERS as DATE_QUALIFIERS,
    DEFAULT_DUPLICATE_MAX_ADJUDICATIONS as DEFAULT_DUPLICATE_MAX_ADJUDICATIONS,
    DEFAULT_DUPLICATE_MAX_ADJUDICATIONS_PER_PERSON as DEFAULT_DUPLICATE_MAX_ADJUDICATIONS_PER_PERSON,
    DEFAULT_DUPLICATE_MAX_BUCKET_SIZE as DEFAULT_DUPLICATE_MAX_BUCKET_SIZE,
    DEFAULT_DUPLICATE_MAX_CANDIDATES as DEFAULT_DUPLICATE_MAX_CANDIDATES,
    DEFAULT_DUPLICATE_MAX_PAIRS_PER_PERSON as DEFAULT_DUPLICATE_MAX_PAIRS_PER_PERSON,
    DEFAULT_DUPLICATE_MAX_SCORED_PAIRS as DEFAULT_DUPLICATE_MAX_SCORED_PAIRS,
    DEFAULT_SIMILARITY_THRESHOLD as DEFAULT_SIMILARITY_THRESHOLD,
    DETERMINISTIC_HARD_CONFLICTS as DETERMINISTIC_HARD_CONFLICTS,
    FAMILY_IDENTITY_FACT_TAGS as FAMILY_IDENTITY_FACT_TAGS,
    GEDCOM_MONTHS as GEDCOM_MONTHS,
    IDENTITY_FACT_TAGS as IDENTITY_FACT_TAGS,
    KNOWN_COUNTRY_NAMES as KNOWN_COUNTRY_NAMES,
    MAX_AI_TEXT as MAX_AI_TEXT,
    MAX_DEDUP_PROMPT_TOKENS as MAX_DEDUP_PROMPT_TOKENS,
    MAX_DUPLICATE_RELATIVES_PER_ROLE as MAX_DUPLICATE_RELATIVES_PER_ROLE,
    XREF_RE as XREF_RE,
    DuplicateSearchLimits as DuplicateSearchLimits,
    DuplicateSearchPlan as DuplicateSearchPlan,
    GenealogicalFact as GenealogicalFact,
    IndividualRecord,
    MatchAssessment as MatchAssessment,
    PersonalName as PersonalName,
    RelativeIdentity as RelativeIdentity,
    _blocking_keys as _blocking_keys,
    _dedup_response_schema as _dedup_response_schema,
    _normalise_country as _normalise_country,
    _normalise_record_dates,
    _record_to_gedcom_lines,
    _top_level_blocks,
    assess_similarity as assess_similarity,
    enrich_relationship_context as enrich_relationship_context,
    estimate_duplicate_search as estimate_duplicate_search,
    find_duplicate_candidates as find_duplicate_candidates,
    merge_two_records as merge_two_records,
    normalise_gedcom_date as normalise_gedcom_date,
    similarity_score as similarity_score,
)
from ancestryllm.gedcom.quality import (
    QUALITY_AI_LIMIT as QUALITY_AI_LIMIT,
    QUALITY_DUPLICATE_THRESHOLD as QUALITY_DUPLICATE_THRESHOLD,
    QUALITY_SEVERITY_ORDER as QUALITY_SEVERITY_ORDER,
    QualityFinding as QualityFinding,
    QualityReport,
    _markdown,
    _quality_response_schema as _quality_response_schema,
    analyze_quality as analyze_quality,
    refine_quality_report_with_ai as refine_quality_report_with_ai,
    render_quality_report as render_quality_report,
)

log = logging.getLogger(__name__)


def iter_gedcom_records(
    path: str | Path,
    ingress: FileIngressPolicy | None = None,
    expected: FileSnapshot | None = None,
) -> Iterator[GedcomRecord]:
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
    """Compatibility adapter for internal engine callers; retire in #166."""
    validate_document_555(lines, checkpoint=cancellation_checkpoint)


def _wrap_long_gedcom_lines(lines: Sequence[str]) -> list[str]:
    """Compatibility adapter for internal engine callers; retire in #166."""
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


def load_sources(
    paths: Sequence[str | Path],
    ingress: FileIngressPolicy | None = None,
    expected: Mapping[Path, FileSnapshot] | None = None,
    *,
    validate_structure: bool = False,
) -> list[ParsedSource]:
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
        # Namespace undefined references too.  Otherwise an undefined
        # @I99@ in source A could be accidentally rebound to a defined @I99@
        # in source B during the merge.
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


def _atomic_write_text(path: Path, payload: str) -> StagedFileToken:
    """Write through a publication-owned reservation without clobbering it."""

    if is_staging_path(path):
        return write_staged_text(path, payload)
    staged = staging_path(path)
    try:
        token = write_staged_text(staged, payload)
        claim_staged_path(staged, token)
        publish_staged_bundle(((staged, path),), replace=os.replace)
        return token
    except BaseException:
        cleanup_staged_path(staged)
        raise


def write_quality_report(
    report: QualityReport,
    output_path: str | Path,
) -> StagedFileToken:
    """Atomically write a quality report without modifying genealogy data.

    Raises:
        OSError: The parent directory is absent or atomic replacement fails.
    """
    cancellation_checkpoint()
    path = Path(output_path).resolve()
    if not path.parent.is_dir():
        raise OSError(f"Quality report directory does not exist: {path.parent}")
    payload = render_quality_report(report)
    return _atomic_write_text(path, payload)


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
    _atomic_write_text(path, payload)


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
    """Stage a master file for transactional publication.

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
        OSError: The destination directory or transactional publication fails.
        GedcomParseError: Emitted 5.5.5 structure fails validation.
    """
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
        # Reorder the standard root records into the conventional sequence:
        # HEAD, SUBM, INDI, FAM, then NOTE/OBJE/REPO/SOUR/etc.  This is more
        # interoperable with older importers while preserving every line.
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
        # Compatibility path for callers of the previous DOM-based API.
        # New CLI calls use source_documents so unknown lines are retained.
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
    token = _atomic_write_text(out_path, payload)
    log.info("Wrote %d individuals to %s", len(records), out_path)
    return token
