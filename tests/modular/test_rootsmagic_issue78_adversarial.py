"""Issue #78 adversarial matrix for RootsMagic SQLite inputs.

All records and canaries are fictional.  The focused tests here cover
resource/content cases that are not already exercised by the general ingress,
query-cancellation, or issue #29 export suites.
"""

from __future__ import annotations

import base64
import dataclasses
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from ancestryllm.core.errors import AncestryError, FileIngressError
from ancestryllm.core.ingress import FileIngressLimits, FileIngressPolicy
from ancestryllm.rootsmagic.exporter import RootsMagicExporter
from ancestryllm.rootsmagic.reader import RootsMagicReader


def _tree(path: Path, script: str) -> Path:
    connection = sqlite3.connect(path)
    connection.executescript(script)
    connection.commit()
    connection.close()
    return path


def _bounded_reader(
    root: Path,
    *,
    max_record_bytes: int | None = None,
    max_collection_items: int = 50_000,
) -> RootsMagicReader:
    defaults = FileIngressLimits()
    rootsmagic = dataclasses.replace(
        defaults.rootsmagic,
        max_record_bytes=max_record_bytes,
        max_collection_items=max_collection_items,
    )
    return RootsMagicReader(
        [root],
        ingress=FileIngressPolicy(dataclasses.replace(defaults, rootsmagic=rootsmagic)),
    )


def _identity(path: Path) -> tuple[bytes, int, int]:
    metadata = path.stat()
    return path.read_bytes(), metadata.st_mtime_ns, metadata.st_mode


def test_default_rootsmagic_limits_bound_each_logical_record() -> None:
    assert FileIngressLimits().rootsmagic.max_record_bytes == 16_777_216


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    (
        (b"", "FILE_FORMAT_INVALID"),
        (b"SQLite format 3\x00", "ROOTSMAGIC_INPUT_INVALID"),
        (b"SQLite format 3\x00" + (b"\x00" * 100), "ROOTSMAGIC_INPUT_INVALID"),
        (b"fictional random database bytes", "FILE_FORMAT_INVALID"),
        (b"Salted__fictional-encrypted-database", "FILE_FORMAT_INVALID"),
    ),
)
def test_invalid_database_families_fail_stably_without_artifacts_or_source_changes(
    tmp_path: Path,
    payload: bytes,
    expected_code: str,
) -> None:
    source = tmp_path / "fictional-private-corrupt.rmtree"
    source.write_bytes(payload)
    os.chmod(source, 0o600)
    before = _identity(source)
    output = tmp_path / "existing.ged"
    report = tmp_path / "existing.md"
    output.write_bytes(b"GEDCOM sentinel\n")
    report.write_bytes(b"report sentinel\n")

    with pytest.raises(AncestryError) as raised:
        RootsMagicExporter(RootsMagicReader([tmp_path])).export(
            source,
            output,
            report_path=report,
        )

    assert raised.value.code == expected_code
    assert str(source) not in raised.value.render()
    assert "fictional-private" not in raised.value.render()
    assert _identity(source) == before
    assert output.read_bytes() == b"GEDCOM sentinel\n"
    assert report.read_bytes() == b"report sentinel\n"
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_malformed_declared_schema_is_rejected_without_disclosing_schema_text(
    tmp_path: Path,
) -> None:
    source = _tree(
        tmp_path / "malformed-schema.rmtree",
        """
        CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY, PrivateCanary TEXT);
        INSERT INTO PersonTable VALUES (1, 'FICTIONAL_SCHEMA_CANARY');
        """,
    )
    connection = sqlite3.connect(source)
    connection.execute("PRAGMA writable_schema = ON")
    connection.execute(
        "UPDATE sqlite_master SET sql = ? WHERE name = 'PersonTable'",
        ("CREATE TABLE PersonTable(PRIVATE_MALFORMED",),
    )
    connection.commit()
    connection.close()
    before = _identity(source)

    with pytest.raises(AncestryError) as raised:
        RootsMagicReader([tmp_path]).schema(source)

    assert raised.value.code == "ROOTSMAGIC_INPUT_INVALID"
    assert "PRIVATE_MALFORMED" not in raised.value.render()
    assert str(source) not in raised.value.render()
    assert _identity(source) == before


def test_excessive_declared_columns_are_bounded_before_rows_are_consumed(
    tmp_path: Path,
) -> None:
    source = _tree(
        tmp_path / "many-columns.rmtree",
        """
        CREATE TABLE PersonTable(
            PersonID INTEGER PRIMARY KEY,
            First TEXT,
            Second TEXT,
            Third TEXT
        );
        INSERT INTO PersonTable VALUES (1, 'one', 'two', 'three');
        """,
    )
    before = _identity(source)

    with pytest.raises(FileIngressError) as raised:
        _bounded_reader(tmp_path, max_collection_items=3).schema(source)

    assert raised.value.code == "FILE_COLLECTION_LIMIT_EXCEEDED"
    assert _identity(source) == before


