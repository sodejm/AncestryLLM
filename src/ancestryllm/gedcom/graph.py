"""Root-person and subtree graph operations."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Sequence

from ancestryllm.gedcom.engine import (
    XREF_RE,
    GedcomRecord,
    IndividualRecord,
    _top_level_blocks,
    connected_tree_pointers,
    parse_gedcom_line,
    resolve_root_person,
)


def scoped_tree_pointers(
    root_pointer: str,
    people: Sequence[IndividualRecord],
    source_records: Iterable[GedcomRecord],
    scope: str = "connected",
    generations: int | None = None,
) -> tuple[set[str], set[str]]:
    """Select connected, ancestor, or descendant records without inventing edges."""
    records = list(source_records)
    if scope == "connected":
        return connected_tree_pointers(root_pointer, people, records)
    if scope not in {"ancestors", "descendants"}:
        raise ValueError("scope must be connected, ancestors, or descendants")
    if generations is not None and generations < 0:
        raise ValueError("generations must not be negative")

    families: dict[str, dict[str, set[str]]] = {}
    child_families: dict[str, set[str]] = defaultdict(set)
    spouse_families: dict[str, set[str]] = defaultdict(set)
    for record in records:
        if record.tag != "FAM" or not record.pointer:
            continue
        roles: dict[str, set[str]] = {"parents": set(), "children": set()}
        for block in _top_level_blocks(record.lines):
            first = parse_gedcom_line(block[0])
            pointers = set(XREF_RE.findall(first.value))
            if first.tag in {"HUSB", "WIFE"}:
                roles["parents"].update(pointers)
            elif first.tag == "CHIL":
                roles["children"].update(pointers)
        families[record.pointer] = roles
        for parent in roles["parents"]:
            spouse_families[parent].add(record.pointer)
        for child in roles["children"]:
            child_families[child].add(record.pointer)

    keep_people = {root_pointer}
    keep_families: set[str] = set()
    pending: deque[tuple[str, int]] = deque([(root_pointer, 0)])
    while pending:
        pointer, depth = pending.popleft()
        if generations is not None and depth >= generations:
            continue
        family_ids = (
            child_families.get(pointer, set())
            if scope == "ancestors"
            else spouse_families.get(pointer, set())
        )
        for family_id in family_ids:
            keep_families.add(family_id)
            roles = families[family_id]
            if scope == "ancestors":
                next_people = roles["parents"]
            else:
                keep_people.update(roles["parents"])
                next_people = roles["children"]
            for related in next_people:
                if related not in keep_people:
                    keep_people.add(related)
                    pending.append((related, depth + 1))
    return keep_people, keep_families


__all__ = ["connected_tree_pointers", "resolve_root_person", "scoped_tree_pointers"]
