"""Root-person, rooted auxiliary, and subtree graph operations."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

from ancestryllm.core.cancellation import cancellation_checkpoint
from ancestryllm.gedcom.identity import (
    XREF_RE,
    IndividualRecord,
    _top_level_blocks,
)
from ancestryllm.gedcom.model import GedcomRecord, parse_gedcom_line

_POINTER_REFERENCE_TAGS = frozenset(
    {
        "ALIA",
        "ASSO",
        "CHIL",
        "FAMC",
        "FAMS",
        "HUSB",
        "WIFE",
        "OBJE",
        "NOTE",
        "REPO",
        "SOUR",
        "SUBM",
        "SNOTE",
        "WITN",
    }
)
_ROOTED_AUXILIARY_RECORD_TAGS = frozenset({"NOTE", "SOUR", "OBJE", "REPO", "SUBM"})


def _rewrite_xrefs(line: str, pointer_map: dict[str, str]) -> str:
    """Rewrite exact reference fields, never arbitrary note text."""
    parsed = parse_gedcom_line(line)
    if not parsed.xref and parsed.tag not in _POINTER_REFERENCE_TAGS:
        return line
    if not parsed.xref and not XREF_RE.fullmatch(parsed.value.strip()):
        return line
    return XREF_RE.sub(
        lambda match: pointer_map.get(match.group(), match.group()),
        line,
    )


def _exact_pointer_references(lines: Iterable[str]) -> set[str]:
    """Return semantic, exact pointer references from already-rewritten lines."""
    references: set[str] = set()
    for line in lines:
        cancellation_checkpoint()
        parsed = parse_gedcom_line(line)
        value = parsed.value.strip()
        if not parsed.xref and parsed.tag in _POINTER_REFERENCE_TAGS and XREF_RE.fullmatch(value):
            references.add(value)
    return references


def _rooted_auxiliary_pointer_closure(
    seed_lines: Iterable[str],
    source_records: Sequence[GedcomRecord],
    pointer_map: dict[str, str],
) -> set[str]:
    """Find transitively referenced auxiliary records after xref rewriting."""
    auxiliary_records: dict[str, GedcomRecord] = {}
    for record in source_records:
        cancellation_checkpoint()
        if record.tag not in _ROOTED_AUXILIARY_RECORD_TAGS or not record.pointer:
            continue
        rewritten_header = parse_gedcom_line(_rewrite_xrefs(record.lines[0], pointer_map))
        auxiliary_records[rewritten_header.xref] = record

    retained: set[str] = set()
    pending = deque(sorted(_exact_pointer_references(seed_lines)))
    while pending:
        cancellation_checkpoint()
        pointer = pending.popleft()
        if pointer in retained:
            continue
        auxiliary_record = auxiliary_records.get(pointer)
        if auxiliary_record is None:
            continue
        retained.add(pointer)
        rewritten_lines = (_rewrite_xrefs(line, pointer_map) for line in auxiliary_record.lines)
        for referenced in sorted(_exact_pointer_references(rewritten_lines)):
            if referenced not in retained:
                pending.append(referenced)
    return retained


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
        record.pointer for record in records if record.full_name.casefold() == requested.casefold()
    }
    if len(name_matches) == 1:
        return name_matches.pop()
    if not name_matches and not mapped:
        raise ValueError(f"Root person not found: {requested}")
    raise ValueError(f"Root person is ambiguous: {requested!r}; use a unique GEDCOM pointer")


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
            family: {merged_pointer_map.get(member, member) for member in members}
            for family, members in family_members.items()
        }
    person_to_families: dict[str, set[str]] = defaultdict(set)
    for family_pointer, members in family_members.items():
        cancellation_checkpoint()
        for member in members:
            person_to_families[member].add(family_pointer)
    keep_people: set[str] = set()
    keep_families: set[str] = set()
    pending = [root_pointer]
    known_people = {person.pointer for person in people}
    while pending:
        cancellation_checkpoint()
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


def scoped_tree_pointers(
    root_pointer: str,
    people: Sequence[IndividualRecord],
    source_records: Iterable[GedcomRecord],
    scope: str = "connected",
    generations: int | None = None,
) -> tuple[set[str], set[str]]:
    """Select connected, ancestor, or descendant records without inventing edges."""
    cancellation_checkpoint()
    records: list[GedcomRecord] = []
    for record in source_records:
        cancellation_checkpoint()
        records.append(record)
    if scope == "connected":
        cancellation_checkpoint()
        return connected_tree_pointers(root_pointer, people, records)
    if scope not in {"ancestors", "descendants"}:
        raise ValueError("scope must be connected, ancestors, or descendants")
    if generations is not None and generations < 0:
        raise ValueError("generations must not be negative")

    families: dict[str, dict[str, set[str]]] = {}
    child_families: dict[str, set[str]] = defaultdict(set)
    spouse_families: dict[str, set[str]] = defaultdict(set)
    for record in records:
        cancellation_checkpoint()
        if record.tag != "FAM" or not record.pointer:
            continue
        roles: dict[str, set[str]] = {"parents": set(), "children": set()}
        for block in _top_level_blocks(record.lines):
            cancellation_checkpoint()
            first = parse_gedcom_line(block[0])
            pointers = set(XREF_RE.findall(first.value))
            if first.tag in {"HUSB", "WIFE"}:
                roles["parents"].update(pointers)
            elif first.tag == "CHIL":
                roles["children"].update(pointers)
        families[record.pointer] = roles
        for parent in roles["parents"]:
            cancellation_checkpoint()
            spouse_families[parent].add(record.pointer)
        for child in roles["children"]:
            cancellation_checkpoint()
            child_families[child].add(record.pointer)

    keep_people = {root_pointer}
    keep_families: set[str] = set()
    pending: deque[tuple[str, int]] = deque([(root_pointer, 0)])
    while pending:
        cancellation_checkpoint()
        pointer, depth = pending.popleft()
        if generations is not None and depth >= generations:
            continue
        family_ids = (
            child_families.get(pointer, set())
            if scope == "ancestors"
            else spouse_families.get(pointer, set())
        )
        for family_id in family_ids:
            cancellation_checkpoint()
            keep_families.add(family_id)
            roles = families[family_id]
            if scope == "ancestors":
                next_people = roles["parents"]
            else:
                keep_people.update(roles["parents"])
                next_people = roles["children"]
            for related in next_people:
                cancellation_checkpoint()
                if related not in keep_people:
                    keep_people.add(related)
                    pending.append((related, depth + 1))
    return keep_people, keep_families


__all__ = ["connected_tree_pointers", "resolve_root_person", "scoped_tree_pointers"]
