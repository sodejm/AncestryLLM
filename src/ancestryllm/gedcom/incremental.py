"""Offline incremental synchronization for :mod:`ancestryllm.gedcom.engine`.

This module deliberately receives the merge module as a runtime dependency.
The engine is injected explicitly so synchronization remains testable and
strictly offline when ``ai_backend`` is ``none``.

Website snapshots are data origins, not evidence sources.  Snapshot ownership
therefore lives only in the private JSON manifest; standard GEDCOM ``SOUR``
records and fact-level citations remain the evidence model in the GEDCOM.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Optional, Sequence


MANIFEST_SCHEMA_VERSION = 1
SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
SUPPORTED_VENDORS = ("ancestry", "geni", "myheritage", "other")
CONTROLLED_TAGS = frozenset(
    {
        "ADOP",
        "MEDI",
        "PEDI",
        "QUAY",
        "ROLE",
        "SEX",
        "STAT",
        "TYPE",
    }
)
ATTACHMENT_TAGS = frozenset({"NOTE", "OBJE", "SOUR"})
SOURCE_ADMIN_TAGS = frozenset({"CHAN", "RIN"})
RECORD_PREFIXES = {
    "FAM": "F",
    "INDI": "I",
    "NOTE": "N",
    "OBJE": "O",
    "REPO": "R",
    "SOUR": "S",
}
EXIT_CODES = {
    "SYNC_CONFIGURATION": 2,
    "SYNC_PARSE": 3,
    "MANIFEST_INVALID": 4,
    "MANIFEST_MASTER_MISMATCH": 4,
    "SYNC_AMBIGUOUS": 5,
    "SYNC_UNSAFE_REMOVAL": 6,
    "SYNC_OUTPUT": 7,
}


class PlainEnglishArgumentParser(argparse.ArgumentParser):
    """Convert argparse failures into the updater's stable error contract."""

    def error(self, message: str) -> None:
        """Raise a remediable configuration error instead of exiting abruptly."""
        raise SyncError(
            "SYNC_CONFIGURATION",
            f"The command-line options are not valid: {message}",
            "The updater cannot safely infer missing paths or synchronization intent.",
            [f"Run `{self.prog} --help` and correct the listed option."],
        )


@dataclass(frozen=True, slots=True)
class SnapshotSpec:
    """One stable website source ID and the newly exported GEDCOM snapshot."""

    source_id: str
    vendor: str
    path: Path
    exported_at: str
    date_basis: str
    sha256: str

    @property
    def snapshot_id(self) -> str:
        """Return a stable content-addressed observation identifier."""
        return f"{self.source_id}:{self.sha256[:20]}"


@dataclass(slots=True)
class SyncStats:
    """Human-readable counters and details for one update operation."""

    added_people: list[str] = field(default_factory=list)
    mapped_people: list[str] = field(default_factory=list)
    unchanged_people: list[str] = field(default_factory=list)
    unresolved_people: list[str] = field(default_factory=list)
    added_facts: list[str] = field(default_factory=list)
    consolidated_facts: list[str] = field(default_factory=list)
    citations_attached: list[str] = field(default_factory=list)
    citations_deduplicated: list[str] = field(default_factory=list)
    source_records_consolidated: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    disappeared_retained: list[str] = field(default_factory=list)
    record_aliases: dict[str, str] = field(default_factory=dict)


class SyncError(RuntimeError):
    """A safe operational failure with plain-English remediation."""

    def __init__(
        self,
        code: str,
        what: str,
        why: str,
        fixes: Sequence[str],
        *,
        details: Sequence[str] = (),
    ) -> None:
        super().__init__(what)
        self.code = code
        self.what = what
        self.why = why
        self.fixes = tuple(fixes)
        self.details = tuple(details)

    @property
    def exit_code(self) -> int:
        """Return the documented shell status for this error category."""
        return EXIT_CODES.get(self.code, 1)

    def render(self) -> str:
        """Return a troubleshooting message without raw genealogy content."""
        lines = [
            f"ERROR [{self.code}]",
            "",
            f"What happened: {self.what}",
            "",
            f"Why it matters: {self.why}",
            "",
            "How to fix it:",
        ]
        lines.extend(f"  {index}. {fix}" for index, fix in enumerate(self.fixes, 1))
        if self.details:
            lines.extend(["", "Details:"])
            lines.extend(f"  - {detail}" for detail in self.details)
        lines.extend(["", "No release files were changed."])
        return "\n".join(lines) + "\n"


def _sha256_file(path: Path) -> str:
    """Hash a file in bounded chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_text(value: str) -> str:
    """Return a SHA-256 digest for deterministic canonical text."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_bytes(value: object) -> bytes:
    """Serialize manifest data deterministically for checksums and output."""
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _normal_space(value: str) -> str:
    """Normalize Unicode and non-semantic horizontal whitespace."""
    return " ".join(unicodedata.normalize("NFC", value).strip().split())


def _normal_place(value: str, core: ModuleType) -> str:
    """Normalize place formatting without removing or reordering jurisdictions."""
    components = [
        re.sub(r"(?<=\w)[.;](?=\s|$)", "", _normal_space(part)).casefold()
        for part in value.split(",")
    ]
    if components:
        components[-1] = core._normalise_country(components[-1])
    return ",".join(components)


def _normal_value(tag: str, value: str, core: ModuleType) -> str:
    """Return a conservative comparison value while preserving source output."""
    value = _normal_space(value)
    if tag == "DATE":
        return core.normalise_gedcom_date(value).upper()
    if tag == "CTRY":
        return core._normalise_country(value)
    if tag == "PLAC":
        return _normal_place(value, core)
    if tag in CONTROLLED_TAGS:
        return value.casefold()
    if tag == "NAME":
        return value.casefold()
    return value


def _relative_lines(lines: Sequence[str], core: ModuleType) -> list[str]:
    """Reassemble continuations and normalize levels relative to the root."""
    if not lines:
        return []
    parsed = [core.parse_gedcom_line(line) for line in lines]
    root_level = parsed[0].level
    output: list[tuple[int, str, str]] = []
    for item in parsed:
        level = item.level - root_level
        if item.tag in {"CONC", "CONT"} and output:
            previous_level, previous_tag, previous_value = output[-1]
            separator = "\n" if item.tag == "CONT" else ""
            output[-1] = (
                previous_level,
                previous_tag,
                previous_value + separator + item.value,
            )
            continue
        output.append((level, item.tag, item.value))
    return [
        f"{level} {tag} {_normal_value(tag, value, core)}".rstrip() for level, tag, value in output
    ]


def _direct_blocks(
    lines: Sequence[str],
    core: ModuleType,
) -> list[list[str]]:
    """Split a structure into direct-child subtrees."""
    if len(lines) < 2:
        return []
    root_level = core.parse_gedcom_line(lines[0]).level
    child_level = root_level + 1
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines[1:]:
        parsed = core.parse_gedcom_line(line)
        if parsed.level == child_level:
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _block_parts(
    block: Sequence[str],
    core: ModuleType,
) -> tuple[list[str], list[list[str]]]:
    """Separate a fact's semantic core from citations, notes, and media."""
    if not block:
        return [], []
    root = block[0]
    core_lines = [root]
    attachments: list[list[str]] = []
    for child in _direct_blocks(block, core):
        tag = core.parse_gedcom_line(child[0]).tag
        if tag in ATTACHMENT_TAGS:
            attachments.append(list(child))
        else:
            core_lines.extend(child)
    return core_lines, attachments


def _block_key(block: Sequence[str], core: ModuleType) -> str:
    """Hash semantic fact content without its independently merged citations."""
    semantic, _ = _block_parts(block, core)
    return _hash_text("\n".join(_relative_lines(semantic, core)))


