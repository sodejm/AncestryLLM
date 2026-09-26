"""Bounded, schema-aware RootsMagic browsing presets."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast
from unicodedata import category

from ancestryllm.application.operations import (
    QueryRow,
    RootsMagicPresetQueryRequest,
    RootsMagicQueryDefinition,
    RootsMagicQueryParameterDefinition,
    RootsMagicResultPage,
)
from ancestryllm.core.errors import AncestryError

if TYPE_CHECKING:
    from pathlib import Path

    from ancestryllm.application.dto import Scalar
    from ancestryllm.rootsmagic.core import JsonScalar, RootsMagicReader, TableSchema

__all__ = ["RootsMagicPresetService"]


class RootsMagicPresetService:
    """Execute the small, allowlisted query set used by the desktop workbench."""

    _QUERIES: ClassVar[frozenset[str]] = frozenset({"people", "family_links", "events"})
    _INT64_MAX: ClassVar[int] = 2**63 - 1
    _PERSON_ID_MAX: ClassVar[int] = 2**53 - 1
    _OFFSET_LIMIT: ClassVar[int] = 1_000_000
    _NAME_LIMIT: ClassVar[int] = 200
    _PAGE_SIZE_LIMIT: ClassVar[int] = 100
    _TEXT_LIMIT: ClassVar[int] = 512

    def __init__(self, reader: RootsMagicReader) -> None:
        self.reader = reader

    @staticmethod
    def definitions() -> tuple[RootsMagicQueryDefinition, ...]:
        """Describe the fixed presets without exposing schema or SQL text."""
        paging = (
            RootsMagicQueryParameterDefinition(
                "offset", "integer", False, 0, RootsMagicPresetService._OFFSET_LIMIT, ()
            ),
            RootsMagicQueryParameterDefinition(
                "page_size", "integer", False, 1, RootsMagicPresetService._PAGE_SIZE_LIMIT, ()
            ),
        )
        person = RootsMagicQueryParameterDefinition(
            "person_id", "integer", True, 1, RootsMagicPresetService._PERSON_ID_MAX, ()
        )
        return (
            RootsMagicQueryDefinition(
                "people",
                "People",
                "Browse people by an optional literal name filter.",
                (
                    RootsMagicQueryParameterDefinition(
                        "name_filter",
                        "string",
                        False,
                        None,
                        RootsMagicPresetService._NAME_LIMIT,
                        (),
                    ),
                    *paging,
                ),
                RootsMagicPresetService._PAGE_SIZE_LIMIT,
            ),
            RootsMagicQueryDefinition(
                "family_links",
                "Family links",
                "Browse parents, spouses, children, and siblings for one person.",
                (person, *paging),
                RootsMagicPresetService._PAGE_SIZE_LIMIT,
            ),
            RootsMagicQueryDefinition(
                "events",
                "Events",
                "Browse dated events for one person.",
                (person, *paging),
                RootsMagicPresetService._PAGE_SIZE_LIMIT,
            ),
        )

    def execute(self, request: RootsMagicPresetQueryRequest) -> RootsMagicResultPage:
        """Execute a typed preset request through the application boundary."""
        return self.query(request.source_ref, request)

    def validate_capabilities(self, source: str | Path, query_id: str) -> None:
        """Raise a stable error when a source cannot support a fixed preset."""
        if query_id not in self._QUERIES:
            self._raise_invalid_preset()
        self._schema(self.reader.resolve_tree(source), query_id)

    def query(
        self, source: str | Path, request: RootsMagicPresetQueryRequest
    ) -> RootsMagicResultPage:
        """Return one deterministic page from an immutable source snapshot."""
        self._validate(request)
        path = self.reader.resolve_tree(source)
        with self.reader.operation(path):
            schema = self._schema(path, request.query_id)
            if request.query_id == "people":
                sql, parameters = self._people_statement(schema, request)
            elif request.query_id == "family_links":
                sql, parameters = self._family_statement(schema, request)
            else:
                sql, parameters = self._events_statement(schema, request)
            result = self.reader.query(
                path, sql, parameters=parameters, row_limit=request.page_size
            )
        rows = tuple(QueryRow(self._scalar_row(row)) for row in result.rows)
        next_offset = request.offset + request.page_size
        has_more = result.truncated and next_offset <= self._OFFSET_LIMIT
        return RootsMagicResultPage(
            request.query_id,
            result.columns,
            rows,
            request.offset,
            len(rows),
            None,
            has_more,
            next_offset if has_more else None,
        )

    def _people_statement(
        self, schema: dict[str, TableSchema], request: RootsMagicPresetQueryRequest
    ) -> tuple[str, tuple[JsonScalar, ...]]:
        display_name = self._name_expression(schema, "p.PersonID")
        sql = (
            "SELECT p.PersonID AS person_id, "  # noqa: S608 - fixed schema fragments only
            f"{display_name} AS display_name, "
            f"{self._sex_expression(schema)} AS sex, "
            f"{self._living_expression(schema)} AS living "
            "FROM PersonTable AS p "
            "WHERE typeof(p.PersonID) = 'integer' "
            f"AND instr(COALESCE({display_name}, ''), ?) > 0 "
            "ORDER BY p.PersonID OFFSET ?"
        )
        return sql, (request.name_filter, request.offset)

    def _family_statement(
        self, schema: dict[str, TableSchema], request: RootsMagicPresetQueryRequest
    ) -> tuple[str, tuple[JsonScalar, ...]]:
        display_name = self._name_expression(schema, "r.person_id")
        sql = (
            "SELECT r.person_id AS person_id, "  # noqa: S608 - fixed schema fragments only
            f"{display_name} AS display_name, r.relationship AS relationship "
            "FROM ("
            "SELECT f.FatherID AS person_id, 'parent' AS relationship "
            "FROM FamilyTable AS f JOIN ChildTable AS c ON c.FamilyID = f.FamilyID "
            "WHERE c.ChildID = ? AND typeof(f.FatherID) = 'integer' "
            "UNION SELECT f.MotherID, 'parent' "
            "FROM FamilyTable AS f JOIN ChildTable AS c ON c.FamilyID = f.FamilyID "
            "WHERE c.ChildID = ? AND typeof(f.MotherID) = 'integer' "
            "UNION SELECT CASE WHEN f.FatherID = ? THEN f.MotherID ELSE f.FatherID END, 'spouse' "
            "FROM FamilyTable AS f WHERE f.FatherID = ? OR f.MotherID = ? "
            "UNION SELECT c.ChildID, 'child' "
            "FROM FamilyTable AS f JOIN ChildTable AS c ON c.FamilyID = f.FamilyID "
            "WHERE f.FatherID = ? OR f.MotherID = ? "
            "UNION SELECT sibling.ChildID, 'sibling' "
            "FROM ChildTable AS selected JOIN ChildTable AS sibling "
            "ON sibling.FamilyID = selected.FamilyID "
            "WHERE selected.ChildID = ? AND sibling.ChildID != ?"
            ") AS r JOIN PersonTable AS p ON p.PersonID = r.person_id "
            "WHERE typeof(r.person_id) = 'integer' AND typeof(p.PersonID) = 'integer' "
            "ORDER BY r.person_id, r.relationship OFFSET ?"
        )
        person_id = cast("JsonScalar", request.person_id)
        return sql, (person_id,) * 9 + (request.offset,)

    def _events_statement(
        self, schema: dict[str, TableSchema], request: RootsMagicPresetQueryRequest
    ) -> tuple[str, tuple[JsonScalar, ...]]:
        event_columns = self._columns(schema["eventtable"])
        joins: list[str] = []
        if "eventtype" in event_columns and self._has(
            schema, "facttypetable", "facttypeid", "name"
        ):
            joins.append("LEFT JOIN FactTypeTable AS ft ON ft.FactTypeID = e.EventType")
            event_type = self._text_expression("ft", "Name")
        else:
            event_type = "NULL"
        if "placeid" in event_columns and self._has(schema, "placetable", "placeid", "name"):
            joins.append("LEFT JOIN PlaceTable AS pl ON pl.PlaceID = e.PlaceID")
            place = self._text_expression("pl", "Name")
        else:
            place = "NULL"
        date = self._text_expression("e", "Date") if "date" in event_columns else "NULL"
        sql = (
            f"SELECT {event_type} AS event_type, {date} AS date, {place} AS place FROM EventTable AS e "  # noqa: S608 - fixed schema fragments only
            f"{' '.join(joins)} WHERE e.OwnerID = ? AND e.OwnerType = 0 AND typeof(e.OwnerID) = 'integer' "
            "ORDER BY e.EventID OFFSET ?"
        )
        return sql, (cast("JsonScalar", request.person_id), request.offset)

    def _schema(self, path: Path, query_id: str) -> dict[str, TableSchema]:
        try:
            inspection = self.reader.inspect_schema(path)
            schema = {table.name.casefold(): table for table in inspection.tables}
        except (AttributeError, TypeError, ValueError):
            self._raise_schema_unsupported(query_id)
        if not self._has(schema, "persontable", "personid"):
            self._raise_schema_unsupported(query_id)
        if query_id == "family_links" and not (
            self._has(schema, "familytable", "familyid", "fatherid", "motherid")
            and self._has(schema, "childtable", "familyid", "childid")
        ):
            self._raise_schema_unsupported(query_id)
        if query_id == "events" and not self._has(
            schema, "eventtable", "eventid", "ownerid", "ownertype"
        ):
            self._raise_schema_unsupported(query_id)
        return schema

    @staticmethod
    def _columns(table: TableSchema) -> frozenset[str]:
        return frozenset(column.casefold() for column in table.columns)

    def _has(self, schema: dict[str, TableSchema], table_name: str, *columns: str) -> bool:
        table = schema.get(table_name.casefold())
        return table is not None and {column.casefold() for column in columns} <= self._columns(
            table
        )

    def _name_expression(self, schema: dict[str, TableSchema], owner_expression: str) -> str:
        if not self._has(schema, "nametable", "ownerid", "given", "surname"):
            return "NULL"
        primary = "AND n.IsPrimary = 1 " if self._has(schema, "nametable", "isprimary") else ""
        given = self._text_expression("n", "Given")
        surname = self._text_expression("n", "Surname")
        return (
            "(SELECT min(NULLIF(substr(trim(COALESCE("  # noqa: S608 - fixed schema fragments only
            f"{given}, '') || ' ' || COALESCE({surname}, '')), 1, {self._TEXT_LIMIT}), '')) "
            "FROM NameTable AS n WHERE typeof(n.OwnerID) = 'integer' "
            f"AND n.OwnerID = {owner_expression} {primary})"
        )

    def _sex_expression(self, schema: dict[str, TableSchema]) -> str:
        return (
            "CASE WHEN typeof(p.Sex) = 'integer' THEN CASE p.Sex WHEN 1 THEN 'F' WHEN 0 THEN 'M' END END"
            if self._has(schema, "persontable", "sex")
            else "NULL"
        )

    def _living_expression(self, schema: dict[str, TableSchema]) -> str:
        return (
            "CASE WHEN typeof(p.Living) = 'integer' THEN CASE WHEN p.Living = 1 THEN 1 ELSE 0 END END"
            if self._has(schema, "persontable", "living")
            else "NULL"
        )

    def _text_expression(self, alias: str, column: str) -> str:
        return f"CASE WHEN typeof({alias}.{column}) = 'text' THEN substr({alias}.{column}, 1, {self._TEXT_LIMIT}) END"

    @staticmethod
    def _scalar_row(row: tuple[object, ...]) -> tuple[Scalar, ...]:
        if not all(value is None or type(value) in {str, int, float, bool} for value in row):
            raise AncestryError(
                "ROOTSMAGIC_SCHEMA_UNSUPPORTED",
                "The selected RootsMagic tree does not support this browsing preset.",
                "Choose a RootsMagic tree with the required records.",
                exit_code=2,
            )
        # Preserve SQLite integer identity when serialized through JavaScript numbers.
        return cast(
            "tuple[Scalar, ...]",
            tuple(
                str(value)
                if type(value) is int and abs(value) > RootsMagicPresetService._PERSON_ID_MAX
                else "".join(
                    " " if category(character).startswith("C") else character for character in value
                )
                if type(value) is str
                else value
                for value in row
            ),
        )

    def _validate(self, request: RootsMagicPresetQueryRequest) -> None:
        if request.query_id not in self._QUERIES:
            self._raise_invalid_preset()
        if type(request.source_ref) is not str or not request.source_ref.strip():
            raise AncestryError("ROOTSMAGIC_SOURCE_REQUIRED", "A source capability is required.")
        if type(request.offset) is not int or not 0 <= request.offset <= self._OFFSET_LIMIT:
            raise ValueError("The query offset is outside the supported int64 range.")
        if type(request.page_size) is not int or not 1 <= request.page_size <= min(
            self._PAGE_SIZE_LIMIT, self.reader.max_rows
        ):
            raise ValueError("The query page size is outside the configured bound.")
        if request.offset > self._INT64_MAX - request.page_size:
            raise ValueError("The query offset and page size exceed the supported int64 range.")
        if request.query_id in {"family_links", "events"} and (
            type(request.person_id) is not int or not 1 <= request.person_id <= self._PERSON_ID_MAX
        ):
            raise AncestryError(
                "ROOTSMAGIC_PERSON_REQUIRED",
                "This preset requires a selected person.",
                "Select a person before browsing related records.",
                exit_code=2,
            )
        if (
            type(request.name_filter) is not str
            or len(request.name_filter) > self._NAME_LIMIT
            or "\x00" in request.name_filter
        ):
            raise ValueError("The name filter is outside the configured bound.")

    @staticmethod
    def _raise_invalid_preset() -> None:
        raise AncestryError(
            "ROOTSMAGIC_PRESET_INVALID",
            "The requested RootsMagic preset is not supported.",
            "Choose People, Family links, or Events.",
            exit_code=2,
        )

    @staticmethod
    def _raise_schema_unsupported(query_id: str) -> None:
        raise AncestryError(
            "ROOTSMAGIC_SCHEMA_UNSUPPORTED",
            "The selected RootsMagic tree does not support this browsing preset.",
            "Choose a RootsMagic tree with the required records.",
            exit_code=2,
            details={"query_id": query_id},
        )
