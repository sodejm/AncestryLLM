"""Deterministic, loss-minimizing GEDCOM synchronization algorithms."""

from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import hashlib
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    from ancestryllm.gedcom.contracts import IdentityResolver

from ancestryllm.core.cancellation import (
    cancellation_checkpoint,
)
from ancestryllm.gedcom.identity import individual_from_record
from ancestryllm.gedcom.sync_contracts import (
    ATTACHMENT_TAGS,
    CONTROLLED_TAGS,
    RECORD_PREFIXES,
    SOURCE_ADMIN_TAGS,
    SnapshotSpec,
    SyncStats,
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


def _block_logical_identity(block: Sequence[str], core: ModuleType) -> str:
    """Identify a fact slot without treating its mutable value as a new fact."""
    if not block:
        return _hash_text("")
    root = core.parse_gedcom_line(block[0])
    identity_lines = [f"0 {root.tag}"]
    for child in _direct_blocks(block, core):
        cancellation_checkpoint()
        if core.parse_gedcom_line(child[0]).tag in {"ADDR", "DATE", "PLAC", "TYPE"}:
            identity_lines.extend(_relative_lines(child, core))
    return _hash_text("\n".join(identity_lines))


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
    exact = Counter(_structure_key(value, core) for value in attachments)
    citation_counts = Counter(
        _citation_identity(value, core)
        for value in attachments
        if core.parse_gedcom_line(value[0]).tag == "SOUR"
    )
    observed_citations: Counter[tuple[str, ...]] = Counter()
    for candidate in right_attachments:
        cancellation_checkpoint()
        candidate_key = _structure_key(candidate, core)
        candidate_tag = core.parse_gedcom_line(candidate[0]).tag
        citation_identity: tuple[str, ...] | None = None
        if candidate_tag == "SOUR":
            citation_identity = _citation_identity(candidate, core)
            observed_citations[citation_identity] += 1
        if exact[candidate_key]:
            if (
                citation_identity is not None
                and observed_citations[citation_identity] > citation_counts[citation_identity]
            ):
                attachments.append(list(candidate))
                exact[candidate_key] += 1
                citation_counts[citation_identity] += 1
                stats.citations_attached.append(candidate_key[:12])
            elif citation_identity is not None:
                stats.citations_deduplicated.append(candidate_key[:12])
            continue
        if citation_identity is not None:
            merged = False
            if observed_citations[citation_identity] <= citation_counts[citation_identity]:
                for index, existing in enumerate(attachments):
                    if core.parse_gedcom_line(existing[0]).tag != "SOUR":
                        continue
                    combined = _merge_citations(existing, candidate, core)
                    if combined is not None:
                        existing_key = _structure_key(existing, core)
                        exact[existing_key] -= 1
                        attachments[index] = combined
                        exact[_structure_key(combined, core)] += 1
                        stats.citations_attached.append(candidate_key[:12])
                        merged = True
                        break
            if merged:
                continue
            stats.citations_attached.append(candidate_key[:12])
        attachments.append(list(candidate))
        exact[candidate_key] += 1
        if citation_identity is not None:
            citation_counts[citation_identity] += 1
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


def _person_allocation_key(
    person: Any,
    original_pointer: str,
    core: ModuleType,
) -> tuple[tuple[str, ...], tuple[str, ...], str, str]:
    """Order new people by stable semantic content rather than record order."""
    normalized = _replace_header_pointer(person.raw_lines, "@PERSON@", core)
    return (
        _identity_fingerprint(person),
        tuple(sorted(_identifier_values(person, core))),
        "\n".join(_relative_lines(normalized, core)),
        original_pointer,
    )


def _record_allocation_key(
    record: Any,
    pointer_map: Mapping[str, str],
    core: ModuleType,
) -> tuple[str, str, str]:
    """Order non-person records by canonical content with a stable xref tie-break."""
    rewritten = _rewrite_lines(record.lines, pointer_map, core)
    normalized = (
        _replace_header_pointer(rewritten, "@RECORD@", core) if record.pointer else rewritten
    )
    return (
        record.tag,
        "\n".join(_relative_lines(normalized, core)),
        record.pointer,
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


def _person_from_record(record: Any, _core: ModuleType) -> Any:
    """Build one comparison person while retaining its full source record."""
    return individual_from_record(record)


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
        ordered_people = sorted(
            (
                (incoming, inverse_pointer_map.get(incoming.pointer, incoming.pointer))
                for incoming in people_by_source[index]
            ),
            key=lambda item: _person_allocation_key(item[0], item[1], core),
        )
        for incoming, original in ordered_people:
            cancellation_checkpoint()
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
    incoming_records = sorted(
        (
            record
            for source in sources[1:]
            for record in source.records
            if record.tag not in {"HEAD", "TRLR", "INDI", "SUBM"}
        ),
        key=lambda record: _record_allocation_key(record, pointer_map, core),
    )
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
    logical_tombstones: set[tuple[str, str]] = set()
    for item in manifest.get("manual_tombstones", ()):
        cancellation_checkpoint()
        person = str(item.get("person", ""))
        block_hash = str(item.get("block_hash", ""))
        logical_identity = item.get("logical_identity")
        if logical_identity is None:
            logical_identity = (
                block_registry.get(person, {}).get(block_hash, {}).get("logical_identity")
            )
        if isinstance(logical_identity, str):
            logical_tombstones.add((person, logical_identity))
    for source in sources:
        cancellation_checkpoint()
        for record in source.records:
            cancellation_checkpoint()
            if record.tag != "INDI":
                continue
            target = pointer_map.get(record.pointer, record.pointer)
            grouped[target].append((source_spec_by_path.get(record.source_file), record))
    people: list[Any] = []
    for target in sorted(grouped):
        cancellation_checkpoint()
        origin_records = grouped[target]
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
                logical_identity = _block_logical_identity(block, core)
                tag = core.parse_gedcom_line(block[0]).tag
                if origin_spec is not None and (
                    (target, key) in tombstones or (target, logical_identity) in logical_tombstones
                ):
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
                entry.setdefault("logical_identity", logical_identity)
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
        people.append(individual_from_record(record))
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
        return [*lines, "None.", ""]
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
        ("Cross-origin citation repetitions consolidated", stats.citations_deduplicated),
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