def _structure_key(lines: Sequence[str], core: ModuleType) -> str:
    """Hash a complete subtree after safe semantic normalization."""
    return _hash_text("\n".join(_relative_lines(lines, core)))


def _citation_identity(lines: Sequence[str], core: ModuleType) -> tuple[str, ...]:
    """Identify the same source location independently of richer attachments."""
    first = core.parse_gedcom_line(lines[0])
    values = {"PAGE": "", "EVEN": "", "ROLE": ""}
    for line in lines[1:]:
        parsed = core.parse_gedcom_line(line)
        if parsed.tag in values and not values[parsed.tag]:
            values[parsed.tag] = _normal_value(parsed.tag, parsed.value, core)
    return (
        _normal_space(first.value),
        values["PAGE"],
        values["EVEN"],
        values["ROLE"],
    )


def _singleton_values(
    lines: Sequence[str],
    core: ModuleType,
) -> dict[str, set[str]]:
    """Return constrained citation values used to detect unsafe unions."""
    result: dict[str, set[str]] = defaultdict(set)
    for line in lines[1:]:
        parsed = core.parse_gedcom_line(line)
        if parsed.tag in {"DATE", "PAGE", "EVEN", "ROLE", "QUAY"}:
            result[parsed.tag].add(_normal_value(parsed.tag, parsed.value, core))
    return result


def _merge_citations(
    left: Sequence[str],
    right: Sequence[str],
    core: ModuleType,
) -> Optional[list[str]]:
    """Merge compatible citations, returning ``None`` on singleton conflicts."""
    if _citation_identity(left, core) != _citation_identity(right, core):
        return None
    left_values = _singleton_values(left, core)
    right_values = _singleton_values(right, core)
    for tag in left_values.keys() | right_values.keys():
        if left_values[tag] and right_values[tag] and (left_values[tag] != right_values[tag]):
            return None
    result = list(left)
    seen = {_structure_key(child, core) for child in _direct_blocks(left, core)}
    singleton_tags = {"DATA", "DATE", "EVEN", "PAGE", "QUAY", "ROLE"}
    for child in _direct_blocks(right, core):
        key = _structure_key(child, core)
        if key in seen:
            continue
        child_tag = core.parse_gedcom_line(child[0]).tag
        merge_index: Optional[int] = None
        merged_child: Optional[list[str]] = None
        if child_tag in singleton_tags:
            blocks = _direct_blocks(result, core)
            for index, existing in enumerate(blocks):
                if core.parse_gedcom_line(existing[0]).tag != child_tag:
                    continue
                left_first = core.parse_gedcom_line(existing[0])
                right_first = core.parse_gedcom_line(child[0])
                if _normal_value(child_tag, left_first.value, core) != _normal_value(
                    child_tag, right_first.value, core
                ):
                    return None
                if child_tag == "DATA":
                    merged_child = _merge_compatible_structure(existing, child, core)
                    if merged_child is None:
                        return None
                else:
                    merged_child = list(existing)
                merge_index = index
                break
        if merge_index is not None and merged_child is not None:
            rebuilt = [result[0]]
            for index, existing in enumerate(_direct_blocks(result, core)):
                rebuilt.extend(merged_child if index == merge_index else existing)
            result = rebuilt
            seen.add(_structure_key(merged_child, core))
            continue
        result.extend(child)
        seen.add(key)
    return result


def _merge_compatible_structure(
    left: Sequence[str],
    right: Sequence[str],
    core: ModuleType,
) -> Optional[list[str]]:
    """Union compatible child structures without duplicating singleton fields.

    This helper is intentionally conservative.  It is primarily used for the
    single ``DATA`` structure beneath a citation.  Different repeatable
    ``TEXT``, ``NOTE``, and ``OBJE`` children survive; conflicting singleton
    values make the parent citations remain separate.
    """
    left_first = core.parse_gedcom_line(left[0])
    right_first = core.parse_gedcom_line(right[0])
    if left_first.tag != right_first.tag or _normal_value(
        left_first.tag, left_first.value, core
    ) != _normal_value(right_first.tag, right_first.value, core):
        return None
    result = list(left)
    existing_blocks = _direct_blocks(result, core)
    seen = {_structure_key(child, core) for child in existing_blocks}
    singleton_tags = {"DATE"}
    for child in _direct_blocks(right, core):
        key = _structure_key(child, core)
        if key in seen:
            continue
        tag = core.parse_gedcom_line(child[0]).tag
        same_tag = [
            existing
            for existing in existing_blocks
            if core.parse_gedcom_line(existing[0]).tag == tag
        ]
        if tag in singleton_tags and same_tag:
            if any(_structure_key(value, core) != key for value in same_tag):
                return None
            continue
        result.extend(child)
        existing_blocks.append(list(child))
        seen.add(key)
    return result


def _merge_same_fact(
    left: Sequence[str],
    right: Sequence[str],
    core: ModuleType,
    stats: SyncStats,
) -> list[str]:
    """Union attachments for two already-proven identical fact cores."""
    semantic, left_attachments = _block_parts(left, core)
    _, right_attachments = _block_parts(right, core)
    attachments = [list(value) for value in left_attachments]
    exact = {_structure_key(value, core) for value in attachments}
    for candidate in right_attachments:
        candidate_key = _structure_key(candidate, core)
        if candidate_key in exact:
            if core.parse_gedcom_line(candidate[0]).tag == "SOUR":
                stats.citations_deduplicated.append(candidate_key[:12])
            continue
        if core.parse_gedcom_line(candidate[0]).tag == "SOUR":
            merged = False
            for index, existing in enumerate(attachments):
                if core.parse_gedcom_line(existing[0]).tag != "SOUR":
                    continue
                combined = _merge_citations(existing, candidate, core)
                if combined is not None:
                    attachments[index] = combined
                    exact.add(_structure_key(combined, core))
                    stats.citations_attached.append(candidate_key[:12])
                    merged = True
                    break
            if merged:
                continue
            stats.citations_attached.append(candidate_key[:12])
        attachments.append(list(candidate))
        exact.add(candidate_key)
    output = list(semantic)
    for attachment in attachments:
        output.extend(attachment)
    return output


def _rewrite_lines(
    lines: Sequence[str],
    pointer_map: Mapping[str, str],
    core: ModuleType,
) -> list[str]:
    """Rewrite exact GEDCOM pointer fields using the core safety rules."""
    return [core._rewrite_xrefs(line, dict(pointer_map)) for line in lines]


def _replace_header_pointer(
    lines: Sequence[str],
    pointer: str,
    core: ModuleType,
) -> list[str]:
    """Replace only a level-zero record's introducing xref."""
    first = core.parse_gedcom_line(lines[0])
    header = f"0 {pointer} {first.tag}"
    if first.value:
        header += f" {first.value}"
    return [header, *lines[1:]]


def _record_semantic_key(
    record: Any,
    pointer_map: Mapping[str, str],
    core: ModuleType,
) -> str:
    """Hash a level-zero record independently of its xref and admin metadata."""
    lines = _rewrite_lines(record.lines, pointer_map, core)
    first = core.parse_gedcom_line(lines[0])
    normalized = [f"0 @RECORD@ {first.tag}"]
    for block in core._top_level_blocks(lines):
        tag = core.parse_gedcom_line(block[0]).tag
        if record.tag == "SOUR" and tag in SOURCE_ADMIN_TAGS:
            continue
        normalized.extend(block)
    return _hash_text("\n".join(_relative_lines(normalized, core)))