def test_excessive_declared_tables_are_bounded_incrementally(tmp_path: Path) -> None:
    source = _tree(
        tmp_path / "many-tables.rmtree",
        """
        CREATE TABLE FirstTable(Value INTEGER);
        CREATE TABLE SecondTable(Value INTEGER);
        CREATE TABLE ThirdTable(Value INTEGER);
        CREATE TABLE FourthTable(Value INTEGER);
        """,
    )
    before = _identity(source)

    with pytest.raises(FileIngressError) as raised:
        _bounded_reader(tmp_path, max_collection_items=3).schema(source)

    assert raised.value.code == "FILE_COLLECTION_LIMIT_EXCEEDED"
    assert _identity(source) == before


@pytest.mark.parametrize("value", ("FICTIONAL" * 8, b"\x01" * 64))
def test_oversized_text_and_blob_cells_use_the_logical_record_budget(
    tmp_path: Path,
    value: str | bytes,
) -> None:
    source = tmp_path / "oversized-cell.rmtree"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY, Payload)")
    connection.execute("INSERT INTO PersonTable VALUES (1, ?)", (value,))
    connection.commit()
    connection.close()
    before = _identity(source)

    with pytest.raises(FileIngressError) as raised:
        _bounded_reader(tmp_path, max_record_bytes=32).read_table(
            source,
            "PersonTable",
        )

    assert raised.value.code == "FILE_RECORD_TOO_LARGE"
    assert "FICTIONAL" not in raised.value.render()
    assert _identity(source) == before


