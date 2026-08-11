"""Feature-detected RootsMagic schema aliases used by the export boundary."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ancestryllm.core.cancellation import cancellation_checkpoint

if TYPE_CHECKING:
    from pathlib import Path

    from ancestryllm.rootsmagic.source import RootsMagicReader

TABLE_ALIASES: dict[str, tuple[str, ...]] = {
    "person": ("PersonTable", "PeopleTable", "Person", "People"),
    "name": ("NameTable", "NamesTable", "Name", "Names"),
    "family": ("FamilyTable", "FamiliesTable", "Family", "Families"),
    "child": ("ChildTable", "ChildrenTable", "Child", "Children"),
    "place": ("PlaceTable", "PlacesTable", "Place", "Places"),
    "event": ("EventTable", "EventsTable", "Event", "Events"),
    "fact_type": (
        "FactTypeTable",
        "FactTypesTable",
        "EventTypeTable",
        "FactType",
        "EventType",
    ),
    "note": ("NoteTable", "NotesTable", "Note", "Notes"),
    "source": ("SourceTable", "SourcesTable", "Source", "Sources"),
    "citation": ("CitationTable", "CitationsTable", "Citation", "Citations"),
    "media": ("MediaTable", "MultimediaTable", "Media", "Multimedia"),
}


def _normalized_text(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value))


def semantic_value(value: Any) -> tuple[int, int | str, str]:
    """Return a stable natural-order key without exposing a source value."""

    if value is None:
        return (0, "", "")
    if isinstance(value, bool):
        return (1, int(value), "")
    if isinstance(value, int):
        return (1, value, "")
    if isinstance(value, float) and value.is_integer():
        return (1, int(value), "")
    if isinstance(value, bytes):
        return (3, "", "")
    text = _normalized_text(value).strip()
    if re.fullmatch(r"[+-]?\d+", text):
        return (1, int(text), text)
    return (2, text.casefold(), text)


def semantic_row_key(
    row: dict[str, Any],
    *identity_columns: str,
) -> tuple[tuple[int, int | str, str], ...]:
    lowered = {column.casefold(): value for column, value in row.items()}
    identity = next(
        (lowered[column.casefold()] for column in identity_columns if column.casefold() in lowered),
        None,
    )
    remainder = tuple(
        semantic_value(value)
        for column, value in sorted(
            row.items(),
            key=lambda item: (item[0].casefold(), _normalized_text(item[0])),
        )
        if column.casefold() not in {name.casefold() for name in identity_columns}
    )
    return (semantic_value(identity), *remainder)


@dataclass(frozen=True, slots=True)
class AdaptedTable:
    logical_name: str
    actual_name: str
    columns: tuple[str, ...]
    declared_types: tuple[tuple[str, str], ...]
    rows: tuple[dict[str, Any], ...]

    def declared_type(self, *column_names: str) -> str | None:
        by_folded = {name.casefold(): value for name, value in self.declared_types}
        return next(
            (by_folded[name.casefold()] for name in column_names if name.casefold() in by_folded),
            None,
        )


class RootsMagicSchemaAdapter:
    """Resolve supported table aliases without requiring optional tables."""

    def __init__(
        self,
        reader: RootsMagicReader,
        path: Path,
        schema: dict[str, tuple[str, ...]],
    ) -> None:
        self._reader = reader
        self._path = path
        self._schema = schema
        self._tables = self._load_tables()

    def _load_tables(self) -> dict[str, AdaptedTable]:
        by_folded = {name.casefold(): name for name in self._schema}
        result: dict[str, AdaptedTable] = {}
        for logical_name, aliases in TABLE_ALIASES.items():
            cancellation_checkpoint()
            actual = next(
                (by_folded[alias.casefold()] for alias in aliases if alias.casefold() in by_folded),
                None,
            )
            if actual is None:
                continue
            rows = self._reader.read_table(self._path, actual)
            result[logical_name] = AdaptedTable(
                logical_name=logical_name,
                actual_name=actual,
                columns=self._schema[actual],
                declared_types=tuple(
                    self._reader.declared_column_types(self._path, actual).items()
                ),
                rows=tuple(rows),
            )
        return result

    def table(self, logical_name: str) -> AdaptedTable | None:
        return self._tables.get(logical_name)

    def rows(self, logical_name: str) -> list[dict[str, Any]]:
        table = self.table(logical_name)
        return list(table.rows) if table is not None else []

    @property
    def mapped_tables(self) -> list[str]:
        return [
            table.actual_name
            for logical_name in TABLE_ALIASES
            if (table := self._tables.get(logical_name)) is not None
        ]

    @property
    def unmapped_tables(self) -> list[str]:
        mapped = {table.actual_name.casefold() for table in self._tables.values()}
        return sorted(
            (name for name in self._schema if name.casefold() not in mapped),
            key=lambda name: (
                unicodedata.normalize("NFC", name).casefold(),
                unicodedata.normalize("NFC", name),
            ),
        )