def _family_semantic_key(
    record: Any,
    pointer_map: Mapping[str, str],
    core: ModuleType,
) -> str:
    """Hash family identity and fact cores independently of attachments."""
    lines = _rewrite_lines(record.lines, pointer_map, core)
    normalized = ["0 @RECORD@ FAM"]
    for block in core._top_level_blocks(lines):
        semantic, _ = _block_parts(block, core)
        normalized.extend(semantic)
    return _hash_text("\n".join(_relative_lines(normalized, core)))


def _merge_family_records(
    left: Any,
    right: Any,
    pointer: str,
    core: ModuleType,
    stats: SyncStats,
) -> Any:
    """Merge equal family records while preserving unique event attachments."""
    accumulator: dict[str, list[str]] = {}
    order: list[str] = []
    for record in (left, right):
        for block in core._top_level_blocks(record.lines):
            key = _block_key(block, core)
            if key not in accumulator:
                accumulator[key] = list(block)
                order.append(key)
            else:
                accumulator[key] = _merge_same_fact(accumulator[key], block, core, stats)
    lines = [f"0 {pointer} FAM"]
    for key in order:
        lines.extend(accumulator[key])
    return core.GedcomRecord(lines, left.source_file, left.sequence)


def _identifier_values(person: Any, core: ModuleType) -> set[str]:
    """Extract stable standard/vendor identifiers without using free-form notes."""
    values: set[str] = set()
    for block in core._top_level_blocks(person.raw_lines):
        first = core.parse_gedcom_line(block[0])
        if first.tag == "REFN" or first.tag in {
            "_APID",
            "_FSFTID",
            "_MHID",
            "_UID",
        }:
            if first.value.strip():
                values.add(f"{first.tag}:{_normal_space(first.value)}")
    return values


def _identity_fingerprint(person: Any) -> tuple[str, ...]:
    """Return exact stable identity anchors for changed-xref matching."""
    return (
        person.full_name.casefold(),
        person.birth_date.upper(),
        person.birth_place.casefold(),
        person.death_date.upper(),
        person.death_place.casefold(),
        person.gender.casefold(),
    )


def _next_pointer(
    tag: str,
    used: set[str],
    counters: dict[str, int],
) -> str:
    """Allocate a stable, GEDCOM-5.5.5-safe master-controlled xref."""
    prefix = RECORD_PREFIXES.get(tag, "X")
    counter = int(counters.get(prefix, 1))
    pointer = f"@M_{prefix}{counter}@"
    while pointer in used:
        counter += 1
        pointer = f"@M_{prefix}{counter}@"
    counters[prefix] = counter + 1
    used.add(pointer)
    return pointer


def _parse_snapshot_argument(value: str) -> tuple[str, str, Path]:
    """Parse ``SOURCE_ID:VENDOR=PATH`` without restricting path characters."""
    descriptor, separator, raw_path = value.partition("=")
    source_id, vendor_separator, vendor = descriptor.partition(":")
    if not separator or not vendor_separator or not raw_path:
        raise SyncError(
            "SYNC_CONFIGURATION",
            f"Snapshot descriptor {value!r} is incomplete.",
            "The updater cannot distinguish the source, vendor, and file path.",
            [
                "Use --snapshot SOURCE_ID:VENDOR=/absolute/or/relative/file.ged.",
                "Example: --snapshot ancestry-main:ancestry=tree.ged.",
            ],
        )
    if not SOURCE_ID_RE.fullmatch(source_id):
        raise SyncError(
            "SYNC_CONFIGURATION",
            f"Source ID {source_id!r} is not valid.",
            "Stable source IDs are persisted across snapshot generations.",
            [
                "Start the ID with a lowercase letter.",
                "Use only lowercase letters, digits, underscores, or hyphens.",
            ],
        )
    if vendor not in SUPPORTED_VENDORS:
        raise SyncError(
            "SYNC_CONFIGURATION",
            f"Vendor {vendor!r} is not recognized.",
            "Vendor metadata controls reporting and future compatibility profiles.",
            [f"Choose one of: {', '.join(SUPPORTED_VENDORS)}."],
        )
    return source_id, vendor, Path(raw_path).expanduser().resolve()


def _header_export_date(path: Path, core: ModuleType) -> Optional[str]:
    """Read a usable HEAD.DATE without treating it as genealogical evidence."""
    try:
        first = next(core.iter_gedcom_records(path))
    except (StopIteration, OSError, ValueError):
        return None
    if first.tag != "HEAD":
        return None
    for block in core._top_level_blocks(first.lines):
        line = core.parse_gedcom_line(block[0])
        if line.tag == "DATE" and line.value.strip():
            return core.normalise_gedcom_date(line.value.strip())
    return None


def _snapshot_specs(args: argparse.Namespace, core: ModuleType) -> list[SnapshotSpec]:
    """Validate repeated snapshot arguments and derive export timestamps."""
    explicit_dates: dict[str, str] = {}
    for value in args.exported_at or []:
        source_id, separator, timestamp = value.partition("=")
        if not separator or not timestamp:
            raise SyncError(
                "SYNC_CONFIGURATION",
                f"Export date descriptor {value!r} is incomplete.",
                "An export date must be tied to one stable source ID.",
                ["Use --exported-at SOURCE_ID=YYYY-MM-DD or an ISO-8601 timestamp."],
            )
        try:
            dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SyncError(
                "SYNC_CONFIGURATION",
                f"Export date {timestamp!r} is not valid ISO-8601 text.",
                "Incorrect dates make snapshot history misleading.",
                ["Use a value such as 2026-07-17 or 2026-07-17T14:30:00-04:00."],
            ) from exc
        explicit_dates[source_id] = timestamp
    specs: list[SnapshotSpec] = []
    seen: set[str] = set()
    for value in args.snapshot:
        source_id, vendor, path = _parse_snapshot_argument(value)
        if source_id in seen:
            raise SyncError(
                "SYNC_CONFIGURATION",
                f"Source ID {source_id!r} was supplied more than once.",
                "Only one snapshot can replace a source in a single generation.",
                ["Keep one --snapshot entry for each source ID."],
            )
        seen.add(source_id)
        if not path.is_file():
            raise SyncError(
                "SYNC_CONFIGURATION",
                f"Snapshot file does not exist: {path}",
                "No comparison can be performed without the exported GEDCOM.",
                ["Correct the path and confirm that the file is readable."],
                details=(f"Source ID: {source_id}",),
            )
        if source_id in explicit_dates:
            exported_at = explicit_dates[source_id]
            basis = "operator"
        else:
            header_date = _header_export_date(path, core)
            if header_date:
                exported_at = header_date
                basis = "HEAD.DATE"
            else:
                exported_at = dt.datetime.fromtimestamp(
                    path.stat().st_mtime, tz=dt.timezone.utc
                ).isoformat()
                basis = "file-mtime"
        specs.append(
            SnapshotSpec(
                source_id=source_id,
                vendor=vendor,
                path=path,
                exported_at=exported_at,
                date_basis=basis,
                sha256=_sha256_file(path),
            )
        )
    unknown_dates = sorted(set(explicit_dates) - seen)
    if unknown_dates:
        raise SyncError(
            "SYNC_CONFIGURATION",
            "An --exported-at value refers to a source ID with no snapshot.",
            "The date would have no snapshot observation to describe.",
            ["Add the matching --snapshot or remove the unused --exported-at value."],
            details=tuple(f"Unknown source ID: {value}" for value in unknown_dates),
        )
    return specs


