"""Offline incremental synchronization for :mod:`ancestryllm.gedcom.engine`.

This module deliberately receives the merge module and any optional identity
resolver as runtime dependencies. Synchronization remains deterministic and
strictly offline when ``provider`` is ``none``.

Website snapshots are data origins, not evidence sources.  Snapshot ownership
therefore lives only in the private JSON manifest; standard GEDCOM ``SOUR``
records and fact-level citations remain the evidence model in the GEDCOM.
"""

from __future__ import annotations

import argparse
import copy
import ctypes
import dataclasses
import datetime as dt
import errno
import hashlib
import json
import math
import os
import re
import stat
import sys
import unicodedata
import uuid
from collections import defaultdict
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping, Never, Optional, Sequence

from ancestryllm.core.cancellation import (
    CancellationError,
    cancellation_checkpoint,
    non_interruptible_section,
)
from ancestryllm.core.errors import AncestryError, FileIngressError
from ancestryllm.core.ingress import (
    FileFingerprint,
    FileIngressPolicy,
    FileKind,
    FileSnapshot,
)
from ancestryllm.gedcom.contracts import IdentityResolver

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
    "SYNC_PUBLICATION_INCOMPLETE": 7,
}
ResolverFactory = Callable[[str, str, str | None], IdentityResolver]


