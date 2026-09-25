"""Fictional immutable-source tests for bounded RootsMagic browsing."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path  # noqa: TC003
from types import SimpleNamespace

import pytest

from ancestryllm.rootsmagic.core import RootsMagicReader


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """Build a small fictional tree including literal SQL wildcard characters."""
    path = tmp_path / "fictional.rmtree"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            "CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY, Sex INTEGER, Living INTEGER);"
            "INSERT INTO PersonTable VALUES (1,0,0),(2,1,0),(3,0,1);"
            "CREATE TABLE NameTable(NameID INTEGER PRIMARY KEY, OwnerID INTEGER, "
            "Given TEXT, Surname TEXT, IsPrimary INTEGER);"
            "INSERT INTO NameTable VALUES (1,1,'Alex','Example',1),"
            "(2,2,'Robin','100%_Example',1),(3,3,'Morgan','Example',1);"
        )
    return path


def test_reader_binds_values_and_pages_without_changing_source(source: Path) -> None:
    reader = RootsMagicReader([source.parent])
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    result = reader.query(
        source,
        "SELECT PersonID FROM PersonTable WHERE PersonID > ? ORDER BY PersonID OFFSET 0",
        parameters=(1,),
        row_limit=1,
    )
    assert result.rows == ((2,),)
    assert result.truncated
    assert result.truncation.row_limit == 1
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("limit", [0, -1, 101, True])
def test_reader_rejects_invalid_page_bounds(source: Path, limit: int) -> None:
    reader = RootsMagicReader([source.parent])
    with pytest.raises(ValueError, match="row limit"):
        reader.query(source, "SELECT PersonID FROM PersonTable", row_limit=limit)


def test_reader_bound_injection_is_only_a_value(source: Path) -> None:
    reader = RootsMagicReader([source.parent])
    result = reader.query(
        source,
        "SELECT PersonID FROM PersonTable WHERE PersonID = ?",
        parameters=("1 OR 1=1",),
    )
    assert result.rows == ()


def test_people_literal_filter_and_stable_pages(source: Path) -> None:
    from ancestryllm.application._rootsmagic_presets import RootsMagicPresetService
    from ancestryllm.application.operations import RootsMagicPresetQueryRequest

    service = RootsMagicPresetService(RootsMagicReader([source.parent]))
    request = RootsMagicPresetQueryRequest("opaque", "people", None, "%_", 0, 2)
    result = service.query(source, request)
    assert result.rows[0].values == (2, "Robin 100%_Example", "F", False)
    assert result.returned_rows == 1
    assert result.total_rows is None
    assert not result.has_more
    first = service.query(source, RootsMagicPresetQueryRequest("opaque", "people", None, "", 0, 2))
    second = service.query(source, RootsMagicPresetQueryRequest("opaque", "people", None, "", 2, 2))
    assert [row.values[0] for row in first.rows + second.rows] == [1, 2, 3]
    assert first.next_offset == 2
    assert second.next_offset is None


def test_people_preserves_missing_names_and_nullable_optional_columns(tmp_path: Path) -> None:
    from ancestryllm.application._rootsmagic_presets import RootsMagicPresetService
    from ancestryllm.application.operations import RootsMagicPresetQueryRequest

    path = tmp_path / "optional-columns.rmtree"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            "CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY);"
            "INSERT INTO PersonTable VALUES (1),(2);"
            "CREATE TABLE NameTable(NameID INTEGER PRIMARY KEY, OwnerID INTEGER, "
            "Given TEXT, Surname TEXT);"
            "INSERT INTO NameTable VALUES (1,1,'Only','Name');"
        )
    service = RootsMagicPresetService(RootsMagicReader([path.parent]))
    result = service.query(path, RootsMagicPresetQueryRequest("opaque", "people", None, "", 0, 20))
    assert [row.values for row in result.rows] == [
        (1, "Only Name", None, None),
        (2, None, None, None),
    ]


def test_people_handles_absent_name_table_without_losing_people(tmp_path: Path) -> None:
    from ancestryllm.application._rootsmagic_presets import RootsMagicPresetService
    from ancestryllm.application.operations import RootsMagicPresetQueryRequest

    path = tmp_path / "no-names.rmtree"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            "CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY, Sex INTEGER, Living INTEGER);"
            "INSERT INTO PersonTable VALUES (1,0,0);"
        )
    service = RootsMagicPresetService(RootsMagicReader([path.parent]))
    result = service.query(path, RootsMagicPresetQueryRequest("opaque", "people", None, "", 0, 20))
    assert [row.values for row in result.rows] == [(1, None, "M", False)]


def test_people_limits_display_payload_and_excludes_blob_names(tmp_path: Path) -> None:
    from ancestryllm.application._rootsmagic_presets import RootsMagicPresetService
    from ancestryllm.application.operations import RootsMagicPresetQueryRequest

    path = tmp_path / "bounded-names.rmtree"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            "CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY);"
            "INSERT INTO PersonTable VALUES (1),(2);"
            "CREATE TABLE NameTable(NameID INTEGER PRIMARY KEY, OwnerID INTEGER, "
            "Given TEXT, Surname TEXT, IsPrimary INTEGER);"
        )
        connection.execute("INSERT INTO NameTable VALUES (1,1,?,?,1)", ("A" * 800, "Example"))
        connection.execute(
            "INSERT INTO NameTable VALUES (2,2,?,?,1)", (sqlite3.Binary(b"not text"), "Safe")
        )
        connection.commit()

    service = RootsMagicPresetService(RootsMagicReader([path.parent]))
    result = service.query(path, RootsMagicPresetQueryRequest("opaque", "people", None, "", 0, 20))
    assert len(result.rows[0].values[1]) == 512
    assert result.rows[1].values[1] == "Safe"


def test_capability_validation_rejects_malformed_schema_metadata(
    source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ancestryllm.application._rootsmagic_presets import RootsMagicPresetService
    from ancestryllm.core.errors import AncestryError

    reader = RootsMagicReader([source.parent])
    monkeypatch.setattr(reader, "inspect_schema", lambda path: SimpleNamespace(tables=(object(),)))
    service = RootsMagicPresetService(reader)
    with pytest.raises(AncestryError) as raised:
        service.validate_capabilities(source, "people")
    assert raised.value.code == "ROOTSMAGIC_SCHEMA_UNSUPPORTED"


@pytest.mark.parametrize(
    "code", ["ROOTSMAGIC_QUERY_TIMEOUT", "FILE_INPUT_CHANGED", "ROOTSMAGIC_INPUT_INVALID"]
)
def test_schema_inspection_preserves_operational_errors(
    source: Path, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    from ancestryllm.application._rootsmagic_presets import RootsMagicPresetService
    from ancestryllm.core.errors import AncestryError

    reader = RootsMagicReader([source.parent])
    failure = AncestryError(code, "The source inspection could not complete.")

    def fail_inspection(path: Path) -> None:
        raise failure

    monkeypatch.setattr(reader, "inspect_schema", fail_inspection)
    with pytest.raises(AncestryError) as raised:
        RootsMagicPresetService(reader).validate_capabilities(source, "people")
    assert raised.value is failure


def test_presets_reject_untrusted_query_ids_and_unknown_fields(source: Path) -> None:
    from ancestryllm.application._rootsmagic_presets import RootsMagicPresetService
    from ancestryllm.application.operations import RootsMagicPresetQueryRequest
    from ancestryllm.core.errors import AncestryError

    with pytest.raises((ValueError, TypeError)):
        RootsMagicPresetQueryRequest.from_json(
            '{"source_ref":"opaque","query_id":"people","person_id":null,'
            '"name_filter":"","offset":0,"page_size":2,"sql":"SELECT 1"}'
        )
    service = RootsMagicPresetService(RootsMagicReader([source.parent]))
    with pytest.raises(AncestryError, match="preset"):
        service.query(source, RootsMagicPresetQueryRequest("opaque", "SELECT 1", None, "", 0, 2))


def test_family_links_and_events_are_person_rooted(source: Path) -> None:
    from ancestryllm.application._rootsmagic_presets import RootsMagicPresetService
    from ancestryllm.application.operations import RootsMagicPresetQueryRequest

    with closing(sqlite3.connect(source)) as connection:
        connection.executescript(
            "CREATE TABLE FamilyTable(FamilyID INTEGER PRIMARY KEY, FatherID INTEGER, MotherID INTEGER);"
            "INSERT INTO FamilyTable VALUES (1,1,2),(2,4,5);"
            "CREATE TABLE ChildTable(RecID INTEGER PRIMARY KEY, FamilyID INTEGER, ChildID INTEGER);"
            "INSERT INTO ChildTable VALUES (1,1,3),(2,2,1),(3,2,6);"
            "INSERT INTO PersonTable VALUES (4,0,0),(5,1,0),(6,1,0);"
            "INSERT INTO NameTable VALUES (4,4,'Parent','One',1),(5,5,'Parent','Two',1),"
            "(6,6,'Sibling','Example',1);"
            "CREATE TABLE EventTable(EventID INTEGER PRIMARY KEY, OwnerID INTEGER, OwnerType INTEGER, "
            "EventType INTEGER, Date TEXT, PlaceID INTEGER);"
            "INSERT INTO EventTable VALUES (1,1,0,1,'D.+19000101..+00000000..',1),"
            "(2,2,0,1,'unrelated',1),(3,1,1,1,'family event',1);"
            "CREATE TABLE FactTypeTable(FactTypeID INTEGER PRIMARY KEY, Name TEXT);"
            "INSERT INTO FactTypeTable VALUES (1,'Birth');"
            "CREATE TABLE PlaceTable(PlaceID INTEGER PRIMARY KEY, Name TEXT);"
            "INSERT INTO PlaceTable VALUES (1,'Fictional Town');"
        )
    service = RootsMagicPresetService(RootsMagicReader([source.parent]))
    result = service.query(
        source, RootsMagicPresetQueryRequest("opaque", "family_links", 1, "", 0, 20)
    )
    assert {row.values for row in result.rows} == {
        (2, "Robin 100%_Example", "spouse"),
        (3, "Morgan Example", "child"),
        (4, "Parent One", "parent"),
        (5, "Parent Two", "parent"),
        (6, "Sibling Example", "sibling"),
    }
    events = service.query(source, RootsMagicPresetQueryRequest("opaque", "events", 1, "", 0, 20))
    assert [row.values for row in events.rows] == [
        ("Birth", "D.+19000101..+00000000..", "Fictional Town")
    ]


@pytest.mark.parametrize("query_id", ["family_links", "events"])
def test_schema_capabilities_reject_missing_required_tables(source: Path, query_id: str) -> None:
    from ancestryllm.application._rootsmagic_presets import RootsMagicPresetService
    from ancestryllm.application.operations import RootsMagicPresetQueryRequest
    from ancestryllm.core.errors import AncestryError

    service = RootsMagicPresetService(RootsMagicReader([source.parent]))
    with pytest.raises(AncestryError) as raised:
        service.query(source, RootsMagicPresetQueryRequest("opaque", query_id, 1, "", 0, 20))
    assert raised.value.code == "ROOTSMAGIC_SCHEMA_UNSUPPORTED"
    assert "FamilyTable" not in raised.value.message
    assert "EventTable" not in raised.value.message


def test_definitions_describe_offset_and_page_size(source: Path) -> None:
    from ancestryllm.application._rootsmagic_presets import RootsMagicPresetService

    definition = RootsMagicPresetService.definitions()[0]
    assert [parameter.parameter_id for parameter in definition.parameters] == [
        "name_filter",
        "offset",
        "page_size",
    ]


def test_http_contract_bounds_are_enforced(source: Path) -> None:
    from ancestryllm.application._rootsmagic_presets import RootsMagicPresetService
    from ancestryllm.application.operations import RootsMagicPresetQueryRequest

    service = RootsMagicPresetService(RootsMagicReader([source.parent]))
    definition = RootsMagicPresetService.definitions()[0]
    assert {parameter.parameter_id: parameter.maximum for parameter in definition.parameters} == {
        "name_filter": 200,
        "offset": 1_000_000,
        "page_size": 100,
    }
    with pytest.raises(ValueError, match="name filter"):
        service.query(
            source, RootsMagicPresetQueryRequest("opaque", "people", None, "x" * 201, 0, 1)
        )
    with pytest.raises(ValueError, match="offset"):
        service.query(
            source, RootsMagicPresetQueryRequest("opaque", "people", None, "", 1_000_001, 1)
        )


def test_preset_page_bound_is_independent_of_cli_reader_limit(source: Path) -> None:
    from ancestryllm.application._rootsmagic_presets import RootsMagicPresetService
    from ancestryllm.application.operations import RootsMagicPresetQueryRequest

    service = RootsMagicPresetService(RootsMagicReader([source.parent], max_rows=1_000))
    with pytest.raises(ValueError, match="page size"):
        service.query(source, RootsMagicPresetQueryRequest("opaque", "people", None, "", 0, 101))


def test_person_selection_contract_preserves_large_ids_without_rounding(source: Path) -> None:
    from ancestryllm.application._rootsmagic_presets import RootsMagicPresetService
    from ancestryllm.application.operations import RootsMagicPresetQueryRequest

    with closing(sqlite3.connect(source)) as connection:
        connection.execute("INSERT INTO PersonTable VALUES (?,0,0)", (2**53 + 1,))
        connection.commit()
    service = RootsMagicPresetService(RootsMagicReader([source.parent]))
    result = service.query(
        source, RootsMagicPresetQueryRequest("opaque", "people", None, "", 0, 20)
    )
    assert result.rows[-1].values[0] == str(2**53 + 1)
    person = service.definitions()[1].parameters[0]
    assert person.maximum == 2**53 - 1


def test_large_source_pages_remain_bounded_and_immutable(tmp_path: Path) -> None:
    from ancestryllm.application._rootsmagic_presets import RootsMagicPresetService
    from ancestryllm.application.operations import RootsMagicPresetQueryRequest

    path = tmp_path / "large-fictional.rmtree"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            "CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY);"
            "CREATE TABLE NameTable(OwnerID INTEGER PRIMARY KEY, Given TEXT, Surname TEXT);"
        )
        connection.executemany(
            "INSERT INTO PersonTable VALUES (?)", ((i,) for i in range(1, 25_001))
        )
        connection.executemany(
            "INSERT INTO NameTable VALUES (?, 'Fictional', 'Person')",
            ((i,) for i in range(1, 25_001)),
        )
        connection.commit()
    original = path.read_bytes()
    service = RootsMagicPresetService(RootsMagicReader([tmp_path], timeout_seconds=2.0))
    result = service.query(
        path, RootsMagicPresetQueryRequest("opaque", "people", None, "", 10_000, 25)
    )
    assert [row.values[0] for row in result.rows] == list(range(10_001, 10_026))
    assert result.has_more and result.next_offset == 10_025
    assert result.total_rows is None
    assert path.read_bytes() == original


def test_preset_sql_execution_obeys_reader_deadline(source: Path) -> None:
    from ancestryllm.application._rootsmagic_presets import RootsMagicPresetService
    from ancestryllm.application.operations import RootsMagicPresetQueryRequest
    from ancestryllm.core.errors import AncestryError

    with closing(sqlite3.connect(source)) as connection:
        connection.executemany(
            "INSERT INTO PersonTable VALUES (?,0,0)", ((i,) for i in range(4, 10_004))
        )
        connection.commit()
    original = source.read_bytes()
    service = RootsMagicPresetService(RootsMagicReader([source.parent], timeout_seconds=0.0))
    with pytest.raises(AncestryError) as raised:
        service.query(
            source, RootsMagicPresetQueryRequest("opaque", "people", None, "absent", 0, 25)
        )
    assert raised.value.code == "ROOTSMAGIC_QUERY_TIMEOUT"
    assert source.read_bytes() == original


def test_final_page_does_not_advertise_offset_above_limit(
    source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ancestryllm.application._rootsmagic_presets import RootsMagicPresetService
    from ancestryllm.application.operations import RootsMagicPresetQueryRequest

    reader = RootsMagicReader([source.parent])
    monkeypatch.setattr(
        reader,
        "query",
        lambda *args, **kwargs: SimpleNamespace(
            rows=((1,),),
            columns=("person_id",),
            truncated=True,
        ),
    )
    service = RootsMagicPresetService(reader)
    page = service.query(
        source, RootsMagicPresetQueryRequest("opaque", "people", None, "", 999_999, 100)
    )
    assert page.next_offset is None
    assert not page.has_more


def test_query_result_text_normalizes_control_characters(source: Path) -> None:
    from ancestryllm.application._rootsmagic_presets import RootsMagicPresetService
    from ancestryllm.application.operations import RootsMagicPresetQueryRequest

    with closing(sqlite3.connect(source)) as connection:
        connection.execute(
            "UPDATE NameTable SET Given = ? WHERE OwnerID = 1",
            ("Fictional\n\t\u202eName",),
        )
        connection.commit()
    service = RootsMagicPresetService(RootsMagicReader([source.parent]))
    page = service.query(source, RootsMagicPresetQueryRequest("opaque", "people", None, "", 0, 20))
    assert page.rows[0].values[1] == "Fictional   Name Example"