def _new_manifest(master: Path, release_root: Path) -> dict[str, Any]:
    """Create an empty schema-v1 manifest for protected baseline seeding."""
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "tree_id": str(uuid.uuid4()),
        "generation": 0,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "master": {"path": str(master), "sha256": _sha256_file(master)},
        "parent_release": None,
        "release_root": str(release_root),
        "active_snapshots": {},
        "snapshots": {},
        "person_bindings": {},
        "record_aliases": {},
        "blocks": {},
        "removed": [],
        "manual_tombstones": [],
        "next_ids": {prefix: 1 for prefix in set(RECORD_PREFIXES.values()) | {"X"}},
        "releases": [],
    }


def _load_manifest(path: Path, master: Path) -> dict[str, Any]:
    """Load and validate a manifest before using any provenance decisions."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncError(
            "MANIFEST_INVALID",
            f"The manifest could not be read as JSON: {path}",
            "Continuing without trustworthy provenance could delete or duplicate data.",
            [
                "Restore manifest.json from the same release as the master GEDCOM.",
                "Do not hand-edit the manifest unless you also validate its schema.",
            ],
            details=(str(exc),),
        ) from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise SyncError(
            "MANIFEST_INVALID",
            "The manifest schema version is missing or unsupported.",
            "The updater cannot safely interpret provenance from another schema.",
            ["Use the manifest emitted by this version of the updater."],
            details=(f"Found schema_version: {value.get('schema_version')!r}",),
        )
    expected = str(value.get("master", {}).get("sha256", ""))
    actual = _sha256_file(master)
    if expected != actual:
        raise SyncError(
            "MANIFEST_MASTER_MISMATCH",
            "The master GEDCOM does not match the supplied manifest.",
            "Block ownership and person bindings may point to a different generation.",
            [
                "Select master.ged and manifest.json from the same release bundle.",
                "If the master was intentionally edited, run the rebase command first.",
            ],
            details=(f"Expected SHA-256: {expected}", f"Actual SHA-256: {actual}"),
        )
    required = {"active_snapshots", "snapshots", "blocks", "next_ids"}
    missing = sorted(required - value.keys())
    if missing:
        raise SyncError(
            "MANIFEST_INVALID",
            "The manifest is missing required synchronization fields.",
            "Incomplete provenance cannot support safe snapshot replacement.",
            ["Restore an unmodified manifest from the release bundle."],
            details=tuple(f"Missing: {item}" for item in missing),
        )
    return value


def _build_update_parser() -> argparse.ArgumentParser:
    """Return the incremental-update command parser."""
    parser = PlainEnglishArgumentParser(
        prog="gedcom_merge.py update",
        description="Update a master GEDCOM from versioned website snapshots.",
    )
    parser.add_argument("--master", required=True)
    parser.add_argument("--manifest")
    parser.add_argument("--initialize-manifest", action="store_true")
    parser.add_argument(
        "--snapshot",
        action="append",
        required=True,
        metavar="SOURCE_ID:VENDOR=PATH",
    )
    parser.add_argument(
        "--exported-at",
        action="append",
        metavar="SOURCE_ID=ISO8601",
    )
    parser.add_argument("--release-root", required=True)
    parser.add_argument("--quality-root-person")
    parser.add_argument("--no-quality-report", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--ai-backend",
        choices=("none", "ollama", "openai", "gemini", "openrouter", "auto"),
        default="none",
    )
    parser.add_argument("--ollama-model", default=os.getenv("OLLAMA_MODEL", "llama3.1"))
    parser.add_argument(
        "--ollama-url",
        default=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
    )
    parser.add_argument("--openai-model", default=os.getenv("OPENAI_MODEL", "gpt-5-mini"))
    parser.add_argument(
        "--gemini-model",
        default=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    )
    parser.add_argument(
        "--openrouter-model",
        default=os.getenv("OPENROUTER_MODEL", "openrouter/auto"),
    )
    parser.add_argument(
        "--credit-check",
        choices=("required", "best-effort", "disabled"),
        default=os.getenv("REMOTE_CREDIT_CHECK", "required"),
    )
    parser.add_argument(
        "--minimum-credit-usd",
        type=float,
        default=float(os.getenv("MINIMUM_REMOTE_CREDIT_USD", "0.01")),
    )
    parser.add_argument("--gedcom-version", choices=("5.5.5", "5.5.1"), default="5.5.5")
    parser.add_argument("--auto", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def _build_rebase_parser() -> argparse.ArgumentParser:
    """Return the explicit external-master rebase parser."""
    parser = PlainEnglishArgumentParser(
        prog="gedcom_merge.py rebase",
        description="Adopt intentional external master edits as protected manual data.",
    )
    parser.add_argument("--master", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--release-root", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--accept-manual-deletions", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def _person_from_record(record: Any, core: ModuleType) -> Any:
    """Build one comparison person while retaining its full source record."""
    return core._individual_from_record(record)


def _match_people(
    sources: Sequence[Any],
    specs: Sequence[SnapshotSpec],
    manifest: dict[str, Any],
    core: ModuleType,
    stats: SyncStats,
    *,
    ai_backend: str = "none",
    ai_kwargs: Optional[dict[str, object]] = None,
) -> tuple[dict[str, str], dict[str, dict[str, str]], list[Any]]:
    """Map snapshot people to stable master pointers conservatively."""
    people_by_source: list[list[Any]] = []
    for source in sources:
        people = [
            _person_from_record(record, core) for record in source.records if record.tag == "INDI"
        ]
        people_by_source.append(core.enrich_relationship_context(people, source.records))
    master_people = people_by_source[0]
    survivors: list[Any] = list(master_people)
    pointer_map = {person.pointer: person.pointer for person in master_people}
    used = {record.pointer for source in sources for record in source.records if record.pointer}
    identifier_index: dict[str, set[str]] = defaultdict(set)
    fingerprint_index: dict[tuple[str, ...], set[str]] = defaultdict(set)
    by_pointer = {person.pointer: person for person in survivors}
    for person in survivors:
        for identifier in _identifier_values(person, core):
            identifier_index[identifier].add(person.pointer)
        fingerprint_index[_identity_fingerprint(person)].add(person.pointer)
    new_bindings: dict[str, dict[str, str]] = {}
    counters = manifest["next_ids"]
    for index, spec in enumerate(specs, 1):
        source = sources[index]
        previous_bindings = manifest.get("person_bindings", {}).get(spec.source_id, {})
        bindings: dict[str, str] = {}
        inverse_pointer_map = {
            global_pointer: original for original, global_pointer in source.pointer_map.items()
        }
        for incoming in people_by_source[index]:
            original = inverse_pointer_map.get(incoming.pointer, incoming.pointer)
            candidate_pointers: set[str] = set()
            previous = previous_bindings.get(original)
            if previous in by_pointer:
                assessment = core.assess_similarity(by_pointer[previous], incoming)
                if not assessment.conflicts and assessment.score >= 78:
                    candidate_pointers.add(previous)
            identifiers = _identifier_values(incoming, core)
            for identifier in identifiers:
                candidate_pointers.update(identifier_index.get(identifier, ()))
            candidate_pointers.update(fingerprint_index.get(_identity_fingerprint(incoming), ()))
            scored: list[tuple[float, str, Any]] = []
            incoming_keys = core._blocking_keys(incoming)
            for survivor in survivors:
                if not incoming_keys.intersection(core._blocking_keys(survivor)):
                    continue
                assessment = core.assess_similarity(survivor, incoming)
                if assessment.score >= 78:
                    scored.append((assessment.score, survivor.pointer, assessment))
                    if assessment.automatic_merge_safe:
                        candidate_pointers.add(survivor.pointer)
            safe_candidates = {
                pointer
                for pointer in candidate_pointers
                if pointer in by_pointer
                and not core.assess_similarity(by_pointer[pointer], incoming).conflicts
            }
            if len(safe_candidates) == 1:
                target = next(iter(safe_candidates))
                collection = stats.unchanged_people if previous == target else stats.mapped_people
                collection.append(f"{spec.source_id}:{original} -> {target}")
            elif len(safe_candidates) > 1:
                target = _next_pointer("INDI", used, counters)
                stats.unresolved_people.append(
                    f"{spec.source_id}:{original} matched multiple people; retained as {target}"
                )
            else:
                safe_scored = [
                    item for item in scored if item[0] >= 95 and item[2].automatic_merge_safe
                ]
                if len(safe_scored) == 1:
                    target = safe_scored[0][1]
                    stats.mapped_people.append(
                        f"{spec.source_id}:{original} -> {target} (score {safe_scored[0][0]:.2f})"
                    )
                elif ai_backend != "none" and scored:
                    best_score, best_pointer, assessment = max(
                        scored, key=lambda item: (item[0], item[1])
                    )
                    target = ""
                    if not assessment.conflicts:
                        try:
                            verdict = core.ai_resolve(
                                by_pointer[best_pointer],
                                incoming,
                                backend=ai_backend,
                                **(ai_kwargs or {}),
                            )
                            if (
                                bool(verdict.get("is_duplicate"))
                                and float(verdict.get("confidence", 0.0)) >= 0.90
                            ):
                                target = best_pointer
                                stats.mapped_people.append(
                                    f"{spec.source_id}:{original} -> {target} "
                                    f"(AI {verdict.get('_provider', ai_backend)}/"
                                    f"{verdict.get('_model', 'provider default')}, "
                                    f"score {best_score:.2f})"
                                )
                        except Exception as exc:
                            stats.conflicts.append(
                                f"AI left {spec.source_id}:{original} unresolved: "
                                f"{type(exc).__name__}: "
                                f"{_normal_space(str(exc))[:240]}"
                            )
                    if not target:
                        target = _next_pointer("INDI", used, counters)
                        stats.added_people.append(f"{spec.source_id}:{original} -> {target}")
                        stats.unresolved_people.append(
                            f"{spec.source_id}:{original} retained separately from "
                            f"{best_pointer} (score {best_score:.2f})"
                        )
                else:
                    target = _next_pointer("INDI", used, counters)
                    stats.added_people.append(f"{spec.source_id}:{original} -> {target}")
                    if scored:
                        stats.unresolved_people.append(
                            f"{spec.source_id}:{original} retained separately from "
                            f"{scored[0][1]} (score {scored[0][0]:.2f})"
                        )
            pointer_map[incoming.pointer] = target
            bindings[original] = target
            if target not in by_pointer:
                replacement = dataclasses.replace(incoming, pointer=target)
                survivors.append(replacement)
                by_pointer[target] = replacement
                for identifier in identifiers:
                    identifier_index[identifier].add(target)
                fingerprint_index[_identity_fingerprint(incoming)].add(target)
        new_bindings[spec.source_id] = bindings
    return pointer_map, new_bindings, survivors


def _map_nonpeople(
    sources: Sequence[Any],
    pointer_map: dict[str, str],
    manifest: dict[str, Any],
    core: ModuleType,
    stats: SyncStats,
) -> tuple[dict[str, str], list[Any]]:
    """Consolidate semantic level-zero records and allocate stable new xrefs."""
    used = {record.pointer for source in sources for record in source.records if record.pointer}
    counters = manifest["next_ids"]
    representatives: dict[tuple[str, str], str] = {}
    canonical_records: dict[str, Any] = {}
    canonical_order: list[str] = []
    ordered_tags = ("REPO", "NOTE", "OBJE", "SOUR", "FAM")
    master_records = [
        record
        for record in sources[0].records
        if record.tag not in {"HEAD", "TRLR", "INDI", "SUBM"}
    ]
    incoming_records = [
        record
        for source in sources[1:]
        for record in source.records
        if record.tag not in {"HEAD", "TRLR", "INDI", "SUBM"}
    ]
    all_records = master_records + incoming_records
    for tag in ordered_tags:
        for record in (item for item in all_records if item.tag == tag):
            is_master = record.source_file == str(sources[0].path)
            if is_master:
                pointer_map.setdefault(record.pointer, record.pointer)
            key_function = _family_semantic_key if tag == "FAM" else _record_semantic_key
            key = key_function(record, pointer_map, core)
            existing = representatives.get((tag, key))
            if existing:
                target = existing
                if record.pointer != target:
                    stats.record_aliases[record.pointer] = target
                if tag == "SOUR" and record.pointer != target:
                    stats.source_records_consolidated.append(f"{record.pointer} -> {target}")
            else:
                target = record.pointer if is_master else _next_pointer(tag, used, counters)
                representatives[(tag, key)] = target
                canonical_order.append(target)
            pointer_map[record.pointer] = target
            rewritten = _replace_header_pointer(
                _rewrite_lines(record.lines, pointer_map, core),
                target,
                core,
            )
            synthetic = core.GedcomRecord(rewritten, record.source_file, record.sequence)
            if target not in canonical_records:
                canonical_records[target] = synthetic
            elif tag == "FAM":
                canonical_records[target] = _merge_family_records(
                    canonical_records[target], synthetic, target, core, stats
                )
    for record in all_records:
        if record.tag in ordered_tags:
            continue
        if record.pointer:
            if record.source_file == str(sources[0].path):
                pointer_map.setdefault(record.pointer, record.pointer)
            else:
                pointer_map.setdefault(
                    record.pointer,
                    _next_pointer(record.tag, used, counters),
                )
            lines = _replace_header_pointer(
                _rewrite_lines(record.lines, pointer_map, core),
                pointer_map[record.pointer],
                core,
            )
        else:
            lines = _rewrite_lines(record.lines, pointer_map, core)
        target_key = record.pointer or f"anonymous:{len(canonical_order)}"
        canonical_records[target_key] = core.GedcomRecord(
            lines, record.source_file, record.sequence
        )
        canonical_order.append(target_key)
    # Re-run every representative through the complete pointer map because
    # early source records may point to families or objects mapped later.
    return pointer_map, [
        core.GedcomRecord(
            _rewrite_lines(record.lines, pointer_map, core),
            record.source_file,
            record.sequence,
        )
        for record in (canonical_records[key] for key in canonical_order)
    ]


def _is_removable_fact(tag: str, core: ModuleType) -> bool:
    """Return whether snapshot omission may remove this uncited structure."""
    return tag in core.IDENTITY_FACT_TAGS or tag in {
        "ANUL",
        "DIV",
        "DIVF",
        "ENGA",
        "MARB",
        "MARC",
        "MARL",
        "MARR",
        "MARS",
        "RESI",
    }


def _block_has_citation(block: Sequence[str], core: ModuleType) -> bool:
    """Return whether a fact carries any standard GEDCOM source citation."""
    return any(core.parse_gedcom_line(line).tag == "SOUR" for line in block[1:])


def _reconcile_person_blocks(
    sources: Sequence[Any],
    specs: Sequence[SnapshotSpec],
    pointer_map: Mapping[str, str],
    manifest: dict[str, Any],
    core: ModuleType,
    stats: SyncStats,
    *,
    initialize: bool,
) -> tuple[list[Any], dict[str, Any]]:
    """Build one canonical person record per stable pointer with provenance."""
    active = dict(manifest.get("active_snapshots", {}))
    for spec in specs:
        active[spec.source_id] = spec.snapshot_id
    active_snapshot_ids = set(active.values())
    block_registry: dict[str, dict[str, Any]] = copy.deepcopy(manifest.get("blocks", {}))
    grouped: dict[str, list[tuple[Optional[SnapshotSpec], Any]]] = defaultdict(list)
    source_spec_by_path = {str(spec.path): spec for spec in specs}
    tombstones = {
        (str(item.get("person", "")), str(item.get("block_hash", "")))
        for item in manifest.get("manual_tombstones", ())
    }
    for source in sources:
        for record in source.records:
            if record.tag != "INDI":
                continue
            target = pointer_map.get(record.pointer, record.pointer)
            grouped[target].append((source_spec_by_path.get(record.source_file), record))
    people: list[Any] = []
    for target, origin_records in grouped.items():
        accumulator: dict[str, list[str]] = {}
        order: list[str] = []
        current_master_keys: set[str] = set()
        person_registry = block_registry.setdefault(target, {})
        for spec, record in origin_records:
            rewritten = _replace_header_pointer(
                _rewrite_lines(record.lines, pointer_map, core), target, core
            )
            for block in core._top_level_blocks(rewritten):
                key = _block_key(block, core)
                tag = core.parse_gedcom_line(block[0]).tag
                if spec is not None and (target, key) in tombstones:
                    stats.conflicts.append(
                        f"{target}:{tag}:{key[:12]} was present in "
                        f"{spec.source_id} but retained as an intentional "
                        "manual deletion"
                    )
                    continue
                if key not in accumulator:
                    accumulator[key] = list(block)
                    order.append(key)
                else:
                    accumulator[key] = _merge_same_fact(accumulator[key], block, core, stats)
                    stats.consolidated_facts.append(f"{target}:{tag}:{key[:12]}")
                entry = person_registry.setdefault(
                    key,
                    {
                        "tag": tag,
                        "kind": "person-block",
                        "protected": [],
                        "observations": [],
                        "first_seen_generation": manifest.get("generation", 0) + 1,
                    },
                )
                if spec is None:
                    current_master_keys.add(key)
                    if initialize and "baseline" not in entry["protected"]:
                        entry["protected"].append("baseline")
                elif spec.snapshot_id not in entry["observations"]:
                    entry["observations"].append(spec.snapshot_id)
                    stats.added_facts.append(f"{target}:{tag}:{spec.source_id}:{key[:12]}")
                entry["last_seen_generation"] = manifest.get("generation", 0) + 1
        retained_order: list[str] = []
        for key in order:
            block = accumulator[key]
            entry = person_registry[key]
            active_observations = set(entry.get("observations", ())) & active_snapshot_ids
            protected = bool(entry.get("protected"))
            tag = entry["tag"]
            absent = key in current_master_keys and not active_observations and not protected
            if absent and _is_removable_fact(tag, core) and not _block_has_citation(block, core):
                stats.removed.append(f"{target}:{tag}:{key[:12]}")
                manifest.setdefault("removed", []).append(
                    {
                        "generation": manifest.get("generation", 0) + 1,
                        "person": target,
                        "block_hash": key,
                        "tag": tag,
                        "reason": "sole-origin uncited fact omitted by active snapshots",
                    }
                )
                continue
            if absent:
                stats.disappeared_retained.append(
                    f"{target}:{tag}:{key[:12]} retained because it is protected, cited, or non-removable"
                )
            retained_order.append(key)
        lines = [f"0 {target} INDI"]
        for key in retained_order:
            lines.extend(accumulator[key])
        record = core.GedcomRecord(lines, str(sources[0].path), len(people))
        people.append(core._individual_from_record(record))
    return people, block_registry


def _seed_snapshot_history(manifest: dict[str, Any], specs: Sequence[SnapshotSpec]) -> None:
    """Record immutable snapshot metadata and replace active source pointers."""
    for spec in specs:
        manifest["snapshots"].setdefault(
            spec.snapshot_id,
            {
                "snapshot_id": spec.snapshot_id,
                "source_id": spec.source_id,
                "vendor": spec.vendor,
                "path": str(spec.path),
                "sha256": spec.sha256,
                "exported_at": spec.exported_at,
                "date_basis": spec.date_basis,
                "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            },
        )
        manifest["active_snapshots"][spec.source_id] = spec.snapshot_id


def _render_list(title: str, values: Sequence[str]) -> list[str]:
    """Render one deterministic update-report section."""
    lines = [f"## {title}", ""]
    if not values:
        return lines + ["None.", ""]
    lines.extend(f"- `{value}`" for value in sorted(dict.fromkeys(values)))
    lines.append("")
    return lines


def _render_update_report(
    manifest: Mapping[str, Any],
    specs: Sequence[SnapshotSpec],
    stats: SyncStats,
    master_sha256: str,
    *,
    ai_backend: str,
    dry_run: bool,
) -> str:
    """Render the complete transparent incremental change report."""
    lines = [
        "# GEDCOM Incremental Update Report",
        "",
        f"- Tree ID: `{manifest['tree_id']}`",
        f"- Generation: {manifest['generation']}",
        f"- Mode: {'dry run; no files written' if dry_run else 'atomic release'}",
        f"- Master SHA-256: `{master_sha256}`",
        f"- AI backend: `{ai_backend}`"
        + (" (offline deterministic)" if ai_backend == "none" else " (opt-in)"),
        "",
        "## Snapshot inputs",
        "",
    ]
    lines.extend(
        f"- `{spec.source_id}` ({spec.vendor}): `{spec.sha256}`; "
        f"exported {spec.exported_at} ({spec.date_basis})"
        for spec in specs
    )
    lines.append("")
    for title, values in (
        ("Added people", stats.added_people),
        ("Mapped people", stats.mapped_people),
        ("Unchanged people", stats.unchanged_people),
        ("Added facts and structures", stats.added_facts),
        ("Consolidated duplicate facts", stats.consolidated_facts),
        ("Citations attached", stats.citations_attached),
        ("Duplicate citations removed", stats.citations_deduplicated),
        ("Source records consolidated", stats.source_records_consolidated),
        ("Conflicts retained", stats.conflicts),
        ("Data actually removed", stats.removed),
        ("Disappeared but retained", stats.disappeared_retained),
        ("Unresolved person mappings", stats.unresolved_people),
    ):
        lines.extend(_render_list(title, values))
    lines.extend(
        [
            "## Interpretation",
            "",
            "Website snapshot provenance is stored in manifest.json. It is not "
            "represented as synthetic GEDCOM evidence citations. Existing source "
            "records and distinct citations remain attached to the canonical fact.",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _write_bytes(path: Path, payload: bytes) -> None:
    """Write a new artifact inside an unpublished staging directory."""
    with path.open("xb") as handle:
        handle.write(payload)


def _failure_report(release_root: Path, error: SyncError) -> None:
    """Best-effort atomic failure report; never mask the original error."""
    try:
        release_root.mkdir(parents=True, exist_ok=True)
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = release_root / f"failed-update-{timestamp}.md"
        payload = "# GEDCOM Incremental Update Failure\n\n" + error.render()
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=release_root, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        os.replace(temporary, destination)
    except OSError:
        return


def _quality_report(
    people: list[Any],
    records: list[Any],
    source: Any,
    root_requested: str,
    output_path: Path,
    core: ModuleType,
) -> Any:
    """Build the existing deterministic report against final canonical records."""
    root = core.resolve_root_person(root_requested, people, [{}], {})
    return core.analyze_quality(
        people,
        records,
        [source],
        root,
        output_file=str(output_path),
    )


def _ai_kwargs(args: argparse.Namespace) -> dict[str, object]:
    """Translate update options into the existing provider-neutral AI contract."""
    shared: dict[str, object] = {
        "credit_policy": args.credit_check,
        "minimum_credit_usd": args.minimum_credit_usd,
    }
    if args.ai_backend == "ollama":
        return {"model": args.ollama_model, "base_url": args.ollama_url}
    if args.ai_backend == "openai":
        return {"model": args.openai_model, **shared}
    if args.ai_backend == "gemini":
        return {"model": args.gemini_model, **shared}
    if args.ai_backend == "openrouter":
        return {"model": args.openrouter_model, **shared}
    if args.ai_backend == "auto":
        return {
            "openai_model": args.openai_model,
            "gemini_model": args.gemini_model,
            "openrouter_model": args.openrouter_model,
            "ollama_model": args.ollama_model,
            "ollama_url": args.ollama_url,
            **shared,
        }
    return {}


def _safe_exception_detail(exc: BaseException) -> str:
    """Remove quoted GEDCOM payload text while retaining path and line context."""
    detail = _normal_space(str(exc))
    detail = re.sub(r":\s*(['\"]).*\1\s*$", ": [line content redacted]", detail)
    return detail[:500]


def _perform_update(args: argparse.Namespace, core: ModuleType) -> int:
    """Execute one offline update and publish an atomic generation bundle."""
    master = Path(args.master).expanduser().resolve()
    release_root = Path(args.release_root).expanduser().resolve()
    if not master.is_file():
        raise SyncError(
            "SYNC_CONFIGURATION",
            f"Master GEDCOM does not exist: {master}",
            "The updater needs an immutable starting generation.",
            ["Select the master.ged file from the current release bundle."],
        )
    if args.initialize_manifest and args.manifest:
        raise SyncError(
            "SYNC_CONFIGURATION",
            "--initialize-manifest and --manifest were supplied together.",
            "Initialization and continuation have different provenance guarantees.",
            ["Use --initialize-manifest only for a master with no manifest."],
        )
    if not args.initialize_manifest and not args.manifest:
        raise SyncError(
            "SYNC_CONFIGURATION",
            "No manifest was supplied for an existing synchronization tree.",
            "Without it, snapshot ownership and safe removals cannot be proven.",
            [
                "Pass --manifest from the same release as --master.",
                "Or use --initialize-manifest once for a legacy master.",
            ],
        )
    if not args.no_quality_report and not args.quality_root_person:
        raise SyncError(
            "SYNC_CONFIGURATION",
            "Quality reporting is enabled but no quality root person was supplied.",
            "Direct-ancestor priorities require an unambiguous root.",
            [
                "Add --quality-root-person with a master pointer or unique name.",
                "Or add --no-quality-report.",
            ],
        )
    specs = _snapshot_specs(args, core)
    manifest = (
        _new_manifest(master, release_root)
        if args.initialize_manifest
        else _load_manifest(Path(args.manifest).expanduser().resolve(), master)
    )
    if all(
        manifest.get("active_snapshots", {}).get(spec.source_id) == spec.snapshot_id
        for spec in specs
    ):
        print(
            "No update was needed: every supplied snapshot is already active "
            "with the same SHA-256 checksum. No release files were changed."
        )
        return 0
    try:
        sources = core.load_sources([master, *(spec.path for spec in specs)])
    except (OSError, ValueError) as exc:
        raise SyncError(
            "SYNC_PARSE",
            "A master or snapshot GEDCOM could not be parsed safely.",
            "Publishing a partial generation could break relationships or citations.",
            ["Open the named file and repair the reported GEDCOM line, then retry."],
            details=(str(exc),),
        ) from exc
    stats = SyncStats()
    pointer_map, bindings, _ = _match_people(
        sources,
        specs,
        manifest,
        core,
        stats,
        ai_backend=args.ai_backend,
        ai_kwargs=_ai_kwargs(args),
    )
    pointer_map, nonpeople = _map_nonpeople(sources, pointer_map, manifest, core, stats)
    people, block_registry = _reconcile_person_blocks(
        sources,
        specs,
        pointer_map,
        manifest,
        core,
        stats,
        initialize=args.initialize_manifest,
    )
    nonpeople = [
        core.GedcomRecord(
            _rewrite_lines(record.lines, pointer_map, core),
            record.source_file,
            record.sequence,
        )
        for record in nonpeople
    ]
    family_records = [record for record in nonpeople if record.tag == "FAM"]
    people = core.enrich_relationship_context(people, family_records)
    head = next(
        (record for record in sources[0].records if record.tag == "HEAD"),
        core.GedcomRecord(["0 HEAD"], str(master), 0),
    )
    output_records = [head, *nonpeople]
    output_source = core.ParsedSource(master, output_records, {})
    next_generation = int(manifest.get("generation", 0)) + 1
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    release_name = f"g{next_generation:04d}-{timestamp}"
    manifest["generation"] = next_generation
    manifest["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    manifest["blocks"] = block_registry
    manifest["person_bindings"].update(bindings)
    manifest["record_aliases"].update(stats.record_aliases)
    _seed_snapshot_history(manifest, specs)
    if args.dry_run:
        report = _render_update_report(
            manifest,
            specs,
            stats,
            "not-written-dry-run",
            ai_backend=args.ai_backend,
            dry_run=True,
        )
        print(report, end="")
        return 0
    release_root.mkdir(parents=True, exist_ok=True)
    final_dir = release_root / release_name
    if final_dir.exists():
        raise SyncError(
            "SYNC_OUTPUT",
            f"Release directory already exists: {final_dir}",
            "Existing releases are immutable and must never be overwritten.",
            ["Wait one second and retry, or choose a different --release-root."],
        )
    staging = Path(tempfile.mkdtemp(prefix=".gedcom-sync-", dir=release_root))
    try:
        staged_master = staging / "master.ged"
        core.write_gedcom(
            people,
            staged_master,
            source_documents=[output_source],
            pointer_map=dict(pointer_map),
            gedcom_version=args.gedcom_version,
        )
        master_sha = _sha256_file(staged_master)
        parent_master = copy.deepcopy(manifest.get("master"))
        parent_manifest_path = (
            str(Path(args.manifest).expanduser().resolve()) if args.manifest else None
        )
        parent_manifest_sha = (
            _sha256_file(Path(parent_manifest_path)) if parent_manifest_path else None
        )
        manifest["parent_release"] = {
            "generation": next_generation - 1,
            "master": parent_master,
            "manifest_path": parent_manifest_path,
            "manifest_sha256": parent_manifest_sha,
        }
        manifest["master"] = {
            "path": str(final_dir / "master.ged"),
            "sha256": master_sha,
        }
        manifest["releases"].append(
            {
                "generation": next_generation,
                "path": str(final_dir),
                "master_sha256": master_sha,
                "created_at": manifest["updated_at"],
            }
        )
        report_text = _render_update_report(
            manifest,
            specs,
            stats,
            master_sha,
            ai_backend=args.ai_backend,
            dry_run=False,
        )
        _write_bytes(staging / "update.md", report_text.encode("utf-8"))
        if not args.no_quality_report:
            quality = _quality_report(
                people,
                output_records,
                output_source,
                args.quality_root_person,
                final_dir / "master.ged",
                core,
            )
            _write_bytes(
                staging / "quality.md",
                core.render_quality_report(quality).encode("utf-8"),
            )
        else:
            _write_bytes(
                staging / "quality.md",
                b"# Quality Report Disabled\n\nNo quality analysis was requested.\n",
            )
        rollback = {
            "schema_version": 1,
            "current_generation": next_generation,
            "current_master": str(final_dir / "master.ged"),
            "current_master_sha256": master_sha,
            "previous": manifest["parent_release"],
            "instructions": (
                "To roll back, select the previous release's matching master.ged "
                "and manifest.json for the next update. Do not overwrite releases."
            ),
        }
        _write_bytes(staging / "rollback.json", _json_bytes(rollback))
        manifest["artifact_checksums"] = {
            name: _sha256_file(staging / name)
            for name in ("master.ged", "quality.md", "rollback.json", "update.md")
        }
        manifest_payload = _json_bytes(manifest)
        _write_bytes(staging / "manifest.json", manifest_payload)
        os.replace(staging, final_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    print(f"Incremental update complete: {final_dir}")
    print(f"Master GEDCOM: {final_dir / 'master.ged'}")
    print(f"Manifest: {final_dir / 'manifest.json'}")
    print(f"Update report: {final_dir / 'update.md'}")
    return 0


def _master_block_index(path: Path, core: ModuleType) -> dict[str, dict[str, str]]:
    """Index person blocks by pointer for explicit manual rebase comparison."""
    result: dict[str, dict[str, str]] = defaultdict(dict)
    for record in core.iter_gedcom_records(path):
        if record.tag != "INDI":
            continue
        for block in core._top_level_blocks(record.lines):
            result[record.pointer][_block_key(block, core)] = core.parse_gedcom_line(block[0]).tag
    return result


def _perform_rebase(args: argparse.Namespace, core: ModuleType) -> int:
    """Adopt external edits explicitly as protected manual provenance."""
    master = Path(args.master).expanduser().resolve()
    manifest_path = Path(args.manifest).expanduser().resolve()
    release_root = Path(args.release_root).expanduser().resolve()
    if not master.is_file() or not manifest_path.is_file():
        raise SyncError(
            "SYNC_CONFIGURATION",
            "The edited master or previous manifest does not exist.",
            "Rebase needs both sides to identify intentional manual changes.",
            ["Correct --master and --manifest, then retry."],
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncError(
            "MANIFEST_INVALID",
            "The previous manifest is not valid JSON.",
            "Manual edits cannot be protected without trustworthy prior provenance.",
            ["Restore manifest.json from the prior release."],
            details=(str(exc),),
        ) from exc
    previous_path = Path(str(manifest.get("master", {}).get("path", "")))
    if not previous_path.is_file():
        raise SyncError(
            "MANIFEST_INVALID",
            "The manifest's previous master file is no longer available.",
            "A rebase must compare manual edits with the exact prior generation.",
            ["Restore the prior release directory or use its intact backup."],
            details=(f"Expected previous master: {previous_path}",),
        )
    previous = _master_block_index(previous_path, core)
    current = _master_block_index(master, core)
    additions = {
        pointer: set(hashes) - set(previous.get(pointer, {}))
        for pointer, hashes in current.items()
        if set(hashes) - set(previous.get(pointer, {}))
    }
    deletions = {
        pointer: set(hashes) - set(current.get(pointer, {}))
        for pointer, hashes in previous.items()
        if set(hashes) - set(current.get(pointer, {}))
    }
    if deletions and not args.accept_manual_deletions:
        details = tuple(
            f"{pointer}: {len(hashes)} removed block(s)"
            for pointer, hashes in sorted(deletions.items())
        )
        raise SyncError(
            "SYNC_UNSAFE_REMOVAL",
            "The externally edited master removes existing person details.",
            "Without explicit confirmation, the updater cannot know whether the "
            "deletions were intentional.",
            [
                "Review the listed people in the old and edited masters.",
                "If every deletion is intentional, rerun with --accept-manual-deletions.",
            ],
            details=details,
        )
    next_generation = int(manifest.get("generation", 0)) + 1
    for pointer, hashes in additions.items():
        registry = manifest.setdefault("blocks", {}).setdefault(pointer, {})
        for block_hash in hashes:
            entry = registry.setdefault(
                block_hash,
                {
                    "tag": current[pointer][block_hash],
                    "kind": "person-block",
                    "observations": [],
                    "protected": [],
                },
            )
            if "manual" not in entry["protected"]:
                entry["protected"].append("manual")
    if deletions:
        manifest.setdefault("manual_tombstones", []).extend(
            {
                "generation": next_generation,
                "person": pointer,
                "block_hash": block_hash,
                "reason": args.reason,
            }
            for pointer, hashes in deletions.items()
            for block_hash in hashes
        )
    summary = (
        "# GEDCOM Manual Rebase Report\n\n"
        f"- Reason: {args.reason}\n"
        f"- Added or changed blocks protected as manual: "
        f"{sum(map(len, additions.values()))}\n"
        f"- Confirmed manual deletions: {sum(map(len, deletions.values()))}\n"
        "- No website snapshots were processed.\n"
    )
    if args.dry_run:
        print(summary, end="")
        return 0
    release_root.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_dir = release_root / f"g{next_generation:04d}-{timestamp}"
    staging = Path(tempfile.mkdtemp(prefix=".gedcom-rebase-", dir=release_root))
    try:
        shutil.copy2(master, staging / "master.ged")
        master_sha = _sha256_file(staging / "master.ged")
        prior = copy.deepcopy(manifest.get("master"))
        manifest["generation"] = next_generation
        manifest["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        manifest["parent_release"] = {
            "generation": next_generation - 1,
            "master": prior,
            "manifest_path": str(manifest_path),
            "manifest_sha256": _sha256_file(manifest_path),
        }
        manifest["master"] = {
            "path": str(final_dir / "master.ged"),
            "sha256": master_sha,
        }
        manifest["releases"].append(
            {
                "generation": next_generation,
                "path": str(final_dir),
                "master_sha256": master_sha,
                "created_at": manifest["updated_at"],
                "kind": "manual-rebase",
            }
        )
        _write_bytes(staging / "update.md", summary.encode("utf-8"))
        _write_bytes(
            staging / "quality.md",
            b"# Quality Report\n\nRun the next update or basic quality command.\n",
        )
        rollback = {
            "schema_version": 1,
            "current_generation": next_generation,
            "previous": manifest["parent_release"],
            "instructions": "Select the previous matching master and manifest to roll back.",
        }
        _write_bytes(staging / "rollback.json", _json_bytes(rollback))
        _write_bytes(staging / "manifest.json", _json_bytes(manifest))
        os.replace(staging, final_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    print(f"Manual rebase complete: {final_dir}")
    return 0


def main(argv: Sequence[str], core: ModuleType) -> int:
    """Dispatch ``update`` or ``rebase`` and render transparent failures."""
    command = argv[0] if argv else ""
    release_root = Path(".").resolve()
    try:
        if command == "update":
            args = _build_update_parser().parse_args(list(argv[1:]))
            release_root = Path(args.release_root).expanduser().resolve()
            return _perform_update(args, core)
        if command == "rebase":
            args = _build_rebase_parser().parse_args(list(argv[1:]))
            release_root = Path(args.release_root).expanduser().resolve()
            return _perform_rebase(args, core)
        raise SyncError(
            "SYNC_CONFIGURATION",
            f"Unknown incremental command: {command or '(missing)'}",
            "Only update and rebase have defined provenance behavior.",
            ["Use gedcom_merge.py update --help or rebase --help."],
        )
    except SyncError as exc:
        print(exc.render(), end="", file=__import__("sys").stderr)
        _failure_report(release_root, exc)
        return exc.exit_code
    except Exception as exc:
        error = SyncError(
            "SYNC_OUTPUT",
            "The incremental operation stopped before publishing a release.",
            "An unexpected failure was caught to preserve atomic output.",
            [
                "Retry with --verbose and review the failure report.",
                "Preserve the master, manifest, and snapshots for troubleshooting.",
            ],
            details=(f"{type(exc).__name__}: {exc}",),
        )
        print(error.render(), end="", file=__import__("sys").stderr)
        _failure_report(release_root, error)
        return error.exit_code