class PlainEnglishArgumentParser(argparse.ArgumentParser):
    """Convert argparse failures into the updater's stable error contract."""

    def error(self, message: str) -> Never:
        """Raise a remediable configuration error instead of exiting abruptly."""
        del message
        raise SyncError(
            "SYNC_CONFIGURATION",
            "The command-line options are not valid.",
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
    fingerprint: FileFingerprint

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


@dataclass(frozen=True, slots=True)
class _DirectoryIdentity:
    """Stable identity for a directory created by this invocation."""

    device: int
    inode: int
    changed_ns: int
    birth_ns: int | None

    @classmethod
    def from_stat(cls, value: os.stat_result) -> _DirectoryIdentity:
        if value.st_ino <= 0:
            raise ValueError("filesystem identity is unavailable")
        birth_ns = getattr(value, "st_birthtime_ns", None)
        if birth_ns is None:
            birth = getattr(value, "st_birthtime", None)
            birth_ns = int(birth * 1_000_000_000) if birth is not None else None
        return cls(value.st_dev, value.st_ino, value.st_ctime_ns, birth_ns)

    def same_object(self, other: _DirectoryIdentity) -> bool:
        """Compare stable birth identity while allowing directory ctime changes."""

        return (
            self.device == other.device
            and self.inode == other.inode
            and (self.birth_ns is None or other.birth_ns is None or self.birth_ns == other.birth_ns)
        )


@dataclass(slots=True)
class _DirectoryCapability:
    """Held proof that an operation still addresses the directory it claimed."""

    selected_path: Path
    descriptor: int | None
    marker_name: str
    marker_descriptor: int
    owned: bool


@dataclass(slots=True)
class _PublicationTransactionState:
    """Mutable ownership state spanning publish, finalization, and caller cleanup."""

    marker_descriptor: int | None
    committed: bool = False


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
        if self.code == "SYNC_PUBLICATION_INCOMPLETE":
            lines.extend(
                [
                    "",
                    "Publication state is incomplete; an app-owned directory may remain.",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "No release was committed. An empty app-owned cleanup directory may remain.",
                ]
            )
        return "\n".join(lines) + "\n"

    def as_ancestry_error(self) -> AncestryError:
        """Return the transport-neutral coded form used by CLI and REPL services."""

        return AncestryError(
            self.code,
            self.what,
            " ".join(self.fixes),
            self.exit_code,
            {"error_class": self.code},
        )


def _sha256_file(
    path: Path,
) -> str:
    """Hash a file in bounded chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            cancellation_checkpoint()
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
        return str(core.normalise_gedcom_date(value)).upper()
    if tag == "CTRY":
        return str(core._normalise_country(value))
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
    parsed = []
    for line in lines:
        cancellation_checkpoint()
        parsed.append(core.parse_gedcom_line(line))
    root_level = parsed[0].level
    output: list[tuple[int, str, str]] = []
    for item in parsed:
        cancellation_checkpoint()
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
    normalized: list[str] = []
    for level, tag, value in output:
        cancellation_checkpoint()
        normalized.append(f"{level} {tag} {_normal_value(tag, value, core)}".rstrip())
    return normalized


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
        cancellation_checkpoint()
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
        cancellation_checkpoint()
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
        cancellation_checkpoint()
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
        cancellation_checkpoint()
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
        cancellation_checkpoint()
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
        cancellation_checkpoint()
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
        cancellation_checkpoint()
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
    rewritten: list[str] = []
    stable_pointer_map = dict(pointer_map)
    for line in lines:
        cancellation_checkpoint()
        rewritten.append(core._rewrite_xrefs(line, stable_pointer_map))
    return rewritten


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
            "A snapshot descriptor is incomplete.",
            "The updater cannot distinguish the source, vendor, and file path.",
            [
                "Use --snapshot SOURCE_ID:VENDOR=/absolute/or/relative/file.ged.",
                "Example: --snapshot ancestry-main:ancestry=tree.ged.",
            ],
        )
    if not SOURCE_ID_RE.fullmatch(source_id):
        raise SyncError(
            "SYNC_CONFIGURATION",
            "A snapshot source ID is not valid.",
            "Stable source IDs are persisted across snapshot generations.",
            [
                "Start the ID with a lowercase letter.",
                "Use only lowercase letters, digits, underscores, or hyphens.",
            ],
        )
    if vendor not in SUPPORTED_VENDORS:
        raise SyncError(
            "SYNC_CONFIGURATION",
            "A snapshot vendor is not recognized.",
            "Vendor metadata controls reporting and future compatibility profiles.",
            [f"Choose one of: {', '.join(SUPPORTED_VENDORS)}."],
        )
    return source_id, vendor, Path(raw_path)


def _header_export_date(
    path: Path,
    core: ModuleType,
    ingress: FileIngressPolicy,
    expected: FileSnapshot,
) -> Optional[str]:
    """Read a usable HEAD.DATE without treating it as genealogical evidence."""
    try:
        first = next(core.iter_gedcom_records(path, ingress, expected))
    except (StopIteration, OSError, ValueError):
        return None
    if first.tag != "HEAD":
        return None
    for block in core._top_level_blocks(first.lines):
        line = core.parse_gedcom_line(block[0])
        if line.tag == "DATE" and line.value.strip():
            return str(core.normalise_gedcom_date(line.value.strip()))
    return None


def _snapshot_specs(
    args: argparse.Namespace, core: ModuleType, ingress: FileIngressPolicy
) -> list[SnapshotSpec]:
    """Validate repeated snapshot arguments and derive export timestamps."""
    explicit_dates: dict[str, str] = {}
    for value in args.exported_at or []:
        source_id, separator, timestamp = value.partition("=")
        if not separator or not timestamp:
            raise SyncError(
                "SYNC_CONFIGURATION",
                "An export date descriptor is incomplete.",
                "An export date must be tied to one stable source ID.",
                ["Use --exported-at SOURCE_ID=YYYY-MM-DD or an ISO-8601 timestamp."],
            )
        try:
            dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SyncError(
                "SYNC_CONFIGURATION",
                "An export date is not valid ISO-8601 text.",
                "Incorrect dates make snapshot history misleading.",
                ["Use a value such as 2026-07-17 or 2026-07-17T14:30:00-04:00."],
            ) from exc
        explicit_dates[source_id] = timestamp
    specs: list[SnapshotSpec] = []
    seen: set[str] = set()
    for value in args.snapshot:
        source_id, vendor, path = _parse_snapshot_argument(value)
        path = ingress.normalize_path(path, FileKind.GEDCOM, absolute=True)
        if source_id in seen:
            raise SyncError(
                "SYNC_CONFIGURATION",
                f"Source ID {source_id!r} was supplied more than once.",
                "Only one snapshot can replace a source in a single generation.",
                ["Keep one --snapshot entry for each source ID."],
            )
        seen.add(source_id)
        snapshot = ingress.inspect(path, FileKind.GEDCOM)
        if source_id in explicit_dates:
            exported_at = explicit_dates[source_id]
            basis = "operator"
        else:
            header_date = _header_export_date(path, core, ingress, snapshot)
            if header_date:
                exported_at = header_date
                basis = "HEAD.DATE"
            else:
                exported_at = dt.datetime.fromtimestamp(
                    snapshot.modified_ns / 1_000_000_000, tz=dt.timezone.utc
                ).isoformat()
                basis = "file-mtime"
        fingerprint = ingress.fingerprint(
            path,
            FileKind.GEDCOM,
            expected=snapshot,
        )
        specs.append(
            SnapshotSpec(
                source_id=source_id,
                vendor=vendor,
                path=path,
                exported_at=exported_at,
                date_basis=basis,
                sha256=fingerprint.sha256,
                fingerprint=fingerprint,
            )
        )
    unknown_dates = sorted(set(explicit_dates) - seen)
    if unknown_dates:
        raise SyncError(
            "SYNC_CONFIGURATION",
            "An --exported-at value refers to a source ID with no snapshot.",
            "The date would have no snapshot observation to describe.",
            ["Add the matching --snapshot or remove the unused --exported-at value."],
            details=(f"Unused export date entries: {len(unknown_dates)}",),
        )
    return specs


def _new_manifest(
    master: Path,
    release_root: Path,
    master_fingerprint: FileFingerprint,
) -> dict[str, Any]:
    """Create an empty schema-v1 manifest for protected baseline seeding."""
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "tree_id": str(uuid.uuid4()),
        "generation": 0,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "master": {"path": str(master), "sha256": master_fingerprint.sha256},
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


def _manifest_invalid(field_name: str) -> SyncError:
    return SyncError(
        "MANIFEST_INVALID",
        f"The manifest field {field_name!r} has an unsupported structure.",
        "Malformed provenance cannot support a safe synchronization decision.",
        ["Restore an unmodified manifest from the matching release bundle."],
    )


def _validate_manifest(value: dict[str, Any]) -> None:
    """Validate every container shape used before synchronization side effects."""

    required = {
        "schema_version",
        "tree_id",
        "generation",
        "master",
        "active_snapshots",
        "snapshots",
        "person_bindings",
        "record_aliases",
        "blocks",
        "removed",
        "manual_tombstones",
        "next_ids",
        "releases",
    }
    if required - value.keys():
        raise _manifest_invalid("required fields")
    if (
        isinstance(value["schema_version"], bool)
        or value["schema_version"] != MANIFEST_SCHEMA_VERSION
    ):
        raise _manifest_invalid("schema_version")
    if not isinstance(value["tree_id"], str) or not value["tree_id"]:
        raise _manifest_invalid("tree_id")
    if (
        isinstance(value["generation"], bool)
        or not isinstance(value["generation"], int)
        or value["generation"] < 0
    ):
        raise _manifest_invalid("generation")
    master = value["master"]
    if (
        not isinstance(master, dict)
        or not isinstance(master.get("path"), str)
        or not master["path"]
        or not isinstance(master.get("sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", master["sha256"]) is None
    ):
        raise _manifest_invalid("master")
    mapping_fields = (
        "active_snapshots",
        "snapshots",
        "person_bindings",
        "record_aliases",
        "blocks",
        "next_ids",
    )
    if any(not isinstance(value[field_name], dict) for field_name in mapping_fields):
        raise _manifest_invalid("mapping")
    list_fields = ("removed", "manual_tombstones", "releases")
    if any(not isinstance(value[field_name], list) for field_name in list_fields):
        raise _manifest_invalid("list")
    if any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in value["active_snapshots"].items()
    ):
        raise _manifest_invalid("active_snapshots")
    if any(
        not isinstance(key, str) or not isinstance(item, dict)
        for key, item in value["snapshots"].items()
    ):
        raise _manifest_invalid("snapshots")
    snapshot_vendors: dict[str, str] = {}
    for snapshot_id, snapshot in value["snapshots"].items():
        required_snapshot_strings = (
            "snapshot_id",
            "source_id",
            "vendor",
            "path",
            "sha256",
            "exported_at",
            "date_basis",
            "observed_at",
        )
        if (
            any(
                not isinstance(snapshot.get(field_name), str) or not snapshot[field_name]
                for field_name in required_snapshot_strings
            )
            or snapshot["snapshot_id"] != snapshot_id
            or re.fullmatch(r"[0-9a-f]{64}", snapshot["sha256"]) is None
        ):
            raise _manifest_invalid("snapshots")
        source_id = snapshot["source_id"]
        vendor = snapshot["vendor"]
        if (
            SOURCE_ID_RE.fullmatch(source_id) is None
            or vendor not in SUPPORTED_VENDORS
            or snapshot_id != f"{source_id}:{snapshot['sha256'][:20]}"
            or snapshot["date_basis"] not in {"operator", "HEAD.DATE", "file-mtime"}
        ):
            raise _manifest_invalid("snapshots")
        try:
            observed_at = dt.datetime.fromisoformat(snapshot["observed_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise _manifest_invalid("snapshots") from exc
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise _manifest_invalid("snapshots")
        established_vendor = snapshot_vendors.setdefault(source_id, vendor)
        if established_vendor != vendor:
            raise _manifest_invalid("snapshots")
    for source_id, snapshot_id in value["active_snapshots"].items():
        snapshot = value["snapshots"].get(snapshot_id)
        if (
            SOURCE_ID_RE.fullmatch(source_id) is None
            or snapshot is None
            or snapshot["source_id"] != source_id
        ):
            raise _manifest_invalid("active_snapshots")
    if set(value["active_snapshots"]) != set(snapshot_vendors):
        raise _manifest_invalid("active_snapshots")
    if any(
        not isinstance(source_id, str)
        or SOURCE_ID_RE.fullmatch(source_id) is None
        or source_id not in snapshot_vendors
        or not isinstance(bindings, dict)
        or any(
            not isinstance(key, str) or not isinstance(item, str) for key, item in bindings.items()
        )
        for source_id, bindings in value["person_bindings"].items()
    ):
        raise _manifest_invalid("person_bindings")
    if any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in value["record_aliases"].items()
    ):
        raise _manifest_invalid("record_aliases")
    if any(
        not isinstance(pointer, str) or not isinstance(entries, dict)
        for pointer, entries in value["blocks"].items()
    ):
        raise _manifest_invalid("blocks")
    for pointer, entries in value["blocks"].items():
        if not pointer:
            raise _manifest_invalid("blocks")
        for block_hash, entry in entries.items():
            if (
                not isinstance(block_hash, str)
                or not isinstance(entry, dict)
                or not isinstance(entry.get("tag"), str)
                or not entry["tag"]
                or not isinstance(entry.get("observations"), list)
                or any(not isinstance(item, str) for item in entry["observations"])
                or any(item not in value["snapshots"] for item in entry["observations"])
                or not isinstance(entry.get("protected"), list)
                or any(not isinstance(item, str) for item in entry["protected"])
            ):
                raise _manifest_invalid("blocks")
            for generation_field in ("first_seen_generation", "last_seen_generation"):
                generation = entry.get(generation_field)
                if generation is not None and (
                    isinstance(generation, bool)
                    or not isinstance(generation, int)
                    or generation < 0
                ):
                    raise _manifest_invalid("blocks")
    if any(not isinstance(item, dict) for field_name in list_fields for item in value[field_name]):
        raise _manifest_invalid("list entry")
    for tombstone in value["manual_tombstones"]:
        if (
            not isinstance(tombstone.get("person"), str)
            or not isinstance(tombstone.get("block_hash"), str)
            or not isinstance(tombstone.get("reason"), str)
            or isinstance(tombstone.get("generation"), bool)
            or not isinstance(tombstone.get("generation"), int)
            or tombstone["generation"] < 0
        ):
            raise _manifest_invalid("manual_tombstones")
    for release in value["releases"]:
        if (
            isinstance(release.get("generation"), bool)
            or not isinstance(release.get("generation"), int)
            or release["generation"] < 0
            or not isinstance(release.get("path"), str)
            or not release["path"]
            or not isinstance(release.get("master_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", release["master_sha256"]) is None
        ):
            raise _manifest_invalid("releases")
    if any(
        not isinstance(key, str) or isinstance(item, bool) or not isinstance(item, int) or item < 1
        for key, item in value["next_ids"].items()
    ):
        raise _manifest_invalid("next_ids")


def _load_manifest(
    path: Path,
    ingress: FileIngressPolicy,
    manifest_fingerprint: FileFingerprint,
    master_fingerprint: FileFingerprint,
) -> dict[str, Any]:
    """Load and validate a manifest before using any provenance decisions."""
    value = ingress.read_json(
        path,
        FileKind.MANIFEST,
        require_object=True,
        expected=manifest_fingerprint.snapshot,
    )
    assert isinstance(value, dict)
    _validate_manifest(value)
    expected = value["master"]["sha256"]
    actual = master_fingerprint.sha256
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
    return value


def _validate_snapshot_continuity(
    manifest: Mapping[str, Any],
    specs: Sequence[SnapshotSpec],
) -> None:
    """Keep each stable source bound to one vendor and content identity."""
    snapshots = manifest["snapshots"]
    vendors = {snapshot["source_id"]: snapshot["vendor"] for snapshot in snapshots.values()}
    for spec in specs:
        established_vendor = vendors.get(spec.source_id)
        existing_snapshot = snapshots.get(spec.snapshot_id)
        if established_vendor is not None and established_vendor != spec.vendor:
            raise SyncError(
                "SYNC_CONFIGURATION",
                "A snapshot vendor does not match its existing source history.",
                "Changing vendor identity under one source ID corrupts provenance.",
                [
                    "Use the vendor already recorded for this stable source ID.",
                    "Use a new source ID when importing a different website source.",
                ],
            )
        if existing_snapshot is not None and existing_snapshot["sha256"] != spec.sha256:
            raise SyncError(
                "SYNC_CONFIGURATION",
                "A snapshot conflicts with an existing content address.",
                "The updater cannot safely treat two different files as one observation.",
                ["Use a new stable source ID and preserve both snapshots for review."],
            )


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
        "--provider",
        default="none",
        help="Explicit provider profile or built-in provider; none keeps update network-free.",
    )
    parser.add_argument("--model", default="")
    parser.add_argument("--consent")
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


def _verdict_confidence(value: object) -> float:
    """Return a scalar confidence while treating malformed resolver data as zero."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return 0.0
    try:
        confidence = float(value)
    except (OverflowError, ValueError):
        return 0.0
    return confidence if math.isfinite(confidence) and 0.0 <= confidence <= 1.0 else 0.0


def _match_people(
    sources: Sequence[Any],
    specs: Sequence[SnapshotSpec],
    manifest: dict[str, Any],
    core: ModuleType,
    stats: SyncStats,
    *,
    provider_id: str = "none",
    identity_resolver: IdentityResolver | None = None,
) -> tuple[dict[str, str], dict[str, dict[str, str]], list[Any]]:
    """Map snapshot people to stable master pointers conservatively."""
    cancellation_checkpoint()
    people_by_source: list[list[Any]] = []
    for source in sources:
        cancellation_checkpoint()
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
        cancellation_checkpoint()
        for identifier in _identifier_values(person, core):
            identifier_index[identifier].add(person.pointer)
        fingerprint_index[_identity_fingerprint(person)].add(person.pointer)
    new_bindings: dict[str, dict[str, str]] = {}
    counters = manifest["next_ids"]
    for index, spec in enumerate(specs, 1):
        cancellation_checkpoint()
        source = sources[index]
        previous_bindings = manifest.get("person_bindings", {}).get(spec.source_id, {})
        bindings: dict[str, str] = {}
        inverse_pointer_map = {
            global_pointer: original for original, global_pointer in source.pointer_map.items()
        }
        for incoming in people_by_source[index]:
            cancellation_checkpoint()
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
                cancellation_checkpoint()
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
                elif identity_resolver is not None and scored:
                    best_score, best_pointer, assessment = max(
                        scored, key=lambda item: (item[0], item[1])
                    )
                    target = ""
                    if not assessment.conflicts:
                        verdict = dict(
                            identity_resolver(
                                by_pointer[best_pointer],
                                incoming,
                            )
                        )
                        if (
                            bool(verdict.get("is_duplicate"))
                            and _verdict_confidence(verdict.get("confidence")) >= 0.90
                        ):
                            target = best_pointer
                            stats.mapped_people.append(
                                f"{spec.source_id}:{original} -> {target} "
                                f"(LLM {verdict.get('_provider', provider_id)}/"
                                f"{verdict.get('_model', 'provider default')}, "
                                f"score {best_score:.2f})"
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
    cancellation_checkpoint()
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
            cancellation_checkpoint()
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
        cancellation_checkpoint()
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
    cancellation_checkpoint()
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
        cancellation_checkpoint()
        for record in source.records:
            cancellation_checkpoint()
            if record.tag != "INDI":
                continue
            target = pointer_map.get(record.pointer, record.pointer)
            grouped[target].append((source_spec_by_path.get(record.source_file), record))
    people: list[Any] = []
    for target, origin_records in grouped.items():
        cancellation_checkpoint()
        accumulator: dict[str, list[str]] = {}
        order: list[str] = []
        current_master_keys: set[str] = set()
        person_registry = block_registry.setdefault(target, {})
        for origin_spec, record in origin_records:
            cancellation_checkpoint()
            rewritten = _replace_header_pointer(
                _rewrite_lines(record.lines, pointer_map, core), target, core
            )
            for block in core._top_level_blocks(rewritten):
                cancellation_checkpoint()
                key = _block_key(block, core)
                tag = core.parse_gedcom_line(block[0]).tag
                if origin_spec is not None and (target, key) in tombstones:
                    stats.conflicts.append(
                        f"{target}:{tag}:{key[:12]} was present in "
                        f"{origin_spec.source_id} but retained as an intentional "
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
                if origin_spec is None:
                    current_master_keys.add(key)
                    if initialize and "baseline" not in entry["protected"]:
                        entry["protected"].append("baseline")
                elif origin_spec.snapshot_id not in entry["observations"]:
                    entry["observations"].append(origin_spec.snapshot_id)
                    stats.added_facts.append(f"{target}:{tag}:{origin_spec.source_id}:{key[:12]}")
                entry["last_seen_generation"] = manifest.get("generation", 0) + 1
        retained_order: list[str] = []
        for key in order:
            cancellation_checkpoint()
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
        cancellation_checkpoint()
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
    provider_id: str,
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
        f"- LLM provider: `{provider_id}`"
        + (" (offline deterministic)" if provider_id == "none" else " (explicit opt-in)"),
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
    cancellation_checkpoint()
    with path.open("xb") as handle:
        handle.write(payload)
    cancellation_checkpoint()


def _exclusive_rename_directory(
    source: Path,
    destination: Path,
    *,
    source_dir_fd: int | None = None,
    destination_dir_fd: int | None = None,
) -> None:
    """Atomically rename one directory without replacing any destination."""

    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if os.name == "nt":
        # MoveFileEx without MOVEFILE_REPLACE_EXISTING is what os.rename uses
        # on Windows.
        os.rename(source, destination)
        return
    library = ctypes.CDLL(None, use_errno=True)
    current_platform = str(sys.platform)
    if current_platform == "darwin":
        try:
            rename_exclusive = (
                library.renameatx_np
                if source_dir_fd is not None and destination_dir_fd is not None
                else library.renamex_np
            )
        except AttributeError as exc:
            raise OSError(errno.ENOTSUP, "exclusive directory rename is unavailable") from exc
        if source_dir_fd is not None and destination_dir_fd is not None:
            rename_exclusive.argtypes = (
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            arguments: tuple[Any, ...] = (
                source_dir_fd,
                source_bytes,
                destination_dir_fd,
                destination_bytes,
                0x00000004,
            )
        else:
            rename_exclusive.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
            arguments = (source_bytes, destination_bytes, 0x00000004)
        rename_exclusive.restype = ctypes.c_int
        ctypes.set_errno(0)
        result = rename_exclusive(*arguments)
    elif current_platform.startswith("linux"):
        try:
            rename_no_replace = library.renameat2
        except AttributeError as exc:
            raise OSError(errno.ENOTSUP, "exclusive directory rename is unavailable") from exc
        rename_no_replace.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename_no_replace.restype = ctypes.c_int
        ctypes.set_errno(0)
        result = rename_no_replace(
            source_dir_fd if source_dir_fd is not None else -100,
            source_bytes,
            destination_dir_fd if destination_dir_fd is not None else -100,
            destination_bytes,
            0x00000001,
        )
    else:
        raise OSError(errno.ENOTSUP, "exclusive directory rename is unavailable")
    if result != 0:
        error_number = ctypes.get_errno() or errno.EIO
        raise OSError(error_number, os.strerror(error_number))


def _directory_identity(path: Path) -> _DirectoryIdentity:
    """Return identity for an existing real directory or fail closed."""

    try:
        value = os.lstat(path)
    except OSError as exc:
        raise SyncError(
            "SYNC_OUTPUT",
            "The release directory could not be inspected safely.",
            "Release ownership must be proven before publication or cleanup.",
            ["Choose an accessible local --release-root and retry."],
            details=(f"Error class: {type(exc).__name__}",),
        ) from exc
    if not stat.S_ISDIR(value.st_mode):
        raise SyncError(
            "SYNC_OUTPUT",
            "The release destination is not a real directory.",
            "Symlinks and non-directory destinations cannot preserve immutable releases safely.",
            ["Choose a new local --release-root that is an ordinary directory."],
        )
    try:
        return _DirectoryIdentity.from_stat(value)
    except ValueError as exc:
        raise SyncError(
            "SYNC_OUTPUT",
            "The release directory has no trustworthy filesystem identity.",
            "Publication and recursive cleanup require a positive, stable file identity.",
            ["Choose a supported local filesystem and retry."],
        ) from exc


def _raw_directory_identity(path: Path) -> _DirectoryIdentity:
    """Return an ordinary directory identity without translating failures."""

    value = os.lstat(path)
    if not stat.S_ISDIR(value.st_mode):
        raise ValueError("path is not an ordinary directory")
    return _DirectoryIdentity.from_stat(value)


def _held_file_path(descriptor: int) -> Path:
    """Return the current physical pathname of one held file descriptor."""

    current_platform = str(sys.platform)
    if current_platform == "darwin":
        import fcntl

        try:
            raw = fcntl.fcntl(descriptor, 50, b"\0" * 1024)
        except (OSError, ValueError) as exc:
            raise SyncError(
                "SYNC_OUTPUT",
                "A held release capability no longer has a usable path.",
                "The release root moved while the operation was active.",
                ["Stop concurrent filesystem changes and retry."],
            ) from exc
        return Path(os.fsdecode(raw.split(b"\0", 1)[0]))
    if current_platform.startswith("linux"):
        try:
            return Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        except OSError as exc:
            raise SyncError(
                "SYNC_OUTPUT",
                "A held release capability no longer has a usable path.",
                "The release root moved while the operation was active.",
                ["Stop concurrent filesystem changes and retry."],
            ) from exc
    if os.name == "nt":
        import msvcrt

        handle = msvcrt.get_osfhandle(descriptor)  # type: ignore[attr-defined]
        library = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        get_path = library.GetFinalPathNameByHandleW
        get_path.argtypes = (
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint,
            ctypes.c_uint,
        )
        get_path.restype = ctypes.c_uint
        size = get_path(handle, None, 0, 0)
        if size == 0:
            raise SyncError(
                "SYNC_OUTPUT",
                "A held release capability no longer has a usable path.",
                "The release root moved while the operation was active.",
                ["Stop concurrent filesystem changes and retry."],
            )
        buffer = ctypes.create_unicode_buffer(size + 1)
        if get_path(handle, buffer, len(buffer), 0) == 0:
            raise SyncError(
                "SYNC_OUTPUT",
                "A held release capability no longer has a usable path.",
                "The release root moved while the operation was active.",
                ["Stop concurrent filesystem changes and retry."],
            )
        value = buffer.value
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
        return Path(value)
    raise SyncError(
        "SYNC_OUTPUT",
        "The platform cannot hold a release-directory capability.",
        "Safe publication requires a supported directory-handle implementation.",
        ["Use Ubuntu, macOS, or Windows and retry."],
    )


def _capability_current_path(capability: _DirectoryCapability) -> Path:
    """Return the directory containing the held, unguessable marker."""

    return _held_file_path(capability.marker_descriptor).parent


def _uses_windows_capability_handles() -> bool:
    return os.name == "nt"


def _windows_create_file_handle(
    path: Path,
    *,
    access: int,
    share: int,
    creation: int,
    flags: int,
) -> int:
    library = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    create_file = library.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        os.fspath(path),
        access,
        share,
        None,
        creation,
        flags,
        None,
    )
    value = ctypes.cast(handle, ctypes.c_void_p).value
    if value in {None, ctypes.c_void_p(-1).value}:
        error_number = ctypes.get_last_error()  # type: ignore[attr-defined]
        raise OSError(error_number, "Windows could not open a held release marker.")
    assert value is not None
    return int(value)


def _windows_close_handle(handle: int) -> None:
    library = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    close_handle = library.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    close_handle(handle)


def _windows_descriptor_from_handle(handle: int, flags: int) -> int:
    import msvcrt

    return int(msvcrt.open_osfhandle(handle, flags))  # type: ignore[attr-defined]


def _windows_mark_handle_for_deletion(handle: int) -> None:
    library = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    set_information = library.SetFileInformationByHandle
    set_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    set_information.restype = wintypes.BOOL
    delete_file = ctypes.c_ubyte(1)
    if not set_information(
        handle,
        4,
        ctypes.byref(delete_file),
        ctypes.sizeof(delete_file),
    ):
        error_number = ctypes.get_last_error()  # type: ignore[attr-defined]
        raise OSError(error_number, "Windows could not delete an owned release path.")


def _windows_mark_descriptor_for_deletion(descriptor: int) -> None:
    import msvcrt

    _windows_mark_handle_for_deletion(
        msvcrt.get_osfhandle(descriptor)  # type: ignore[attr-defined]
    )


def _open_windows_shared_marker(path: Path, *, create: bool) -> int:
    generic_read = 0x80000000
    generic_write = 0x40000000
    delete_access = 0x00010000
    share_read_write_delete = 0x00000001 | 0x00000002 | 0x00000004
    create_new = 1
    open_existing = 3
    file_attribute_normal = 0x00000080
    open_reparse_point = 0x00200000
    handle = _windows_create_file_handle(
        path,
        access=generic_read | generic_write | delete_access,
        share=share_read_write_delete,
        creation=create_new if create else open_existing,
        flags=file_attribute_normal | open_reparse_point,
    )
    descriptor_flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    try:
        return _windows_descriptor_from_handle(handle, descriptor_flags)
    except BaseException:
        if create:
            try:
                _windows_mark_handle_for_deletion(handle)
            except OSError:
                pass
        _windows_close_handle(handle)
        raise


def _open_held_marker(
    path: Path,
    *,
    create: bool,
    directory_descriptor: int | None = None,
) -> int:
    if _uses_windows_capability_handles():
        if directory_descriptor is not None:
            raise OSError("Windows marker paths must be absolute.")
        return _open_windows_shared_marker(path, create=create)
    flags = os.O_RDWR
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(path, flags, 0o600, dir_fd=directory_descriptor)


def _open_windows_delete_descriptor(path: Path, *, directory: bool) -> int:
    delete_access = 0x00010000
    file_read_attributes = 0x00000080
    share_read_write_delete = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    open_reparse_point = 0x00200000
    backup_semantics = 0x02000000
    handle = _windows_create_file_handle(
        path,
        access=delete_access | file_read_attributes,
        share=share_read_write_delete,
        creation=open_existing,
        flags=open_reparse_point | (backup_semantics if directory else 0),
    )
    descriptor_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    try:
        return _windows_descriptor_from_handle(handle, descriptor_flags)
    except BaseException:
        _windows_close_handle(handle)
        raise


def _marker_identity_at(
    marker_name: str,
    *,
    directory_descriptor: int | None,
    marker_path: Path,
) -> _DirectoryIdentity:
    if directory_descriptor is not None:
        value = os.stat(
            marker_name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    else:
        value = os.lstat(marker_path)
    return _DirectoryIdentity.from_stat(value)


def _delete_held_marker(
    marker_descriptor: int,
    marker_name: str,
    *,
    directory_descriptor: int | None,
    marker_path: Path,
    expected: _DirectoryIdentity | None = None,
    consume_on_failure: bool = True,
) -> None:
    """Delete a proven marker, retaining its descriptor on requested failure."""

    deleted = False
    try:
        held = _DirectoryIdentity.from_stat(os.fstat(marker_descriptor))
        current = _marker_identity_at(
            marker_name,
            directory_descriptor=directory_descriptor,
            marker_path=marker_path,
        )
        if not held.same_object(current) or (
            expected is not None and not expected.same_object(held)
        ):
            raise SyncError(
                "SYNC_OUTPUT",
                "The release ownership marker changed unexpectedly.",
                "Cleanup cannot remove a marker it no longer owns.",
                ["Stop concurrent filesystem changes and inspect the release root."],
            )
        if _uses_windows_capability_handles():
            _windows_mark_descriptor_for_deletion(marker_descriptor)
        elif directory_descriptor is not None:
            os.unlink(marker_name, dir_fd=directory_descriptor)
        else:
            os.unlink(marker_path)
        deleted = True
    finally:
        if deleted or consume_on_failure:
            _close_descriptor_quietly(marker_descriptor)


def _open_directory_capability(path: Path, *, owned: bool) -> _DirectoryCapability:
    """Bind a directory to held directory and marker descriptors."""

    initial: _DirectoryIdentity | None = None
    descriptor: int | None = None
    marker_descriptor = -1
    marker_identity: _DirectoryIdentity | None = None
    marker_name = f".ancestryllm-capability-{uuid.uuid4().hex}"
    try:
        initial = _directory_identity(path)
        if not _uses_windows_capability_handles():
            flags = os.O_RDONLY
            if hasattr(os, "O_DIRECTORY"):
                flags |= os.O_DIRECTORY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            descriptor = os.open(path, flags)
            opened = _DirectoryIdentity.from_stat(os.fstat(descriptor))
            if not initial.same_object(opened):
                raise SyncError(
                    "SYNC_OUTPUT",
                    "The release directory changed while it was being claimed.",
                    "Publication cannot continue without a stable directory capability.",
                    ["Stop concurrent filesystem changes and retry."],
                )
        if descriptor is not None:
            marker_descriptor = _open_held_marker(
                Path(marker_name),
                create=True,
                directory_descriptor=descriptor,
            )
        else:
            marker_descriptor = _open_held_marker(path / marker_name, create=True)
        os.write(marker_descriptor, os.urandom(32))
        os.fsync(marker_descriptor)
        marker_identity = _DirectoryIdentity.from_stat(os.fstat(marker_descriptor))
        return _DirectoryCapability(
            selected_path=path,
            descriptor=descriptor,
            marker_name=marker_name,
            marker_descriptor=marker_descriptor,
            owned=owned,
        )
    except BaseException:
        if owned:
            parent_descriptor: int | None = None
            try:
                if initial is None:
                    initial = _raw_directory_identity(path)
                if not _uses_windows_capability_handles():
                    parent_descriptor = _open_plain_directory_descriptor(path.parent)
                owned_descriptor = descriptor
                owned_marker_descriptor = marker_descriptor if marker_descriptor >= 0 else None
                descriptor = None
                marker_descriptor = -1
                _cleanup_owned_flat_directory(
                    path,
                    path.name,
                    parent_descriptor=parent_descriptor,
                    descriptor=owned_descriptor,
                    expected=initial,
                    marker_name=(marker_name if owned_marker_descriptor is not None else None),
                    marker_descriptor=owned_marker_descriptor,
                    marker_identity=marker_identity,
                    allowed_names=frozenset(),
                )
            except BaseException as cleanup_error:
                del cleanup_error
            finally:
                _close_descriptor_quietly(parent_descriptor)
        elif marker_descriptor >= 0:
            try:
                held_marker_descriptor = marker_descriptor
                marker_descriptor = -1
                _delete_held_marker(
                    held_marker_descriptor,
                    marker_name,
                    directory_descriptor=descriptor,
                    marker_path=path / marker_name,
                    expected=marker_identity,
                )
            except (OSError, SyncError, ValueError):
                pass
        _close_descriptor_quietly(marker_descriptor if marker_descriptor >= 0 else None)
        _close_descriptor_quietly(descriptor)
        raise


def _capability_matches_selected(capability: _DirectoryCapability) -> bool:
    """Return whether the selected path still contains the held marker and root."""

    try:
        selected = os.lstat(capability.selected_path)
        marker = os.lstat(capability.selected_path / capability.marker_name)
        marker_held = os.fstat(capability.marker_descriptor)
        selected_identity = _DirectoryIdentity.from_stat(selected)
        marker_identity = _DirectoryIdentity.from_stat(marker)
        marker_held_identity = _DirectoryIdentity.from_stat(marker_held)
        if not stat.S_ISDIR(selected.st_mode) or marker_identity != marker_held_identity:
            return False
        if capability.descriptor is not None:
            held = _DirectoryIdentity.from_stat(os.fstat(capability.descriptor))
            return selected_identity == held
        return os.path.normcase(os.path.abspath(capability.selected_path)) == os.path.normcase(
            os.path.abspath(_capability_current_path(capability))
        )
    except (OSError, SyncError, ValueError):
        return False


def _require_selected_capability(capability: _DirectoryCapability) -> None:
    """Reject release-root replacement before another filesystem side effect."""

    if not _capability_matches_selected(capability):
        raise SyncError(
            "SYNC_OUTPUT",
            "The release root changed while the operation was active.",
            "Continuing could publish into a directory owned by another process.",
            ["Restore the selected --release-root and retry without concurrent changes."],
        )


def _remove_capability_marker(capability: _DirectoryCapability) -> None:
    """Remove only the marker proven by the held descriptor."""

    if capability.marker_descriptor < 0:
        return
    marker_path = _held_file_path(capability.marker_descriptor)
    marker_descriptor = capability.marker_descriptor
    _delete_held_marker(
        marker_descriptor,
        capability.marker_name,
        directory_descriptor=capability.descriptor,
        marker_path=marker_path,
        consume_on_failure=False,
    )
    capability.marker_descriptor = -1


def _close_capability(capability: _DirectoryCapability) -> None:
    """Remove the owned marker and close held descriptors."""

    _remove_capability_marker(capability)
    if capability.descriptor is not None:
        descriptor = capability.descriptor
        capability.descriptor = None
        _close_descriptor_quietly(descriptor)


def _close_capability_quietly(capability: _DirectoryCapability) -> None:
    for _attempt in range(2):
        try:
            _close_capability(capability)
            return
        except BaseException as exc:
            del exc
    if capability.marker_descriptor >= 0:
        marker_descriptor = capability.marker_descriptor
        capability.marker_descriptor = -1
        _close_descriptor_quietly(marker_descriptor)
    if capability.descriptor is not None:
        descriptor = capability.descriptor
        capability.descriptor = None
        _close_descriptor_quietly(descriptor)


_STAGING_CLEANUP_NAMES = frozenset(
    {
        "manifest.json",
        "master.ged",
        "quality.md",
        "rollback.json",
        "update.md",
    }
)


def _open_plain_directory_descriptor(path: Path) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return os.open(path, flags)


def _open_plain_directory_entry_descriptor(name: str, parent_descriptor: int) -> int:
    """Open one directory entry relative to a held parent descriptor."""

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return os.open(name, flags, dir_fd=parent_descriptor)


def _close_descriptor_quietly(descriptor: int | None) -> None:
    if descriptor is None or descriptor < 0:
        return
    for _attempt in range(2):
        try:
            os.close(descriptor)
            return
        except OSError:
            return
        except BaseException as exc:
            del exc


def _is_flat_cleanup_entry(value: os.stat_result) -> bool:
    return stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode)


def _cleanup_owned_flat_directory(
    path: Path,
    name: str,
    *,
    parent_descriptor: int | None,
    descriptor: int | None,
    expected: _DirectoryIdentity,
    marker_name: str | None,
    marker_descriptor: int | None,
    marker_identity: _DirectoryIdentity | None,
    allowed_names: frozenset[str],
) -> bool:
    """Delete a known flat directory through held capabilities, never recursively.

    All supplied directory and marker descriptors are consumed, including when
    validation fails.
    """

    opened_marker = marker_descriptor
    windows_directory_descriptor: int | None = None
    windows_entry_descriptors: list[int] = []
    try:
        if _uses_windows_capability_handles():
            current_path = (
                _held_file_path(opened_marker).parent if opened_marker is not None else path
            )
            windows_directory_descriptor = _open_windows_delete_descriptor(
                current_path,
                directory=True,
            )
            held_directory = _DirectoryIdentity.from_stat(os.fstat(windows_directory_descriptor))
            current_directory_stat = os.lstat(current_path)
            current_directory = _DirectoryIdentity.from_stat(current_directory_stat)
            if (
                not stat.S_ISDIR(current_directory_stat.st_mode)
                or not expected.same_object(held_directory)
                or not held_directory.same_object(current_directory)
            ):
                return False
            if marker_name is not None and opened_marker is None:
                opened_marker = _open_held_marker(
                    current_path / marker_name,
                    create=False,
                )
            if marker_name is not None:
                if opened_marker is None:
                    return False
                held_marker_stat = os.fstat(opened_marker)
                held_marker = _DirectoryIdentity.from_stat(held_marker_stat)
                expected_marker = marker_identity or held_marker
                current_marker = _marker_identity_at(
                    marker_name,
                    directory_descriptor=None,
                    marker_path=current_path / marker_name,
                )
                if (
                    not stat.S_ISREG(held_marker_stat.st_mode)
                    or not expected_marker.same_object(held_marker)
                    or not held_marker.same_object(current_marker)
                ):
                    return False
            entries = set(os.listdir(current_path))
            expected_names = set(allowed_names)
            if marker_name is not None:
                expected_names.add(marker_name)
                if marker_name not in entries:
                    return False
            if not entries.issubset(expected_names):
                return False
            for entry_name in sorted(entries):
                if entry_name == marker_name:
                    continue
                entry_path = current_path / entry_name
                entry_stat = os.lstat(entry_path)
                if not _is_flat_cleanup_entry(entry_stat):
                    return False
                entry_descriptor = _open_windows_delete_descriptor(
                    entry_path,
                    directory=False,
                )
                windows_entry_descriptors.append(entry_descriptor)
                opened_identity = _DirectoryIdentity.from_stat(os.fstat(entry_descriptor))
                current_identity = _DirectoryIdentity.from_stat(os.lstat(entry_path))
                if not opened_identity.same_object(
                    _DirectoryIdentity.from_stat(entry_stat)
                ) or not opened_identity.same_object(current_identity):
                    return False
            current_directory = _DirectoryIdentity.from_stat(os.lstat(current_path))
            held_directory = _DirectoryIdentity.from_stat(os.fstat(windows_directory_descriptor))
            if not expected.same_object(held_directory) or not held_directory.same_object(
                current_directory
            ):
                return False
            for entry_descriptor in windows_entry_descriptors:
                _windows_mark_descriptor_for_deletion(entry_descriptor)
                os.close(entry_descriptor)
            windows_entry_descriptors.clear()
            if marker_name is not None:
                assert opened_marker is not None
                marker_to_delete = opened_marker
                opened_marker = None
                _delete_held_marker(
                    marker_to_delete,
                    marker_name,
                    directory_descriptor=None,
                    marker_path=current_path / marker_name,
                    expected=marker_identity,
                )
            _windows_mark_descriptor_for_deletion(windows_directory_descriptor)
            os.close(windows_directory_descriptor)
            windows_directory_descriptor = None
            return True

        if descriptor is None:
            descriptor = _open_plain_directory_descriptor(path)
        if parent_descriptor is None:
            return False
        held_directory = _DirectoryIdentity.from_stat(os.fstat(descriptor))
        current_directory_stat = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        current_directory = _DirectoryIdentity.from_stat(current_directory_stat)
        if (
            not stat.S_ISDIR(current_directory_stat.st_mode)
            or not expected.same_object(held_directory)
            or not held_directory.same_object(current_directory)
        ):
            return False
        if marker_name is not None and opened_marker is None:
            opened_marker = _open_held_marker(
                Path(marker_name),
                create=False,
                directory_descriptor=descriptor,
            )
        if marker_name is not None:
            if opened_marker is None:
                return False
            held_marker_stat = os.fstat(opened_marker)
            held_marker = _DirectoryIdentity.from_stat(held_marker_stat)
            expected_marker = marker_identity or held_marker
            current_marker = _marker_identity_at(
                marker_name,
                directory_descriptor=descriptor,
                marker_path=path / marker_name,
            )
            if (
                not stat.S_ISREG(held_marker_stat.st_mode)
                or not expected_marker.same_object(held_marker)
                or not held_marker.same_object(current_marker)
            ):
                return False
        entries = set(os.listdir(descriptor))
        expected_names = set(allowed_names)
        if marker_name is not None:
            expected_names.add(marker_name)
            if marker_name not in entries:
                return False
        if not entries.issubset(expected_names):
            return False
        for entry_name in sorted(entries):
            entry_stat = os.stat(
                entry_name,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
            if entry_name == marker_name:
                if not stat.S_ISREG(entry_stat.st_mode):
                    return False
            elif not _is_flat_cleanup_entry(entry_stat):
                return False
        current_directory = _DirectoryIdentity.from_stat(
            os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        )
        held_directory = _DirectoryIdentity.from_stat(os.fstat(descriptor))
        if not expected.same_object(held_directory) or not held_directory.same_object(
            current_directory
        ):
            return False
        for entry_name in sorted(entries):
            if entry_name != marker_name:
                os.unlink(entry_name, dir_fd=descriptor)
        if marker_name is not None:
            assert opened_marker is not None
            marker_to_delete = opened_marker
            opened_marker = None
            _delete_held_marker(
                marker_to_delete,
                marker_name,
                directory_descriptor=descriptor,
                marker_path=path / marker_name,
                expected=marker_identity,
            )
        os.fsync(descriptor)
        # POSIX has no portable primitive that removes a directory by its held
        # descriptor. A final ``rmdir(name, dir_fd=parent_descriptor)`` would
        # reopen a namespace race and could delete an empty foreign replacement
        # after the last identity check. Fail closed: leave only the now-empty
        # app-owned directory for explicit inspection/removal.
        return False
    finally:
        for entry_descriptor in windows_entry_descriptors:
            _close_descriptor_quietly(entry_descriptor)
        _close_descriptor_quietly(windows_directory_descriptor)
        _close_descriptor_quietly(opened_marker)
        _close_descriptor_quietly(descriptor)


def _cleanup_capability_tree(capability: _DirectoryCapability) -> None:
    """Delete only an owned, marker-only capability directory."""

    parent_descriptor: int | None = None
    current_path: Path | None = None
    marker_identity: _DirectoryIdentity | None = None
    cleaned = False
    try:
        current_path = _capability_current_path(capability)
        expected = (
            _DirectoryIdentity.from_stat(os.fstat(capability.descriptor))
            if capability.descriptor is not None
            else _directory_identity(current_path)
        )
        marker_identity = _DirectoryIdentity.from_stat(os.fstat(capability.marker_descriptor))
        if not _uses_windows_capability_handles():
            parent_descriptor = _open_plain_directory_descriptor(current_path.parent)
        descriptor = capability.descriptor
        marker_descriptor = capability.marker_descriptor
        capability.descriptor = None
        capability.marker_descriptor = -1
        cleaned = _cleanup_owned_flat_directory(
            current_path,
            current_path.name,
            parent_descriptor=parent_descriptor,
            descriptor=descriptor,
            expected=expected,
            marker_name=capability.marker_name,
            marker_descriptor=marker_descriptor,
            marker_identity=marker_identity,
            allowed_names=frozenset(),
        )
        if not cleaned and current_path is not None and marker_identity is not None:
            reopened_marker = _open_held_marker(
                current_path / capability.marker_name,
                create=False,
            )
            _delete_held_marker(
                reopened_marker,
                capability.marker_name,
                directory_descriptor=None,
                marker_path=current_path / capability.marker_name,
                expected=marker_identity,
            )
    except BaseException:
        _close_capability_quietly(capability)
    finally:
        _close_descriptor_quietly(parent_descriptor)


def _cleanup_empty_release_root(capability: _DirectoryCapability) -> None:
    """Remove an owned root only when its held directory remains otherwise empty."""

    if not capability.owned:
        _close_capability_quietly(capability)
        return
    _cleanup_capability_tree(capability)


def _cleanup_preselected_empty_directory(path: Path) -> None:
    """Best-effort cleanup for an unguessable mkdir candidate.

    The candidate name is selected before ``mkdir`` so a wrapper that raises
    after the syscall cannot hide which directory may have been created. The
    flat cleanup remains identity-bound and deliberately retains the empty
    directory where final directory deletion is name-bound. On Windows, an
    exception raised by a ``mkdir`` wrapper leaves no held handle or marker
    proving that the current pathname is still the created directory, so this
    ambiguous boundary must also fail closed without touching it.
    """

    if _uses_windows_capability_handles():
        return
    parent_descriptor: int | None = None
    descriptor: int | None = None
    try:
        expected = _raw_directory_identity(path)
        if not _uses_windows_capability_handles():
            parent_descriptor = _open_plain_directory_descriptor(path.parent)
            descriptor = _open_plain_directory_descriptor(path)
        _cleanup_owned_flat_directory(
            path,
            path.name,
            parent_descriptor=parent_descriptor,
            descriptor=descriptor,
            expected=expected,
            marker_name=None,
            marker_descriptor=None,
            marker_identity=None,
            allowed_names=frozenset(),
        )
        descriptor = None
    except BaseException as cleanup_error:
        del cleanup_error
    finally:
        _close_descriptor_quietly(descriptor)
        _close_descriptor_quietly(parent_descriptor)


def _ensure_release_root(path: Path) -> _DirectoryCapability:
    """Return a held capability for a preexisting or exclusively created root."""

    try:
        os.lstat(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise SyncError(
            "SYNC_OUTPUT",
            "The release directory could not be inspected safely.",
            "Release ownership must be proven before publication.",
            ["Choose an accessible local --release-root and retry."],
            details=(f"Error class: {type(exc).__name__}",),
        ) from exc
    else:
        return _open_directory_capability(path, owned=False)

    try:
        _directory_identity(path.parent)
    except (OSError, SyncError, ValueError) as exc:
        raise SyncError(
            "SYNC_OUTPUT",
            "The release directory could not be created safely.",
            "Its parent must already be a stable, writable local directory.",
            ["Create the parent directory explicitly, then retry."],
            details=(f"Error class: {type(exc).__name__}",),
        ) from exc
    candidate: Path | None = None
    creation_error: BaseException | None = None
    for _attempt in range(8):
        selected_candidate = path.parent / f".ancestryllm-release-root-{uuid.uuid4().hex}"
        try:
            os.mkdir(selected_candidate, 0o700)
        except FileExistsError:
            continue
        except BaseException as exc:
            _cleanup_preselected_empty_directory(selected_candidate)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            creation_error = exc
            break
        candidate = selected_candidate
        break
    if candidate is None:
        error_class = (
            type(creation_error).__name__ if creation_error is not None else "FileExistsError"
        )
        raise SyncError(
            "SYNC_OUTPUT",
            "The release directory could not be created safely.",
            "Its parent must already be a stable, writable local directory.",
            ["Create the parent directory explicitly, then retry."],
            details=(f"Error class: {error_class}",),
        ) from creation_error
    candidate_capability = _open_directory_capability(candidate, owned=True)
    try:
        _exclusive_rename_directory(candidate, path)
    except OSError as exc:
        _cleanup_capability_tree(candidate_capability)
        if exc.errno in {errno.EEXIST, errno.ENOTEMPTY, errno.EISDIR, errno.ENOTDIR}:
            return _open_directory_capability(path, owned=False)
        raise SyncError(
            "SYNC_OUTPUT",
            "The release directory could not be claimed safely.",
            "The filesystem did not provide exclusive no-replace directory publication.",
            ["Choose a supported local filesystem and retry."],
            details=(f"Error class: {type(exc).__name__}",),
        ) from exc
    except BaseException:
        _cleanup_capability_tree(candidate_capability)
        raise
    candidate_capability.selected_path = path
    try:
        _require_selected_capability(candidate_capability)
    except BaseException:
        _cleanup_capability_tree(candidate_capability)
        raise
    return candidate_capability


def _create_staging_directory(
    release_root: _DirectoryCapability,
    prefix: str,
) -> tuple[
    Path,
    str,
    int | None,
    _DirectoryIdentity,
    str,
    int,
    _DirectoryIdentity,
]:
    """Create staging under the physical held root and retain its identity."""

    _require_selected_capability(release_root)
    name = f"{prefix}{uuid.uuid4().hex}"
    path = _capability_current_path(release_root) / name
    created = False
    identity: _DirectoryIdentity | None = None
    descriptor: int | None = None
    marker_name = f".ancestryllm-staging-{uuid.uuid4().hex}"
    marker_descriptor: int | None = None
    marker_identity: _DirectoryIdentity | None = None
    try:
        if release_root.descriptor is not None:
            os.mkdir(name, 0o700, dir_fd=release_root.descriptor)
        else:
            path.mkdir(mode=0o700)
        created = True
        path = _capability_current_path(release_root) / name
        if release_root.descriptor is not None:
            entry_stat = os.stat(
                name,
                dir_fd=release_root.descriptor,
                follow_symlinks=False,
            )
            if not stat.S_ISDIR(entry_stat.st_mode):
                raise SyncError(
                    "SYNC_OUTPUT",
                    "The staging path is not a real directory.",
                    "Publication cannot continue without stable staging ownership.",
                    ["Stop concurrent filesystem changes and retry."],
                )
            identity = _DirectoryIdentity.from_stat(entry_stat)
            descriptor = _open_plain_directory_entry_descriptor(
                name,
                release_root.descriptor,
            )
            opened = _DirectoryIdentity.from_stat(os.fstat(descriptor))
            if not identity.same_object(opened):
                raise SyncError(
                    "SYNC_OUTPUT",
                    "The staging directory changed while it was being claimed.",
                    "Publication cannot continue without stable staging ownership.",
                    ["Stop concurrent filesystem changes and retry."],
                )
        else:
            identity = _directory_identity(path)
        if descriptor is not None:
            marker_descriptor = _open_held_marker(
                Path(marker_name),
                create=True,
                directory_descriptor=descriptor,
            )
        else:
            marker_descriptor = _open_held_marker(path / marker_name, create=True)
        os.write(marker_descriptor, os.urandom(32))
        os.fsync(marker_descriptor)
        marker_identity = _DirectoryIdentity.from_stat(os.fstat(marker_descriptor))
        _require_selected_capability(release_root)
        return (
            path,
            name,
            descriptor,
            identity,
            marker_name,
            marker_descriptor,
            marker_identity,
        )
    except BaseException:
        if identity is None:
            try:
                path = _capability_current_path(release_root) / name
                if release_root.descriptor is not None:
                    entry_stat = os.stat(
                        name,
                        dir_fd=release_root.descriptor,
                        follow_symlinks=False,
                    )
                    if not stat.S_ISDIR(entry_stat.st_mode):
                        raise ValueError("staging path is not an ordinary directory")
                    identity = _DirectoryIdentity.from_stat(entry_stat)
                    if descriptor is None:
                        descriptor = _open_plain_directory_entry_descriptor(
                            name,
                            release_root.descriptor,
                        )
                    opened = _DirectoryIdentity.from_stat(os.fstat(descriptor))
                    if not identity.same_object(opened):
                        raise ValueError("staging directory identity changed")
                else:
                    identity = _raw_directory_identity(path)
                created = True
            except (OSError, SyncError, ValueError):
                identity = None
        try:
            if created and identity is not None:
                _cleanup_owned_flat_directory(
                    path,
                    name,
                    parent_descriptor=release_root.descriptor,
                    descriptor=descriptor,
                    expected=identity,
                    marker_name=marker_name if marker_descriptor is not None else None,
                    marker_descriptor=marker_descriptor,
                    marker_identity=marker_identity,
                    allowed_names=frozenset(),
                )
            else:
                _close_descriptor_quietly(marker_descriptor)
                _close_descriptor_quietly(descriptor)
        except BaseException:
            _close_descriptor_quietly(marker_descriptor)
            _close_descriptor_quietly(descriptor)
        raise


def _cleanup_staging_directory(
    release_root: _DirectoryCapability,
    name: str,
    descriptor: int | None,
    expected: _DirectoryIdentity,
    marker_name: str,
    marker_descriptor: int | None,
    marker_identity: _DirectoryIdentity,
) -> None:
    """Delete staging only when it still matches its held live identity."""

    try:
        physical_root = _capability_current_path(release_root)
        path = physical_root / name
        cleanup_marker_name: str | None = marker_name
        cleanup_marker_identity: _DirectoryIdentity | None = marker_identity
        if (
            not _uses_windows_capability_handles()
            and descriptor is not None
            and marker_descriptor is not None
        ):
            held_marker_stat = os.fstat(marker_descriptor)
            held_marker = _DirectoryIdentity.from_stat(held_marker_stat)
            held_directory = _DirectoryIdentity.from_stat(os.fstat(descriptor))
            if (
                held_marker_stat.st_nlink == 0
                and marker_identity.same_object(held_marker)
                and expected.same_object(held_directory)
            ):
                cleanup_marker_name = None
                cleanup_marker_identity = None
        _cleanup_owned_flat_directory(
            path,
            name,
            parent_descriptor=release_root.descriptor,
            descriptor=descriptor,
            expected=expected,
            marker_name=cleanup_marker_name,
            marker_descriptor=marker_descriptor,
            marker_identity=cleanup_marker_identity,
            allowed_names=_STAGING_CLEANUP_NAMES,
        )
    except BaseException:
        return


def _publication_destination_is_selected(
    destination_name: str,
    release_root: _DirectoryCapability,
    directory_descriptor: int | None,
    directory_identity: _DirectoryIdentity,
) -> bool:
    """Prove the held publication is at the selected destination."""

    if not _capability_matches_selected(release_root):
        return False
    try:
        if release_root.descriptor is not None:
            current_stat = os.stat(
                destination_name,
                dir_fd=release_root.descriptor,
                follow_symlinks=False,
            )
        else:
            current_stat = os.lstat(release_root.selected_path / destination_name)
        if not stat.S_ISDIR(current_stat.st_mode):
            return False
        current = _DirectoryIdentity.from_stat(current_stat)
        held = (
            _DirectoryIdentity.from_stat(os.fstat(directory_descriptor))
            if directory_descriptor is not None
            else current
        )
    except (OSError, SyncError, ValueError):
        return False
    return directory_identity.same_object(held) and held.same_object(current)


def _publication_root_changed_error() -> SyncError:
    return SyncError(
        "SYNC_OUTPUT",
        "The release root changed during final publication.",
        "The selected path no longer contains the held release destination.",
        ["Restore the selected --release-root and retry without concurrent changes."],
    )


def _publication_incomplete_error(cause: BaseException) -> SyncError:
    return SyncError(
        "SYNC_PUBLICATION_INCOMPLETE",
        "Release publication could not be finalized or rolled back.",
        "An incomplete generation directory with an ownership marker may remain.",
        [
            "Do not use or rename the incomplete generation as a release.",
            "Inspect the release root and remove only an empty app-owned residue.",
            "Retry with a new patch version after the filesystem issue is resolved.",
        ],
        details=(f"Error class: {type(cause).__name__}",),
    )


def _remove_published_staging_marker(
    marker_name: str,
    marker_identity: _DirectoryIdentity,
    directory_descriptor: int | None,
    directory_identity: _DirectoryIdentity,
    destination_name: str,
    release_root: _DirectoryCapability,
    transaction: _PublicationTransactionState,
) -> BaseException | None:
    """Remove the marker at the irreversible commit boundary.

    A failed delete retains the open marker descriptor in ``transaction`` so
    rollback and location proofs remain capability-bound.
    """

    descriptor = transaction.marker_descriptor
    if descriptor is None:
        return SyncError(
            "SYNC_OUTPUT",
            "The staged release marker capability is unavailable.",
            "Publication cannot be finalized without retained ownership.",
            ["Retry with a new patch version after inspecting the release root."],
        )
    try:
        if not _publication_destination_is_selected(
            destination_name,
            release_root,
            directory_descriptor,
            directory_identity,
        ):
            raise _publication_root_changed_error()
        marker_path = _held_file_path(descriptor)
        held_stat = os.fstat(descriptor)
        held = _DirectoryIdentity.from_stat(held_stat)
        current = _marker_identity_at(
            marker_name,
            directory_descriptor=directory_descriptor,
            marker_path=marker_path,
        )
        if (
            not stat.S_ISREG(held_stat.st_mode)
            or not marker_identity.same_object(held)
            or not held.same_object(current)
        ):
            raise SyncError(
                "SYNC_OUTPUT",
                "The staged release marker changed unexpectedly.",
                "The committed directory cannot be finalized without proving marker ownership.",
                ["Inspect the release root and retry with a new patch version if needed."],
            )
        if not _publication_destination_is_selected(
            destination_name,
            release_root,
            directory_descriptor,
            directory_identity,
        ):
            raise _publication_root_changed_error()
        if _uses_windows_capability_handles():
            _windows_mark_descriptor_for_deletion(descriptor)
            close_error: BaseException | None = None
            for _attempt in range(2):
                try:
                    os.close(descriptor)
                    close_error = None
                    break
                except OSError as exc:
                    if exc.errno == errno.EBADF:
                        close_error = None
                        break
                    close_error = exc
                except BaseException as exc:
                    close_error = exc
            if close_error is not None:
                return close_error
            transaction.committed = True
            transaction.marker_descriptor = None
            return None
        elif directory_descriptor is not None:
            os.unlink(marker_name, dir_fd=directory_descriptor)
        else:
            os.unlink(marker_path)
        if not _publication_destination_is_selected(
            destination_name,
            release_root,
            directory_descriptor,
            directory_identity,
        ):
            raise _publication_root_changed_error()
    except BaseException as exc:
        return exc
    transaction.committed = True
    transaction.marker_descriptor = None
    _close_descriptor_quietly(descriptor)
    return None


def _rollback_published_directory(
    staging_name: str,
    destination_name: str,
    release_root: _DirectoryCapability,
) -> bool:
    """Move a not-yet-finalized commit back to its private staging name."""

    try:
        physical_root = _capability_current_path(release_root)
        source = (
            Path(destination_name)
            if release_root.descriptor is not None
            else physical_root / destination_name
        )
        destination = (
            Path(staging_name)
            if release_root.descriptor is not None
            else physical_root / staging_name
        )
        _exclusive_rename_directory(
            source,
            destination,
            source_dir_fd=release_root.descriptor,
            destination_dir_fd=release_root.descriptor,
        )
    except (OSError, SyncError):
        return False
    return True


def _finalize_published_directory(
    staging_name: str,
    destination_name: str,
    release_root: _DirectoryCapability,
    marker_name: str,
    marker_identity: _DirectoryIdentity,
    directory_descriptor: int | None,
    directory_identity: _DirectoryIdentity,
    transaction: _PublicationTransactionState,
) -> BaseException | None:
    """Finalize a commit, or roll it back before exposing marker cleanup failure."""

    error = _remove_published_staging_marker(
        marker_name,
        marker_identity,
        directory_descriptor,
        directory_identity,
        destination_name,
        release_root,
        transaction,
    )
    if error is None:
        return None
    return _recover_interrupted_publication(
        error,
        staging_name,
        destination_name,
        release_root,
        directory_descriptor,
        directory_identity,
        marker_name,
        marker_identity,
        transaction,
    )


def _publish_directory_no_clobber(
    staging_name: str,
    destination_name: str,
    release_root: _DirectoryCapability,
) -> None:
    """Publish within the held root, rollback, and fail if its selected path moved."""

    physical_root = _capability_current_path(release_root)
    source = (
        Path(staging_name) if release_root.descriptor is not None else physical_root / staging_name
    )
    destination = (
        Path(destination_name)
        if release_root.descriptor is not None
        else physical_root / destination_name
    )
    try:
        _require_selected_capability(release_root)
        _exclusive_rename_directory(
            source,
            destination,
            source_dir_fd=release_root.descriptor,
            destination_dir_fd=release_root.descriptor,
        )
    except OSError as exc:
        if exc.errno in {errno.EEXIST, errno.ENOTEMPTY, errno.EISDIR, errno.ENOTDIR}:
            raise SyncError(
                "SYNC_OUTPUT",
                "The release destination was claimed by another operation.",
                "Existing and concurrent release paths are immutable and cannot be replaced.",
                ["Wait one second and retry the operation."],
            ) from exc
        raise SyncError(
            "SYNC_OUTPUT",
            "The staged release could not be published safely.",
            "The filesystem did not complete an exclusive no-replace directory rename.",
            ["Check free space and use a supported local filesystem, then retry."],
            details=(f"Error class: {type(exc).__name__}",),
        ) from exc
    if _capability_matches_selected(release_root):
        return
    try:
        _exclusive_rename_directory(
            destination,
            source,
            source_dir_fd=release_root.descriptor,
            destination_dir_fd=release_root.descriptor,
        )
    except OSError:
        pass
    raise SyncError(
        "SYNC_OUTPUT",
        "The release root changed during final publication.",
        "The staged generation was rolled back inside the held original root.",
        ["Restore the selected --release-root and retry without concurrent changes."],
    )


def _held_staging_location(
    staging_name: str,
    destination_name: str,
    release_root: _DirectoryCapability,
    directory_descriptor: int | None,
    directory_identity: _DirectoryIdentity,
    marker_name: str,
    marker_identity: _DirectoryIdentity,
    transaction: _PublicationTransactionState,
) -> str | None:
    """Return the owned staging directory's current transaction name."""

    marker_descriptor = transaction.marker_descriptor
    if marker_descriptor is None:
        return None
    try:
        held_marker_stat = os.fstat(marker_descriptor)
        held_marker = _DirectoryIdentity.from_stat(held_marker_stat)
        if not stat.S_ISREG(held_marker_stat.st_mode) or not marker_identity.same_object(
            held_marker
        ):
            return None
        marker_is_unlinked = held_marker_stat.st_nlink == 0
        if marker_is_unlinked:
            if _uses_windows_capability_handles() or directory_descriptor is None:
                return None
            try:
                os.stat(
                    marker_name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                return None
            current_directory_path = _held_file_path(directory_descriptor)
        else:
            marker_path = _held_file_path(marker_descriptor)
            current_marker = _DirectoryIdentity.from_stat(os.lstat(marker_path))
            if marker_path.name != marker_name or not held_marker.same_object(current_marker):
                return None
            current_directory_path = marker_path.parent
        physical_root = _capability_current_path(release_root)
        if os.path.normcase(os.path.abspath(current_directory_path.parent)) != os.path.normcase(
            os.path.abspath(physical_root)
        ):
            return None
        current_directory_stat = os.lstat(current_directory_path)
        current_directory = _DirectoryIdentity.from_stat(current_directory_stat)
        held_directory = (
            _DirectoryIdentity.from_stat(os.fstat(directory_descriptor))
            if directory_descriptor is not None
            else current_directory
        )
        if (
            not stat.S_ISDIR(current_directory_stat.st_mode)
            or not directory_identity.same_object(held_directory)
            or not held_directory.same_object(current_directory)
        ):
            return None
        if current_directory_path.name in {staging_name, destination_name}:
            return current_directory_path.name
    except (OSError, SyncError, ValueError):
        return None
    return None


def _prove_committed_unlinked_marker(
    destination_name: str,
    release_root: _DirectoryCapability,
    directory_descriptor: int | None,
    directory_identity: _DirectoryIdentity,
    marker_name: str,
    marker_identity: _DirectoryIdentity,
    transaction: _PublicationTransactionState,
) -> bool:
    """Recover a POSIX commit interrupted after unlink but before state storage."""

    marker_descriptor = transaction.marker_descriptor
    if (
        _uses_windows_capability_handles()
        or marker_descriptor is None
        or directory_descriptor is None
        or release_root.descriptor is None
    ):
        return False
    try:
        marker_stat = os.fstat(marker_descriptor)
        marker_held = _DirectoryIdentity.from_stat(marker_stat)
        if (
            not stat.S_ISREG(marker_stat.st_mode)
            or marker_stat.st_nlink != 0
            or not marker_identity.same_object(marker_held)
        ):
            return False
        try:
            os.stat(
                marker_name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            return False
        if not _publication_destination_is_selected(
            destination_name,
            release_root,
            directory_descriptor,
            directory_identity,
        ):
            return False
    except (OSError, SyncError, ValueError):
        return False
    transaction.committed = True
    transaction.marker_descriptor = None
    _close_descriptor_quietly(marker_descriptor)
    return True


def _recover_interrupted_publication(
    error: BaseException,
    staging_name: str,
    destination_name: str,
    release_root: _DirectoryCapability,
    directory_descriptor: int | None,
    directory_identity: _DirectoryIdentity,
    marker_name: str,
    marker_identity: _DirectoryIdentity,
    transaction: _PublicationTransactionState,
) -> BaseException | None:
    """Roll an interrupted owned rename back while ownership remains held."""

    if transaction.committed:
        return None
    if _prove_committed_unlinked_marker(
        destination_name,
        release_root,
        directory_descriptor,
        directory_identity,
        marker_name,
        marker_identity,
        transaction,
    ):
        return None
    location = _held_staging_location(
        staging_name,
        destination_name,
        release_root,
        directory_descriptor,
        directory_identity,
        marker_name,
        marker_identity,
        transaction,
    )
    if location == destination_name:
        try:
            _rollback_published_directory(
                staging_name,
                destination_name,
                release_root,
            )
        except BaseException as rollback_error:
            del rollback_error
        location = _held_staging_location(
            staging_name,
            destination_name,
            release_root,
            directory_descriptor,
            directory_identity,
            marker_name,
            marker_identity,
            transaction,
        )
    if location == staging_name:
        return error
    if location != destination_name:
        return _publication_incomplete_error(error)
    cleanup_error = _remove_published_staging_marker(
        marker_name,
        marker_identity,
        directory_descriptor,
        directory_identity,
        destination_name,
        release_root,
        transaction,
    )
    if cleanup_error is None:
        return None
    return _publication_incomplete_error(cleanup_error)


def _publish_and_finalize_directory(
    staging_name: str,
    destination_name: str,
    release_root: _DirectoryCapability,
    directory_descriptor: int | None,
    directory_identity: _DirectoryIdentity,
    marker_name: str,
    marker_identity: _DirectoryIdentity,
    transaction: _PublicationTransactionState,
) -> BaseException | None:
    """Treat no-clobber publication and marker finalization as one transaction."""

    try:
        _publish_directory_no_clobber(
            staging_name,
            destination_name,
            release_root,
        )
        return _finalize_published_directory(
            staging_name,
            destination_name,
            release_root,
            marker_name,
            marker_identity,
            directory_descriptor,
            directory_identity,
            transaction,
        )
    except BaseException as exc:
        if transaction.committed:
            return None
        return _recover_interrupted_publication(
            exc,
            staging_name,
            destination_name,
            release_root,
            directory_descriptor,
            directory_identity,
            marker_name,
            marker_identity,
            transaction,
        )


def _report_committed_status(lines: Sequence[str]) -> None:
    """Best-effort status output that cannot invalidate an immutable commit."""
    try:
        for line in lines:
            print(line)
    except BaseException:
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


def _normalize_command_paths(
    args: argparse.Namespace,
    command: str,
    ingress: FileIngressPolicy,
) -> None:
    """Normalize every standalone user-supplied sync path at one boundary."""

    args.master = ingress.normalize_path(args.master, FileKind.GEDCOM, absolute=True)
    args.release_root = ingress.normalize_path(
        args.release_root,
        FileKind.MANIFEST,
        absolute=True,
    )
    if command == "update":
        if args.manifest:
            args.manifest = ingress.normalize_path(
                args.manifest,
                FileKind.MANIFEST,
                absolute=True,
            )
        return
    args.manifest = ingress.normalize_path(
        args.manifest,
        FileKind.MANIFEST,
        absolute=True,
    )


def _perform_update(
    args: argparse.Namespace,
    core: ModuleType,
    ingress: FileIngressPolicy,
    identity_resolver: IdentityResolver | None = None,
) -> int:
    """Execute one offline-first update and publish an atomic generation bundle."""
    cancellation_checkpoint()
    master: Path = args.master
    release_root: Path = args.release_root
    master_fingerprint = ingress.fingerprint(master, FileKind.GEDCOM)
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
    specs = _snapshot_specs(args, core, ingress)
    manifest_path: Path | None = args.manifest
    manifest_fingerprint = (
        ingress.fingerprint(manifest_path, FileKind.MANIFEST) if manifest_path is not None else None
    )
    if args.initialize_manifest:
        manifest = _new_manifest(master, release_root, master_fingerprint)
    else:
        assert manifest_path is not None
        assert manifest_fingerprint is not None
        manifest = _load_manifest(
            manifest_path,
            ingress,
            manifest_fingerprint,
            master_fingerprint,
        )
    _validate_snapshot_continuity(manifest, specs)

    def verify_inputs() -> None:
        ingress.verify(master, FileKind.GEDCOM, master_fingerprint)
        for spec in specs:
            ingress.verify(spec.path, FileKind.GEDCOM, spec.fingerprint)
        if manifest_path is not None and manifest_fingerprint is not None:
            ingress.verify(
                manifest_path,
                FileKind.MANIFEST,
                manifest_fingerprint,
            )

    guarded_identity_resolver = identity_resolver
    if identity_resolver is not None:
        selected_identity_resolver = identity_resolver

        def verify_and_resolve(left: Any, right: Any) -> Mapping[str, object]:
            verify_inputs()
            try:
                return selected_identity_resolver(left, right)
            finally:
                verify_inputs()

        guarded_identity_resolver = verify_and_resolve

    if all(
        manifest.get("active_snapshots", {}).get(spec.source_id) == spec.snapshot_id
        for spec in specs
    ):
        verify_inputs()
        print(
            "No update was needed: every supplied snapshot is already active "
            "with the same SHA-256 checksum. No release files were changed."
        )
        return 0
    try:
        sources = core.load_sources(
            [master, *(spec.path for spec in specs)],
            ingress,
            {
                master: master_fingerprint.snapshot,
                **{spec.path: spec.fingerprint.snapshot for spec in specs},
            },
        )
    except (OSError, ValueError) as exc:
        raise SyncError(
            "SYNC_PARSE",
            "A master or snapshot GEDCOM could not be parsed safely.",
            "Publishing a partial generation could break relationships or citations.",
            ["Open the named file and repair the reported GEDCOM line, then retry."],
            details=(f"Error class: {type(exc).__name__}",),
        ) from exc
    cancellation_checkpoint()
    stats = SyncStats()
    pointer_map, bindings, _ = _match_people(
        sources,
        specs,
        manifest,
        core,
        stats,
        provider_id=args.provider,
        identity_resolver=guarded_identity_resolver,
    )
    cancellation_checkpoint()
    pointer_map, nonpeople = _map_nonpeople(sources, pointer_map, manifest, core, stats)
    cancellation_checkpoint()
    people, block_registry = _reconcile_person_blocks(
        sources,
        specs,
        pointer_map,
        manifest,
        core,
        stats,
        initialize=args.initialize_manifest,
    )
    cancellation_checkpoint()
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
            provider_id=args.provider,
            dry_run=True,
        )
        verify_inputs()
        print(report, end="")
        return 0
    release_root_capability = _ensure_release_root(release_root)
    final_dir = release_root / release_name
    staging: Path | None = None
    staging_name: str | None = None
    staging_descriptor: int | None = None
    staging_identity: _DirectoryIdentity | None = None
    staging_marker_name: str | None = None
    staging_marker_descriptor: int | None = None
    staging_marker_identity: _DirectoryIdentity | None = None
    transaction: _PublicationTransactionState | None = None
    try:
        (
            staging,
            staging_name,
            staging_descriptor,
            staging_identity,
            staging_marker_name,
            staging_marker_descriptor,
            staging_marker_identity,
        ) = _create_staging_directory(release_root_capability, ".gedcom-sync-")
        cancellation_checkpoint()
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
        parent_manifest_path = str(manifest_path) if manifest_path is not None else None
        parent_manifest_sha = (
            manifest_fingerprint.sha256 if manifest_fingerprint is not None else None
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
            provider_id=args.provider,
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
        verify_inputs()
        cancellation_checkpoint()
        assert staging_marker_descriptor is not None
        assert staging_marker_name is not None
        assert staging_marker_identity is not None
        assert staging_identity is not None
        transaction = _PublicationTransactionState(staging_marker_descriptor)
        with non_interruptible_section("publishing incremental release"):
            finalization_error = _publish_and_finalize_directory(
                staging_name,
                release_name,
                release_root_capability,
                staging_descriptor,
                staging_identity,
                staging_marker_name,
                staging_marker_identity,
                transaction,
            )
            if finalization_error is not None:
                raise finalization_error
        if not transaction.committed:
            raise SyncError(
                "SYNC_OUTPUT",
                "The release transaction did not reach a committed state.",
                "Publication stopped before the ownership marker was removed.",
                ["Inspect the release root and retry with a new patch version."],
            )
    except BaseException as exc:
        if transaction is not None and transaction.committed:
            _close_descriptor_quietly(staging_descriptor)
            _close_capability_quietly(release_root_capability)
            if isinstance(exc, CancellationError):
                raise
            return 0
        cleanup_marker_descriptor = (
            transaction.marker_descriptor if transaction is not None else staging_marker_descriptor
        )
        if (
            staging_name is not None
            and staging_identity is not None
            and staging_marker_name is not None
            and staging_marker_identity is not None
        ):
            _cleanup_staging_directory(
                release_root_capability,
                staging_name,
                staging_descriptor,
                staging_identity,
                staging_marker_name,
                cleanup_marker_descriptor,
                staging_marker_identity,
            )
            staging_descriptor = None
            staging_marker_descriptor = None
        _cleanup_empty_release_root(release_root_capability)
        raise
    _close_descriptor_quietly(staging_descriptor)
    _close_capability_quietly(release_root_capability)
    _report_committed_status(
        (
            f"Incremental update complete: {final_dir}",
            f"Master GEDCOM: {final_dir / 'master.ged'}",
            f"Manifest: {final_dir / 'manifest.json'}",
            f"Update report: {final_dir / 'update.md'}",
        )
    )
    return 0


def _master_block_index(
    path: Path,
    core: ModuleType,
    ingress: FileIngressPolicy,
    expected: FileSnapshot,
) -> dict[str, dict[str, str]]:
    """Index person blocks by pointer for explicit manual rebase comparison."""
    result: dict[str, dict[str, str]] = defaultdict(dict)
    for record in core.iter_gedcom_records(path, ingress, expected):
        cancellation_checkpoint()
        if record.tag != "INDI":
            continue
        for block in core._top_level_blocks(record.lines):
            cancellation_checkpoint()
            result[record.pointer][_block_key(block, core)] = core.parse_gedcom_line(block[0]).tag
    return result


def _perform_rebase(args: argparse.Namespace, core: ModuleType, ingress: FileIngressPolicy) -> int:
    """Adopt external edits explicitly as protected manual provenance."""
    cancellation_checkpoint()
    master: Path = args.master
    manifest_path: Path = args.manifest
    release_root: Path = args.release_root
    master_fingerprint = ingress.fingerprint(master, FileKind.GEDCOM)
    manifest_fingerprint = ingress.fingerprint(manifest_path, FileKind.MANIFEST)
    manifest = ingress.read_json(
        manifest_path,
        FileKind.MANIFEST,
        require_object=True,
        expected=manifest_fingerprint.snapshot,
    )
    assert isinstance(manifest, dict)
    _validate_manifest(manifest)
    previous_path = ingress.normalize_path(
        str(manifest.get("master", {}).get("path", "")),
        FileKind.GEDCOM,
        absolute=True,
    )
    previous_fingerprint = ingress.fingerprint(previous_path, FileKind.GEDCOM)
    expected_previous_sha = str(manifest["master"]["sha256"])
    if previous_fingerprint.sha256 != expected_previous_sha:
        raise SyncError(
            "MANIFEST_MASTER_MISMATCH",
            "The previous master GEDCOM does not match the supplied manifest.",
            "Manual rebase comparisons require the exact immutable parent generation.",
            [
                "Restore master.ged and manifest.json from the same release bundle.",
                "Preserve the externally edited master separately, then retry the rebase.",
            ],
            details=(
                f"Expected SHA-256: {expected_previous_sha}",
                f"Actual SHA-256: {previous_fingerprint.sha256}",
            ),
        )
    previous = _master_block_index(
        previous_path,
        core,
        ingress,
        previous_fingerprint.snapshot,
    )
    current = _master_block_index(
        master,
        core,
        ingress,
        master_fingerprint.snapshot,
    )
    cancellation_checkpoint()
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
        cancellation_checkpoint()
        registry = manifest.setdefault("blocks", {}).setdefault(pointer, {})
        for block_hash in hashes:
            cancellation_checkpoint()
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
        ingress.verify(master, FileKind.GEDCOM, master_fingerprint)
        ingress.verify(previous_path, FileKind.GEDCOM, previous_fingerprint)
        ingress.verify(
            manifest_path,
            FileKind.MANIFEST,
            manifest_fingerprint,
        )
        print(summary, end="")
        return 0
    release_root_capability = _ensure_release_root(release_root)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_dir = release_root / f"g{next_generation:04d}-{timestamp}"
    staging: Path | None = None
    staging_name: str | None = None
    staging_descriptor: int | None = None
    staging_identity: _DirectoryIdentity | None = None
    staging_marker_name: str | None = None
    staging_marker_descriptor: int | None = None
    staging_marker_identity: _DirectoryIdentity | None = None
    transaction: _PublicationTransactionState | None = None
    try:
        (
            staging,
            staging_name,
            staging_descriptor,
            staging_identity,
            staging_marker_name,
            staging_marker_descriptor,
            staging_marker_identity,
        ) = _create_staging_directory(release_root_capability, ".gedcom-rebase-")
        cancellation_checkpoint()
        ingress.copy_to(
            master,
            staging / "master.ged",
            FileKind.GEDCOM,
            expected=master_fingerprint,
        )
        master_sha = _sha256_file(staging / "master.ged")
        prior = copy.deepcopy(manifest.get("master"))
        manifest["generation"] = next_generation
        manifest["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        manifest["parent_release"] = {
            "generation": next_generation - 1,
            "master": prior,
            "manifest_path": str(manifest_path),
            "manifest_sha256": manifest_fingerprint.sha256,
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
        ingress.verify(master, FileKind.GEDCOM, master_fingerprint)
        ingress.verify(previous_path, FileKind.GEDCOM, previous_fingerprint)
        ingress.verify(
            manifest_path,
            FileKind.MANIFEST,
            manifest_fingerprint,
        )
        cancellation_checkpoint()
        assert staging_marker_descriptor is not None
        assert staging_marker_name is not None
        assert staging_marker_identity is not None
        assert staging_identity is not None
        transaction = _PublicationTransactionState(staging_marker_descriptor)
        with non_interruptible_section("publishing incremental release"):
            finalization_error = _publish_and_finalize_directory(
                staging_name,
                final_dir.name,
                release_root_capability,
                staging_descriptor,
                staging_identity,
                staging_marker_name,
                staging_marker_identity,
                transaction,
            )
            if finalization_error is not None:
                raise finalization_error
        if not transaction.committed:
            raise SyncError(
                "SYNC_OUTPUT",
                "The release transaction did not reach a committed state.",
                "Publication stopped before the ownership marker was removed.",
                ["Inspect the release root and retry with a new patch version."],
            )
    except BaseException as exc:
        if transaction is not None and transaction.committed:
            _close_descriptor_quietly(staging_descriptor)
            _close_capability_quietly(release_root_capability)
            if isinstance(exc, CancellationError):
                raise
            return 0
        cleanup_marker_descriptor = (
            transaction.marker_descriptor if transaction is not None else staging_marker_descriptor
        )
        if (
            staging_name is not None
            and staging_identity is not None
            and staging_marker_name is not None
            and staging_marker_identity is not None
        ):
            _cleanup_staging_directory(
                release_root_capability,
                staging_name,
                staging_descriptor,
                staging_identity,
                staging_marker_name,
                cleanup_marker_descriptor,
                staging_marker_identity,
            )
            staging_descriptor = None
            staging_marker_descriptor = None
        _cleanup_empty_release_root(release_root_capability)
        raise
    _close_descriptor_quietly(staging_descriptor)
    _close_capability_quietly(release_root_capability)
    _report_committed_status((f"Manual rebase complete: {final_dir}",))
    return 0


def main(
    argv: Sequence[str],
    core: ModuleType,
    ingress: FileIngressPolicy | None = None,
    *,
    resolver_factory: ResolverFactory | None = None,
    raise_errors: bool = False,
) -> int:
    """Dispatch ``update`` or ``rebase`` and render transparent failures."""
    command = argv[0] if argv else ""
    policy = ingress or FileIngressPolicy()
    try:
        if command == "update":
            args = _build_update_parser().parse_args(list(argv[1:]))
            _normalize_command_paths(args, command, policy)
            identity_resolver: IdentityResolver | None = None
            if args.provider != "none":
                if resolver_factory is None:
                    raise AncestryError(
                        "LLM_SERVICE_UNAVAILABLE",
                        "Incremental LLM assistance requires the modular application service.",
                    )
                identity_resolver = resolver_factory(
                    args.provider,
                    args.model,
                    args.consent,
                )
            return _perform_update(args, core, policy, identity_resolver)
        if command == "rebase":
            args = _build_rebase_parser().parse_args(list(argv[1:]))
            _normalize_command_paths(args, command, policy)
            return _perform_rebase(args, core, policy)
        raise SyncError(
            "SYNC_CONFIGURATION",
            f"Unknown incremental command: {command or '(missing)'}",
            "Only update and rebase have defined provenance behavior.",
            ["Use gedcom_merge.py update --help or rebase --help."],
        )
    except FileIngressError as exc:
        if raise_errors:
            raise
        print(exc.render(), end="\n", file=__import__("sys").stderr)
        return exc.exit_code
    except SyncError as exc:
        if raise_errors:
            raise exc.as_ancestry_error() from exc
        print(exc.render(), end="", file=__import__("sys").stderr)
        return exc.exit_code
    except AncestryError:
        raise
    except Exception as exc:
        error = SyncError(
            "SYNC_OUTPUT",
            "The incremental operation stopped before publishing a release.",
            "An unexpected failure was caught to preserve atomic output.",
            [
                "Retry with --verbose and review the coded error above.",
                "Preserve the master, manifest, and snapshots for troubleshooting.",
            ],
            details=(f"Error class: {type(exc).__name__}",),
        )
        if raise_errors:
            raise error.as_ancestry_error() from exc
        print(error.render(), end="", file=__import__("sys").stderr)
        return error.exit_code
