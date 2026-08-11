"""Reusable, deterministic RootsMagic-to-GEDCOM mapping."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ancestryllm.core.cancellation import cancellation_checkpoint
from ancestryllm.core.errors import AncestryError, FileIngressError
from ancestryllm.core.ingress import FileKind
from ancestryllm.gedcom.model import GedcomDocument
from ancestryllm.gedcom.serializer import wrap_long_gedcom_lines
from ancestryllm.rootsmagic.core import (
    RootsMagicReader,
    RootsMagicSchemaAdapter,
    SourceFingerprint,
    semantic_row_key,
    semantic_value,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _value(row: dict[str, Any], *names: str, default: Any = "") -> Any:
    lowered = {key.casefold(): value for key, value in row.items()}
    for name in names:
        if (
            name.casefold() in lowered
            and lowered[name.casefold()] is not None
            and lowered[name.casefold()] != ""
        ):
            return lowered[name.casefold()]
    return default


def _clean_text(value: Any) -> str:
    if isinstance(value, bytes):
        return ""
    return " ".join(str(value).replace("\r", " ").replace("\n", " ").split())


def _identifier(row: dict[str, Any], *names: str) -> str:
    for name in names:
        value = _value(row, name, default="")
        if isinstance(value, float) and value.is_integer():
            result = str(int(value))
        else:
            result = _clean_text(value)
        if result.casefold() not in {"", "0", "none"}:
            return result
    return ""


def _truthy(value: Any) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes"}


def _tag_name(column: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]", "_", column).upper()
    return ("_RM_" + clean)[:31]


def _text_lines(level: int, tag: str, value: Any) -> list[str]:
    """Preserve logical newlines with CONT while normalizing each text line."""

    if isinstance(value, bytes) or value is None:
        return []
    logical = str(value).replace("\r\n", "\n").replace("\r", "\n")
    parts = [" ".join(part.split()) for part in logical.split("\n")]
    if not any(parts):
        return []
    first = f"{level} {tag}" + (f" {parts[0]}" if parts[0] else "")
    result = [first]
    result.extend(f"{level + 1} CONT" + (f" {part}" if part else "") for part in parts[1:])
    return result


_ALIAS_GROUPS: dict[str, tuple[tuple[str, ...], ...]] = {
    "name": (
        ("Given", "GivenName"),
        ("Surname", "LastName"),
        ("IsPrimary", "PrimaryName"),
    ),
    "place": (("Name", "PlaceName"),),
    "event": (
        ("EventType", "Type"),
        ("Date", "EventDate"),
        ("Detail", "Description", "Note"),
    ),
    "note": (("Text", "Note"),),
    "source": (("Title", "Name"), ("Text", "Detail")),
    "citation": (("Detail", "Text"),),
    "media": (("File", "Filename", "Path"), ("Caption", "Title")),
}


def _retained_alias_values(
    row: dict[str, Any],
    alias_groups: tuple[tuple[str, ...], ...],
) -> list[tuple[str, Any]]:
    """Return non-selected, distinct populated aliases in stable order."""

    by_folded = {column.casefold(): (column, value) for column, value in row.items()}
    retained: list[tuple[str, Any]] = []
    for group in alias_groups:
        populated: list[tuple[str, Any, str]] = []
        for alias in group:
            item = by_folded.get(alias.casefold())
            if item is None or item[1] is None or item[1] == "" or isinstance(item[1], bytes):
                continue
            cleaned = _clean_text(item[1])
            if cleaned:
                populated.append((item[0], item[1], cleaned))
        if not populated:
            continue
        seen = {populated[0][2]}
        for column, raw, cleaned in populated[1:]:
            if cleaned in seen:
                continue
            seen.add(cleaned)
            retained.append((column, raw))
    return retained


def _extension_lines(
    row: dict[str, Any],
    known_columns: frozenset[str],
    *,
    level: int,
    alias_groups: tuple[tuple[str, ...], ...] = (),
) -> list[str]:
    """Return privacy-safe scalar extensions in deterministic column order."""

    result: list[str] = []
    for column, raw in _retained_alias_values(row, alias_groups):
        result.extend(_text_lines(level, _tag_name(column), raw))
    for column, raw in sorted(
        row.items(),
        key=lambda item: (item[0].casefold(), unicodedata.normalize("NFC", item[0])),
    ):
        cancellation_checkpoint()
        if column.casefold() in known_columns or raw is None or raw == "" or isinstance(raw, bytes):
            continue
        result.extend(_text_lines(level, _tag_name(column), raw))
    return result


_KNOWN_COLUMNS: dict[str, frozenset[str]] = {
    "person": frozenset({"personid", "id", "sex", "gender", "living", "isliving"}),
    "name": frozenset(
        {
            "nameid",
            "id",
            "ownerid",
            "personid",
            "given",
            "givenname",
            "surname",
            "lastname",
            "isprimary",
            "primaryname",
        }
    ),
    "family": frozenset(
        {
            "familyid",
            "id",
            "fatherid",
            "husbandid",
            "motherid",
            "wifeid",
        }
    ),
    "child": frozenset({"familyid", "childid", "personid"}),
    "place": frozenset({"placeid", "id", "name", "placename"}),
    "event": frozenset(
        {
            "eventid",
            "id",
            "ownerid",
            "personid",
            "familyid",
            "eventtype",
            "type",
            "date",
            "eventdate",
            "placeid",
            "place",
            "detail",
            "description",
            "note",
        }
    ),
    "fact_type": frozenset(
        {
            "facttypeid",
            "eventtypeid",
            "typeid",
            "id",
            "name",
            "eventname",
            "gedcomtag",
            "gedcom",
            "tag",
        }
    ),
    "note": frozenset({"noteid", "id", "ownerid", "personid", "familyid", "text", "note"}),
    "source": frozenset(
        {
            "sourceid",
            "id",
            "ownerid",
            "personid",
            "familyid",
            "title",
            "name",
            "text",
            "detail",
        }
    ),
    "citation": frozenset(
        {
            "citationid",
            "id",
            "ownerid",
            "personid",
            "familyid",
            "sourceid",
            "page",
            "detail",
            "text",
        }
    ),
    "media": frozenset(
        {
            "mediaid",
            "id",
            "ownerid",
            "personid",
            "familyid",
            "file",
            "filename",
            "path",
            "caption",
            "title",
        }
    ),
}

_EVENT_TAGS = {
    "birth": "BIRT",
    "death": "DEAT",
    "burial": "BURI",
    "christening": "CHR",
    "baptism": "BAPM",
    "marriage": "MARR",
    "divorce": "DIV",
    "residence": "RESI",
    "census": "CENS",
    "occupation": "OCCU",
}


def _event_lines(
    row: dict[str, Any],
    place_names: dict[str, tuple[str, ...]],
    event_types: dict[str, tuple[str, str]],
) -> list[str]:
    event_type = _clean_text(_value(row, "EventType", "Type"))
    metadata_tag, metadata_name = event_types.get(event_type, ("", ""))
    resolved_name = metadata_name or event_type
    tag = metadata_tag or _EVENT_TAGS.get(resolved_name.casefold(), "EVEN")
    result = [f"1 {tag}"]
    if tag == "EVEN" and resolved_name:
        result.extend(_text_lines(2, "TYPE", resolved_name))
    date = _clean_text(_value(row, "Date", "EventDate"))
    if date:
        result.append(f"2 DATE {date}")
    place_id = _identifier(row, "PlaceID")
    places = place_names.get(place_id, ())
    if not places:
        inline_place = _clean_text(_value(row, "Place"))
        places = (inline_place,) if inline_place else ()
    for place in places:
        result.extend(_text_lines(2, "PLAC", place))
    result.extend(_text_lines(2, "NOTE", _value(row, "Detail", "Description", "Note")))
    return result


@dataclass(slots=True)
class ExportReport:
    profile: str
    destination: str
    people_read: int
    people_written: int
    families_written: int
    living_omitted: int
    mapped_tables: list[str] = field(default_factory=list)
    unmapped_tables: list[str] = field(default_factory=list)
    unmapped_columns: dict[str, list[str]] = field(default_factory=dict)

    def markdown(
        self,
        source: Path,
        output: Path,
        *,
        omitted_records: dict[str, int] | None = None,
        sqlite_snapshot: str,
    ) -> str:
        lines = [
            "# RootsMagic GEDCOM Export Report",
            "",
            f"- Source: `{source.name}`",
            f"- SQLite snapshot: `{sqlite_snapshot}`",
            f"- Output: `{output.name}`",
            f"- Profile: `{self.profile}`",
            f"- Destination check: `{self.destination}`",
            f"- People read/written: {self.people_read}/{self.people_written}",
            f"- Families written: {self.families_written}",
            f"- Living people omitted: {self.living_omitted}",
            "",
            "## Mapped tables",
            "",
        ]
        lines.extend(f"- `{name}`" for name in self.mapped_tables or ["None"])
        lines.extend(["", "## Unmapped data", ""])
        lines.extend(
            f"- Table `{name}` — count: 1 table; reason: unsupported schema table; "
            "private values omitted."
            for name in self.unmapped_tables
        )
        for table, columns in sorted(self.unmapped_columns.items()):
            lines.append(
                f"- `{table}` columns: "
                + ", ".join(f"`{item}`" for item in columns)
                + f" — count: {len(columns)} column(s); "
                "reason: unsupported or unsafe mapping"
            )
        for table, count in sorted((omitted_records or {}).items()):
            lines.append(
                f"- `{table}` records — count: {count} record(s); "
                "reason: ownership, privacy, or selected scope cannot be safely represented"
            )
        lines.extend(
            [
                "",
                "Portable exports omit unsupported fields. Preservation exports additionally retain",
                "safely attributable scalar values as `_RM_*` custom tags. Nulls are omitted;",
                "binary values and unsafe or unattached private records remain report-only.",
                "Table and column names describe loss without recording private field values.",
                "Automated checks demonstrate format compatibility only. Dated fictional-data",
                "import evidence is required before claiming destination interoperability.",
            ]
        )
        return "\n".join(lines) + "\n"


@dataclass(frozen=True, slots=True)
class RootsMagicUnmappedColumns:
    """One deterministic, value-free disclosure of unmapped columns."""

    table: str
    columns: tuple[str, ...]

    def as_mapping(self) -> dict[str, object]:
        return {"table": self.table, "columns": list(self.columns)}


@dataclass(frozen=True, slots=True)
class RootsMagicLossReport:
    """Serializable structured loss report returned by the core mapper."""

    profile: str
    destination: str
    people_read: int
    people_written: int
    families_written: int
    living_omitted: int
    mapped_tables: tuple[str, ...]
    unmapped_tables: tuple[str, ...]
    unmapped_columns: tuple[RootsMagicUnmappedColumns, ...]
    omitted_records: tuple[tuple[str, int], ...]

    def as_mapping(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "destination": self.destination,
            "people_read": self.people_read,
            "people_written": self.people_written,
            "families_written": self.families_written,
            "living_omitted": self.living_omitted,
            "mapped_tables": list(self.mapped_tables),
            "unmapped_tables": list(self.unmapped_tables),
            "unmapped_columns": [item.as_mapping() for item in self.unmapped_columns],
            "omitted_records": [list(item) for item in self.omitted_records],
        }


@dataclass(frozen=True, slots=True)
class RootsMagicGedcomDocument:
    """Serializable mapping content with an opaque, path-free source reference."""

    source_ref: str
    document: GedcomDocument
    report: RootsMagicLossReport

    @property
    def lines(self) -> tuple[str, ...]:
        """Expose mapped physical lines for compatibility with 0.4 callers."""

        return self.document.lines

    def as_mapping(self) -> dict[str, object]:
        return {
            "source_ref": self.source_ref,
            "document": {
                "version": self.document.version,
                "lines": list(self.document.lines),
            },
            "report": self.report.as_mapping(),
        }

    def to_json(self) -> str:
        """Return deterministic JSON without host filesystem objects."""

        return json.dumps(self.as_mapping(), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class _MappedRootsMagicSnapshot:
    """Private verification lease retained only by the publication boundary."""

    source_path: Path
    source_fingerprint: SourceFingerprint
    sqlite_snapshot: str
    content: RootsMagicGedcomDocument
    legacy_report: ExportReport
    omitted_records: tuple[tuple[str, int], ...]


def _opaque_source_ref(fingerprint: SourceFingerprint) -> str:
    """Identify source content without serializing paths or host stat objects."""

    digest = hashlib.sha256()
    main = getattr(fingerprint, "main", fingerprint)
    components = (
        main,
        getattr(fingerprint, "wal", None),
        getattr(fingerprint, "shm", None),
    )
    for component in components:
        if component is not None:
            digest.update(component.sha256.encode("ascii"))
            digest.update(b"\0")
    return f"rootsmagic:sha256:{digest.hexdigest()}"


def _structured_loss_report(
    report: ExportReport,
    omitted_records: dict[str, int],
) -> RootsMagicLossReport:
    """Freeze the legacy report into a deterministic transport DTO."""

    return RootsMagicLossReport(
        profile=report.profile,
        destination=report.destination,
        people_read=report.people_read,
        people_written=report.people_written,
        families_written=report.families_written,
        living_omitted=report.living_omitted,
        mapped_tables=tuple(sorted(report.mapped_tables, key=str.casefold)),
        unmapped_tables=tuple(sorted(report.unmapped_tables, key=str.casefold)),
        unmapped_columns=tuple(
            RootsMagicUnmappedColumns(
                table=table,
                columns=tuple(sorted(columns, key=str.casefold)),
            )
            for table, columns in sorted(
                report.unmapped_columns.items(),
                key=lambda item: item[0].casefold(),
            )
        ),
        omitted_records=tuple(sorted(omitted_records.items())),
    )


class RootsMagicMapper:
    def __init__(self, reader: RootsMagicReader) -> None:
        self.reader = reader

    @staticmethod
    def _source_changed(exc: FileIngressError) -> AncestryError:
        return AncestryError(
            "ROOTSMAGIC_FILE_CHANGED",
            "The RootsMagic database changed during export; outputs were discarded.",
            "Close RootsMagic and export again from a stable backup.",
            exit_code=2,
        )

    @staticmethod
    def _validate_mapping_options(
        *,
        profile: str,
        gedcom_version: str,
        destination: str,
        living: str,
    ) -> None:
        if profile not in {"portable", "preservation"}:
            raise AncestryError(
                "EXPORT_PROFILE_INVALID", "Profile must be portable or preservation."
            )
        if gedcom_version not in {"5.5.5", "5.5.1"}:
            raise AncestryError("GEDCOM_VERSION_INVALID", "GEDCOM version must be 5.5.5 or 5.5.1.")
        if destination not in {"generic", "ancestry", "geni", "myheritage"}:
            raise AncestryError("EXPORT_DESTINATION_INVALID", "Unsupported destination profile.")
        if living not in {"exclude", "redact", "include"}:
            raise AncestryError(
                "EXPORT_LIVING_INVALID", "Living policy must be exclude, redact, or include."
            )

    @staticmethod
    def _scope_people(
        root: str | None,
        scope: str,
        generations: int | None,
        families: list[dict[str, Any]],
        children: list[dict[str, Any]],
        *,
        _checkpoint: Callable[[], None] | None = None,
    ) -> set[str] | None:
        checkpoint = _checkpoint or cancellation_checkpoint
        checkpoint()
        if root is None:
            return None
        parent_to_children: dict[str, set[str]] = defaultdict(set)
        child_to_parents: dict[str, set[str]] = defaultdict(set)
        family_members: dict[str, set[str]] = defaultdict(set)
        family_parents: dict[str, set[str]] = defaultdict(set)
        for family in families:
            checkpoint()
            family_id = _identifier(family, "FamilyID", "ID")
            for parent in (
                _identifier(family, "FatherID", "HusbandID"),
                _identifier(family, "MotherID", "WifeID"),
            ):
                checkpoint()
                if parent not in {"", "0", "None"}:
                    family_parents[family_id].add(parent)
                    family_members[family_id].add(parent)
        for child in children:
            checkpoint()
            family_id = _identifier(child, "FamilyID")
            child_id = _identifier(child, "ChildID", "PersonID")
            if child_id in {"", "0", "None"}:
                continue
            family_members[family_id].add(child_id)
            for parent in family_parents.get(family_id, set()):
                checkpoint()
                parent_to_children[parent].add(child_id)
                child_to_parents[child_id].add(parent)
        if scope == "connected":
            adjacency: dict[str, set[str]] = defaultdict(set)
            for members in family_members.values():
                checkpoint()
                for member in members:
                    checkpoint()
                    adjacency[member].update(members - {member})
        elif scope == "ancestors":
            adjacency = child_to_parents
        elif scope == "descendants":
            adjacency = parent_to_children
        else:
            raise AncestryError("EXPORT_SCOPE_INVALID", f"Unknown subtree scope: {scope}")
        seen = {root}
        pending: deque[tuple[str, int]] = deque([(root, 0)])
        while pending:
            checkpoint()
            person, depth = pending.popleft()
            if generations is not None and depth >= generations:
                continue
            for related in adjacency.get(person, set()):
                checkpoint()
                if related not in seen:
                    seen.add(related)
                    pending.append((related, depth + 1))
        return seen

    def _build_export(
        self,
        adapter: RootsMagicSchemaAdapter,
        *,
        profile: str,
        gedcom_version: str,
        destination: str,
        root_person_id: str | None,
        scope: str,
        generations: int | None,
        living: str,
    ) -> tuple[list[str], ExportReport, dict[str, int]]:
        person_table = adapter.table("person")
        if person_table is None or not person_table.rows:
            raise AncestryError(
                "ROOTSMAGIC_SCHEMA_UNSUPPORTED",
                "PersonTable is missing or empty; a safe GEDCOM cannot be produced.",
            )
        person_identifier_column = next(
            (
                column
                for candidate in ("PersonID", "ID")
                for column in person_table.columns
                if column.casefold() == candidate.casefold()
            ),
            None,
        )
        person_identifier_type = person_table.declared_type("PersonID", "ID")
        if (
            person_identifier_column is None
            or person_identifier_type is None
            or "INT" not in person_identifier_type.upper()
        ):
            raise AncestryError(
                "ROOTSMAGIC_SCHEMA_UNSUPPORTED",
                "Person identifier declaration is incompatible; safe export is unavailable.",
            )
        people_rows = list(person_table.rows)
        name_rows = adapter.rows("name")
        family_rows = adapter.rows("family")
        child_rows = adapter.rows("child")
        included = self._scope_people(
            root_person_id,
            scope,
            generations,
            family_rows,
            child_rows,
        )

        people_by_id: dict[str, dict[str, Any]] = {}
        living_ids: set[str] = set()
        for row in people_rows:
            cancellation_checkpoint()
            raw_person_id = _value(row, person_identifier_column, default=None)
            if isinstance(raw_person_id, bool) or not isinstance(raw_person_id, int):
                raise AncestryError(
                    "ROOTSMAGIC_SCHEMA_UNSUPPORTED",
                    "Person identifier values are incompatible; safe export is unavailable.",
                )
            person_id = _identifier(row, "PersonID", "ID")
            if not person_id or person_id in people_by_id:
                raise AncestryError(
                    "ROOTSMAGIC_SCHEMA_UNSUPPORTED",
                    "Person identities are missing or duplicated; safe export is unavailable.",
                )
            people_by_id[person_id] = row
            if _truthy(_value(row, "Living", "IsLiving", default="0")):
                living_ids.add(person_id)

        selected_rows = [
            row
            for person_id, row in people_by_id.items()
            if (included is None or person_id in included)
            and not (living == "exclude" and person_id in living_ids)
        ]
        selected_rows.sort(key=lambda row: semantic_row_key(row, "PersonID", "ID"))
        person_map = {
            _identifier(row, "PersonID", "ID"): f"@I{index}@"
            for index, row in enumerate(selected_rows, 1)
        }

        names_by_person: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in name_rows:
            cancellation_checkpoint()
            owner = _identifier(row, "OwnerID", "PersonID")
            if owner in person_map:
                names_by_person[owner].append(row)
        for rows in names_by_person.values():
            rows.sort(
                key=lambda row: (
                    0 if _truthy(_value(row, "IsPrimary", "PrimaryName", default="0")) else 1,
                    semantic_row_key(row, "NameID", "ID"),
                )
            )

        children_by_family: dict[str, list[str]] = defaultdict(list)
        for row in adapter.rows("child"):
            cancellation_checkpoint()
            family_id = _identifier(row, "FamilyID")
            child_id = _identifier(row, "ChildID", "PersonID")
            if family_id and child_id:
                children_by_family[family_id].append(child_id)
        for child_ids in children_by_family.values():
            child_ids.sort(key=semantic_value)

        sorted_families = sorted(
            family_rows,
            key=lambda row: semantic_row_key(row, "FamilyID", "ID"),
        )
        publishable_families: list[dict[str, Any]] = []
        unsafe_family_ids: set[str] = set()
        for row in sorted_families:
            cancellation_checkpoint()
            family_id = _identifier(row, "FamilyID", "ID")
            members = {
                _identifier(row, "FatherID", "HusbandID"),
                _identifier(row, "MotherID", "WifeID"),
                *children_by_family.get(family_id, []),
            } - {"", "0", "None"}
            if living != "include" and members & living_ids:
                unsafe_family_ids.add(family_id)
                continue
            if not any(member in person_map for member in members):
                continue
            publishable_families.append(row)
        family_map = {
            _identifier(row, "FamilyID", "ID"): f"@F{index}@"
            for index, row in enumerate(publishable_families, 1)
        }

        place_values: dict[str, set[str]] = defaultdict(set)
        for row in adapter.rows("place"):
            cancellation_checkpoint()
            place_id = _identifier(row, "PlaceID", "ID")
            place_name = _clean_text(_value(row, "Name", "PlaceName"))
            if place_id and place_name:
                place_values[place_id].add(place_name)
        place_names = {
            place_id: tuple(sorted(values, key=semantic_value))
            for place_id, values in place_values.items()
        }
        event_types: dict[str, tuple[str, str]] = {}
        for row in sorted(
            adapter.rows("fact_type"),
            key=lambda item: semantic_row_key(
                item,
                "FactTypeID",
                "EventTypeID",
                "TypeID",
                "ID",
            ),
        ):
            cancellation_checkpoint()
            type_id = _identifier(row, "FactTypeID", "EventTypeID", "TypeID", "ID")
            if not type_id or type_id in event_types:
                continue
            metadata_name = _clean_text(_value(row, "Name", "EventName"))
            candidate_tag = _clean_text(_value(row, "GedcomTag", "Gedcom", "Tag")).upper()
            metadata_tag = (
                candidate_tag if re.fullmatch(r"[A-Z_][A-Z0-9_]{0,30}", candidate_tag) else ""
            )
            event_types[type_id] = (metadata_tag, metadata_name)
        event_rows = sorted(
            adapter.rows("event"),
            key=lambda row: semantic_row_key(row, "EventID", "ID"),
        )
        note_rows = sorted(
            adapter.rows("note"),
            key=lambda row: semantic_row_key(row, "NoteID", "ID"),
        )
        citation_rows = sorted(
            adapter.rows("citation"),
            key=lambda row: semantic_row_key(row, "CitationID", "ID"),
        )
        media_rows = sorted(
            adapter.rows("media"),
            key=lambda row: semantic_row_key(row, "MediaID", "ID"),
        )
        source_rows = sorted(
            adapter.rows("source"),
            key=lambda row: semantic_row_key(row, "SourceID", "ID"),
        )

        def safe_owner(row: dict[str, Any]) -> bool:
            person_id = _identifier(row, "OwnerID", "PersonID")
            family_id = _identifier(row, "FamilyID")
            if person_id:
                return person_id in person_map and not (
                    living != "include" and person_id in living_ids
                )
            if family_id:
                return family_id in family_map and family_id not in unsafe_family_ids
            return False

        unsafe_source_ids = {
            _identifier(row, "SourceID") for row in citation_rows if not safe_owner(row)
        }
        for row in source_rows:
            owner = _identifier(row, "OwnerID", "PersonID")
            family_id = _identifier(row, "FamilyID")
            if (owner and not safe_owner(row)) or family_id in unsafe_family_ids:
                unsafe_source_ids.add(_identifier(row, "SourceID", "ID"))
        referenced_safe_sources = {
            _identifier(row, "SourceID") for row in citation_rows if safe_owner(row)
        }
        publishable_sources = [
            row
            for row in source_rows
            if (source_id := _identifier(row, "SourceID", "ID"))
            and source_id not in unsafe_source_ids
            and (safe_owner(row) or source_id in referenced_safe_sources)
        ]
        source_map = {
            _identifier(row, "SourceID", "ID"): f"@S{index}@"
            for index, row in enumerate(publishable_sources, 1)
        }
        sources_by_person: dict[str, list[str]] = defaultdict(list)
        sources_by_family: dict[str, list[str]] = defaultdict(list)
        for row in publishable_sources:
            cancellation_checkpoint()
            source_id = _identifier(row, "SourceID", "ID")
            if source_id in referenced_safe_sources:
                continue
            person_id = _identifier(row, "OwnerID", "PersonID")
            family_id = _identifier(row, "FamilyID")
            if person_id and safe_owner(row):
                sources_by_person[person_id].append(source_id)
            elif family_id and safe_owner(row):
                sources_by_family[family_id].append(source_id)

        events_by_person: dict[str, list[dict[str, Any]]] = defaultdict(list)
        events_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
        notes_by_person: dict[str, list[dict[str, Any]]] = defaultdict(list)
        notes_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
        citations_by_person: dict[str, list[dict[str, Any]]] = defaultdict(list)
        citations_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
        media_by_person: dict[str, list[dict[str, Any]]] = defaultdict(list)
        media_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row, by_person, by_family in (
            (row, events_by_person, events_by_family) for row in event_rows
        ):
            person_id = _identifier(row, "OwnerID", "PersonID")
            family_id = _identifier(row, "FamilyID")
            if person_id and safe_owner(row):
                by_person[person_id].append(row)
            elif family_id and safe_owner(row):
                by_family[family_id].append(row)
        for rows, by_person, by_family in (
            (note_rows, notes_by_person, notes_by_family),
            (citation_rows, citations_by_person, citations_by_family),
            (media_rows, media_by_person, media_by_family),
        ):
            for row in rows:
                cancellation_checkpoint()
                person_id = _identifier(row, "OwnerID", "PersonID")
                family_id = _identifier(row, "FamilyID")
                if person_id and safe_owner(row):
                    by_person[person_id].append(row)
                elif family_id and safe_owner(row):
                    by_family[family_id].append(row)

        serialized_events = [
            *(row for rows in events_by_person.values() for row in rows),
            *(row for rows in events_by_family.values() for row in rows),
        ]
        serialized_notes = [
            row
            for rows in (*notes_by_person.values(), *notes_by_family.values())
            for row in rows
            if _clean_text(_value(row, "Text", "Note"))
        ]
        serialized_citations = [
            row
            for rows in (*citations_by_person.values(), *citations_by_family.values())
            for row in rows
            if _identifier(row, "SourceID") in source_map
        ]
        serialized_media = [
            row
            for rows in (*media_by_person.values(), *media_by_family.values())
            for row in rows
            if _clean_text(_value(row, "File", "Filename", "Path"))
            or _clean_text(_value(row, "Caption", "Title"))
        ]
        used_place_ids = {
            _identifier(row, "PlaceID") for row in serialized_events if _identifier(row, "PlaceID")
        }
        serialized_counts = {
            "person": len(selected_rows),
            "name": sum(
                len(rows)
                for person_id, rows in names_by_person.items()
                if not (living == "redact" and person_id in living_ids)
            ),
            "family": len(publishable_families),
            "child": sum(
                1
                for row in adapter.rows("child")
                if _identifier(row, "FamilyID") in family_map
                and _identifier(row, "ChildID", "PersonID") in person_map
            ),
            "place": sum(
                1
                for row in adapter.rows("place")
                if _identifier(row, "PlaceID", "ID") in used_place_ids
            ),
            "event": len(serialized_events),
            "note": len(serialized_notes),
            "source": len(publishable_sources),
            "citation": len(serialized_citations),
            "media": len(serialized_media),
            "fact_type": sum(
                1
                for row in adapter.rows("fact_type")
                if _identifier(row, "FactTypeID", "EventTypeID", "TypeID", "ID")
                in {_clean_text(_value(event, "EventType", "Type")) for event in serialized_events}
            ),
        }
        omitted_records: dict[str, int] = {}
        for logical_name, serialized_count in serialized_counts.items():
            table = adapter.table(logical_name)
            if table is None:
                continue
            omitted_count = len(table.rows) - serialized_count
            if omitted_count > 0:
                omitted_records[table.actual_name] = omitted_count

        lines = [
            "0 HEAD",
            "1 SOUR AncestryLLM",
            "2 VERS 0.2.0",
            "1 GEDC",
            f"2 VERS {gedcom_version}",
            "2 FORM LINEAGE-LINKED",
            "1 CHAR UTF-8",
            "1 SUBM @U1@",
            "0 @U1@ SUBM",
            "1 NAME AncestryLLM Local Export",
        ]

        def append_owned_payload(
            owner_id: str,
            event_index: dict[str, list[dict[str, Any]]],
            note_index: dict[str, list[dict[str, Any]]],
            citation_index: dict[str, list[dict[str, Any]]],
            media_index: dict[str, list[dict[str, Any]]],
            source_index: dict[str, list[str]],
        ) -> None:
            for event in event_index.get(owner_id, []):
                cancellation_checkpoint()
                lines.extend(_event_lines(event, place_names, event_types))
                if profile == "preservation":
                    lines.extend(
                        _extension_lines(
                            event,
                            _KNOWN_COLUMNS["event"],
                            level=2,
                            alias_groups=_ALIAS_GROUPS["event"],
                        )
                    )
            for note in note_index.get(owner_id, []):
                note_lines = _text_lines(1, "NOTE", _value(note, "Text", "Note"))
                if note_lines:
                    lines.extend(note_lines)
                    if profile == "preservation":
                        lines.extend(
                            _extension_lines(
                                note,
                                _KNOWN_COLUMNS["note"],
                                level=2,
                                alias_groups=_ALIAS_GROUPS["note"],
                            )
                        )
            for citation in citation_index.get(owner_id, []):
                source_id = _identifier(citation, "SourceID")
                if source_id not in source_map:
                    continue
                lines.append(f"1 SOUR {source_map[source_id]}")
                lines.extend(_text_lines(2, "PAGE", _value(citation, "Page")))
                lines.extend(_text_lines(2, "DATA", _value(citation, "Detail", "Text")))
                if profile == "preservation":
                    lines.extend(
                        _extension_lines(
                            citation,
                            _KNOWN_COLUMNS["citation"],
                            level=2,
                            alias_groups=_ALIAS_GROUPS["citation"],
                        )
                    )
            for media in media_index.get(owner_id, []):
                filename = _clean_text(_value(media, "File", "Filename", "Path"))
                caption = _clean_text(_value(media, "Caption", "Title"))
                if not filename and not caption:
                    continue
                lines.append("1 OBJE")
                lines.extend(_text_lines(2, "FILE", _value(media, "File", "Filename", "Path")))
                lines.extend(_text_lines(2, "TITL", _value(media, "Caption", "Title")))
                if profile == "preservation":
                    lines.extend(
                        _extension_lines(
                            media,
                            _KNOWN_COLUMNS["media"],
                            level=2,
                            alias_groups=_ALIAS_GROUPS["media"],
                        )
                    )
            for source_id in source_index.get(owner_id, []):
                lines.append(f"1 SOUR {source_map[source_id]}")

        unmapped_columns: dict[str, list[str]] = {}
        for logical_name in _KNOWN_COLUMNS:
            table = adapter.table(logical_name)
            if table is None:
                continue
            unsupported = {
                column
                for column in table.columns
                if column.casefold() not in _KNOWN_COLUMNS[logical_name]
            }
            for row in table.rows:
                cancellation_checkpoint()
                unsupported.update(
                    column for column, value in row.items() if isinstance(value, bytes)
                )
                if profile == "portable":
                    unsupported.update(
                        column
                        for column, _value_to_retain in _retained_alias_values(
                            row,
                            _ALIAS_GROUPS.get(logical_name, ()),
                        )
                    )
            if unsupported:
                unmapped_columns[table.actual_name] = sorted(unsupported, key=str.casefold)

        for row in selected_rows:
            cancellation_checkpoint()
            person_id = _identifier(row, "PersonID", "ID")
            lines.append(f"0 {person_map[person_id]} INDI")
            if person_id in living_ids and living == "redact":
                lines.append("1 NAME Living /Private/")
                continue
            names = names_by_person.get(person_id, [])
            if not names:
                lines.append("1 NAME Unknown")
            for name in names:
                given = _clean_text(_value(name, "Given", "GivenName"))
                surname = _clean_text(_value(name, "Surname", "LastName"))
                lines.append(
                    f"1 NAME {given} /{surname}/" if surname else f"1 NAME {given or 'Unknown'}"
                )
                if profile == "preservation":
                    lines.extend(
                        _extension_lines(
                            name,
                            _KNOWN_COLUMNS["name"],
                            level=2,
                            alias_groups=_ALIAS_GROUPS["name"],
                        )
                    )
            raw_sex = _clean_text(_value(row, "Sex", "Gender")).upper()
            sex = {"0": "M", "1": "F", "2": "U"}.get(raw_sex, raw_sex[:1])
            if sex in {"M", "F", "U", "X"}:
                lines.append(f"1 SEX {sex}")
            if profile == "preservation":
                lines.extend(_extension_lines(row, _KNOWN_COLUMNS["person"], level=1))
            append_owned_payload(
                person_id,
                events_by_person,
                notes_by_person,
                citations_by_person,
                media_by_person,
                sources_by_person,
            )

        for row in publishable_families:
            cancellation_checkpoint()
            family_id = _identifier(row, "FamilyID", "ID")
            lines.append(f"0 {family_map[family_id]} FAM")
            father = _identifier(row, "FatherID", "HusbandID")
            mother = _identifier(row, "MotherID", "WifeID")
            if father in person_map:
                lines.append(f"1 HUSB {person_map[father]}")
            if mother in person_map:
                lines.append(f"1 WIFE {person_map[mother]}")
            lines.extend(
                f"1 CHIL {person_map[child_id]}"
                for child_id in children_by_family.get(family_id, [])
                if child_id in person_map
            )
            if profile == "preservation":
                lines.extend(_extension_lines(row, _KNOWN_COLUMNS["family"], level=1))
            append_owned_payload(
                family_id,
                events_by_family,
                notes_by_family,
                citations_by_family,
                media_by_family,
                sources_by_family,
            )

        for row in publishable_sources:
            cancellation_checkpoint()
            source_id = _identifier(row, "SourceID", "ID")
            lines.append(f"0 {source_map[source_id]} SOUR")
            lines.extend(_text_lines(1, "TITL", _value(row, "Title", "Name")))
            lines.extend(_text_lines(1, "TEXT", _value(row, "Text", "Detail")))
            if profile == "preservation":
                lines.extend(
                    _extension_lines(
                        row,
                        _KNOWN_COLUMNS["source"],
                        level=1,
                        alias_groups=_ALIAS_GROUPS["source"],
                    )
                )
        lines.append("0 TRLR")
        lines = wrap_long_gedcom_lines(
            lines,
            checkpoint=cancellation_checkpoint,
        )

        report = ExportReport(
            profile=profile,
            destination=destination,
            people_read=len(people_rows),
            people_written=len(selected_rows),
            families_written=len(publishable_families),
            living_omitted=(
                len(living_ids if included is None else living_ids & included)
                if living == "exclude"
                else 0
            ),
            mapped_tables=adapter.mapped_tables,
            unmapped_tables=adapter.unmapped_tables,
            unmapped_columns=unmapped_columns,
        )
        return lines, report, omitted_records

    def _map_snapshot(
        self,
        tree: Path,
        *,
        profile: str = "portable",
        gedcom_version: str = "5.5.5",
        destination: str = "generic",
        root_person_id: str | None = None,
        scope: str = "connected",
        generations: int | None = None,
        living: str = "exclude",
    ) -> _MappedRootsMagicSnapshot:
        """Map one stable source while retaining a private publication lease."""

        self._validate_mapping_options(
            profile=profile,
            gedcom_version=gedcom_version,
            destination=destination,
            living=living,
        )
        source_tree = self.reader.ingress.normalize_path(
            tree,
            FileKind.ROOTSMAGIC,
            absolute=True,
        )
        try:
            fingerprint = self.reader.fingerprint_source(source_tree)
            sqlite_snapshot = self.reader.snapshot_provenance(fingerprint)
            with self.reader.operation(source_tree, fingerprint) as schema:
                adapter = RootsMagicSchemaAdapter(
                    self.reader,
                    source_tree,
                    schema,
                )
                lines, report, omitted_records = self._build_export(
                    adapter,
                    profile=profile,
                    gedcom_version=gedcom_version,
                    destination=destination,
                    root_person_id=root_person_id,
                    scope=scope,
                    generations=generations,
                    living=living,
                )
        except FileIngressError as exc:
            if exc.code != "FILE_INPUT_CHANGED":
                raise
            raise self._source_changed(exc) from exc
        content = RootsMagicGedcomDocument(
            source_ref=_opaque_source_ref(fingerprint),
            document=GedcomDocument(
                version=gedcom_version,
                lines=tuple(lines),
            ),
            report=_structured_loss_report(report, omitted_records),
        )
        return _MappedRootsMagicSnapshot(
            source_path=source_tree,
            source_fingerprint=fingerprint,
            sqlite_snapshot=sqlite_snapshot,
            content=content,
            legacy_report=report,
            omitted_records=tuple(sorted(omitted_records.items())),
        )

    def map(
        self,
        tree: Path,
        *,
        profile: str = "portable",
        gedcom_version: str = "5.5.5",
        destination: str = "generic",
        root_person_id: str | None = None,
        scope: str = "connected",
        generations: int | None = None,
        living: str = "exclude",
    ) -> RootsMagicGedcomDocument:
        """Return deterministic, path-free mapped content without publication."""

        return self._map_snapshot(
            tree,
            profile=profile,
            gedcom_version=gedcom_version,
            destination=destination,
            root_person_id=root_person_id,
            scope=scope,
            generations=generations,
            living=living,
        ).content