@pytest.mark.parametrize("method", ("query", "read_table"))
def test_exact_record_budget_is_accepted_despite_sqlite_record_overhead(
    tmp_path: Path,
    method: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"\x01" * 4_096
    source = tmp_path / f"exact-budget-{method}.rmtree"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE PersonTable(Payload BLOB)")
    connection.execute("INSERT INTO PersonTable VALUES (?)", (payload,))
    connection.commit()
    connection.close()
    reader = _bounded_reader(
        tmp_path,
        max_record_bytes=len(payload),
        max_collection_items=4,
    )
    real_connect = sqlite3.connect
    configured_length_limits: list[int] = []
    probe = real_connect(":memory:")
    sqlite_column_limit = probe.getlimit(sqlite3.SQLITE_LIMIT_COLUMN)
    probe.close()

    class TrackingConnection:
        def __init__(self, wrapped: sqlite3.Connection) -> None:
            self._wrapped = wrapped

        def setlimit(self, category: int, limit: int) -> int:
            if category == sqlite3.SQLITE_LIMIT_LENGTH:
                configured_length_limits.append(limit)
            return self._wrapped.setlimit(category, limit)

        @property
        def row_factory(self) -> Any:
            return self._wrapped.row_factory

        @row_factory.setter
        def row_factory(self, value: Any) -> None:
            self._wrapped.row_factory = value

        def __getattr__(self, name: str) -> Any:
            return getattr(self._wrapped, name)

    def connect(*args: object, **kwargs: object) -> TrackingConnection:
        return TrackingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(sqlite3, "connect", connect)

    if method == "query":
        result = reader.query(source, "SELECT Payload FROM PersonTable")
        row = result.rows[0]
    else:
        row = reader.read_table(source, "PersonTable")[0]

    assert configured_length_limits == [len(payload) + 9 * (sqlite_column_limit + 1)]
    assert configured_length_limits[0] > len(payload)
    assert row == (
        (
            {
                "encoding": "base64",
                "data": base64.b64encode(payload).decode("ascii"),
            },
        )
        if method == "query"
        else {"Payload": payload}
    )


@pytest.mark.parametrize("method", ("query", "read_table"))
def test_rows_are_validated_before_the_next_sqlite_row_is_consumed(
    tmp_path: Path,
    method: str,
) -> None:
    source = _tree(
        tmp_path / f"incremental-{method}.rmtree",
        """
        CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY, Payload TEXT);
        INSERT INTO PersonTable VALUES (1, 'FIRST' || char(0) || 'PRIVATE_NUL');
        INSERT INTO PersonTable VALUES (2, CAST(X'80' AS TEXT));
        """,
    )
    before = _identity(source)
    reader = RootsMagicReader([tmp_path])

    with pytest.raises(FileIngressError) as raised:
        if method == "query":
            reader.query(source, "SELECT PersonID, Payload FROM PersonTable ORDER BY PersonID")
        else:
            reader.read_table(source, "PersonTable")

    assert raised.value.code == "FILE_NUL_BYTE_UNSUPPORTED"
    assert "PRIVATE_NUL" not in raised.value.render()
    assert _identity(source) == before


@pytest.mark.parametrize("method", ("query", "read_table"))
def test_sqlite_length_limit_rejects_large_cells_before_python_materialization(
    tmp_path: Path,
    method: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / f"sqlite-length-{method}.rmtree"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY, Payload BLOB)")
    connection.execute("INSERT INTO PersonTable VALUES (1, ?)", (b"\x01" * 65_536,))
    connection.commit()
    connection.close()
    before = _identity(source)
    reader = _bounded_reader(tmp_path, max_record_bytes=1024)
    monkeypatch.setattr(
        reader,
        "_validate_result_row",
        lambda *_args, **_kwargs: pytest.fail(
            "SQLite must reject the cell before Python row validation."
        ),
    )

    with pytest.raises(FileIngressError) as raised:
        if method == "query":
            reader.query(source, "SELECT PersonID, Payload FROM PersonTable")
        else:
            reader.read_table(source, "PersonTable")

    assert raised.value.code == "FILE_RECORD_TOO_LARGE"
    assert str(source) not in raised.value.render()
    assert _identity(source) == before


def test_embedded_nul_text_is_rejected_before_export_content_is_created(
    tmp_path: Path,
) -> None:
    source = tmp_path / "nul-text.rmtree"
    connection = sqlite3.connect(source)
    connection.execute(
        "CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY, FictionalCustom TEXT)"
    )
    connection.execute(
        "INSERT INTO PersonTable VALUES (1, ?)",
        ("fictional\x00PRIVATE_NUL_CANARY",),
    )
    connection.commit()
    connection.close()
    before = _identity(source)
    output = tmp_path / "existing.ged"
    output.write_bytes(b"sentinel\n")

    with pytest.raises(FileIngressError) as raised:
        RootsMagicExporter(RootsMagicReader([tmp_path])).export(source, output)

    assert raised.value.code == "FILE_NUL_BYTE_UNSUPPORTED"
    assert "PRIVATE_NUL_CANARY" not in raised.value.render()
    assert output.read_bytes() == b"sentinel\n"
    assert _identity(source) == before


def test_invalid_utf8_text_storage_fails_stably_without_output_or_disclosure(
    tmp_path: Path,
) -> None:
    source = _tree(
        tmp_path / "invalid-text.rmtree",
        """
        CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY, FictionalCustom TEXT);
        INSERT INTO PersonTable VALUES (1, CAST(X'80' AS TEXT));
        """,
    )
    before = _identity(source)
    output = tmp_path / "existing.ged"
    output.write_bytes(b"sentinel\n")

    with pytest.raises(AncestryError) as raised:
        RootsMagicExporter(RootsMagicReader([tmp_path])).export(source, output)

    assert raised.value.code == "ROOTSMAGIC_READ_FAILED"
    assert str(source) not in raised.value.render()
    assert output.read_bytes() == b"sentinel\n"
    assert _identity(source) == before


def test_quoted_hostile_unknown_identifier_is_data_not_executable_sql(
    tmp_path: Path,
) -> None:
    source = tmp_path / "quoted-identifier.rmtree"
    marker = tmp_path / "must-not-exist.sqlite"
    hostile = f"Odd\"; ATTACH DATABASE '{marker}' AS escaped; --"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO PersonTable VALUES (1)")
    connection.execute(f'CREATE TABLE "{hostile.replace(chr(34), chr(34) * 2)}"(Value TEXT)')
    connection.execute(
        f'INSERT INTO "{hostile.replace(chr(34), chr(34) * 2)}" VALUES (?)',  # noqa: S608
        ("fictional",),
    )
    connection.commit()
    connection.close()
    before = _identity(source)

    schema = RootsMagicReader([tmp_path]).schema(source)

    assert hostile in schema
    assert not marker.exists()
    assert _identity(source) == before


def test_missing_and_duplicate_person_ids_fail_with_stable_redacted_codes(
    tmp_path: Path,
) -> None:
    missing = _tree(
        tmp_path / "missing-id.rmtree",
        "CREATE TABLE PersonTable(PersonID INTEGER, Private TEXT);"
        "INSERT INTO PersonTable VALUES (NULL, 'PRIVATE_MISSING');",
    )
    duplicate = _tree(
        tmp_path / "duplicate-id.rmtree",
        "CREATE TABLE PersonTable(PersonID INTEGER, Private TEXT);"
        "INSERT INTO PersonTable VALUES (1, 'PRIVATE_FIRST');"
        "INSERT INTO PersonTable VALUES (1, 'PRIVATE_SECOND');",
    )

    for source, expected_code in (
        (missing, "ROOTSMAGIC_SCHEMA_UNSUPPORTED"),
        (duplicate, "ROOTSMAGIC_SCHEMA_UNSUPPORTED"),
    ):
        with pytest.raises(AncestryError) as raised:
            RootsMagicExporter(RootsMagicReader([tmp_path])).export(
                source,
                tmp_path / f"{source.stem}.ged",
            )
        assert raised.value.code == expected_code
        assert "PRIVATE_" not in raised.value.render()


def test_empty_person_table_fails_closed_without_replacing_outputs(tmp_path: Path) -> None:
    source = _tree(
        tmp_path / "empty-person-table.rmtree",
        "CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY, Private TEXT);",
    )
    before = _identity(source)
    output = tmp_path / "existing-empty.ged"
    report = tmp_path / "existing-empty.md"
    output.write_bytes(b"GEDCOM sentinel\n")
    report.write_bytes(b"report sentinel\n")

    with pytest.raises(AncestryError) as raised:
        RootsMagicExporter(RootsMagicReader([tmp_path])).export(
            source,
            output,
            report_path=report,
        )

    assert raised.value.code == "ROOTSMAGIC_SCHEMA_UNSUPPORTED"
    assert str(source) not in raised.value.render()
    assert _identity(source) == before
    assert output.read_bytes() == b"GEDCOM sentinel\n"
    assert report.read_bytes() == b"report sentinel\n"


def test_incompatible_declared_person_identifier_type_fails_closed(
    tmp_path: Path,
) -> None:
    source = _tree(
        tmp_path / "text-person-id.rmtree",
        """
        CREATE TABLE PersonTable(PersonID TEXT PRIMARY KEY, Private TEXT);
        INSERT INTO PersonTable VALUES ('1', 'PRIVATE_DECLARED_TYPE');
        """,
    )
    before = _identity(source)
    output = tmp_path / "must-not-exist-text-id.ged"

    with pytest.raises(AncestryError) as raised:
        RootsMagicExporter(RootsMagicReader([tmp_path])).export(source, output)

    assert raised.value.code == "ROOTSMAGIC_SCHEMA_UNSUPPORTED"
    assert "PRIVATE_DECLARED_TYPE" not in raised.value.render()
    assert str(source) not in raised.value.render()
    assert _identity(source) == before
    assert not output.exists()


def test_incompatible_runtime_person_identifier_type_fails_closed(tmp_path: Path) -> None:
    source = _tree(
        tmp_path / "text-value-person-id.rmtree",
        """
        CREATE TABLE PersonTable(PersonID INTEGER, Private TEXT);
        INSERT INTO PersonTable VALUES ('FICTIONAL_RUNTIME_ID', 'PRIVATE_RUNTIME_TYPE');
        """,
    )
    before = _identity(source)
    output = tmp_path / "must-not-exist-runtime-id.ged"

    with pytest.raises(AncestryError) as raised:
        RootsMagicExporter(RootsMagicReader([tmp_path])).export(source, output)

    assert raised.value.code == "ROOTSMAGIC_SCHEMA_UNSUPPORTED"
    assert "PRIVATE_RUNTIME_TYPE" not in raised.value.render()
    assert "FICTIONAL_RUNTIME_ID" not in raised.value.render()
    assert str(source) not in raised.value.render()
    assert _identity(source) == before
    assert not output.exists()


def test_orphans_self_parenting_and_cycles_are_deterministic_and_source_read_only(
    tmp_path: Path,
) -> None:
    source = _tree(
        tmp_path / "hostile-links.rmtree",
        """
        CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY, Sex TEXT, Living INTEGER);
        CREATE TABLE NameTable(
            NameID INTEGER PRIMARY KEY, OwnerID INTEGER, Given TEXT, Surname TEXT, IsPrimary INTEGER
        );
        CREATE TABLE FamilyTable(FamilyID INTEGER PRIMARY KEY, FatherID INTEGER, MotherID INTEGER);
        CREATE TABLE ChildTable(FamilyID INTEGER, ChildID INTEGER);
        INSERT INTO PersonTable VALUES (1, 'U', 0), (2, 'U', 0);
        INSERT INTO NameTable VALUES
            (1, 1, 'FictionalOne', 'Example', 1),
            (2, 2, 'FictionalTwo', 'Example', 1),
            (3, 999, 'Orphan', 'Canary', 1);
        INSERT INTO FamilyTable VALUES (10, 1, 1), (11, 2, NULL), (12, 999, NULL);
        INSERT INTO ChildTable VALUES (10, 1), (11, 1), (12, 999), (999, 2);
        """,
    )
    before = _identity(source)
    first = tmp_path / "first.ged"
    second = tmp_path / "second.ged"
    exporter = RootsMagicExporter(RootsMagicReader([tmp_path]))

    exporter.export(source, first)
    exporter.export(source, second)

    assert first.read_bytes() == second.read_bytes()
    assert b"FictionalOne" in first.read_bytes()
    assert b"Orphan" not in first.read_bytes()
    assert _identity(source) == before
