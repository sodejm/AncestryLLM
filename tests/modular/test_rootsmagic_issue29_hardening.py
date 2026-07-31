from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

import ancestryllm.core.publication as publication_module
import ancestryllm.rootsmagic.exporter as exporter_module
import ancestryllm.rootsmagic.schema as schema_module
import ancestryllm.rootsmagic.schema_adapter as schema_adapter_module
from ancestryllm.core.errors import AncestryError, FileIngressError
from ancestryllm.rootsmagic.exporter import RootsMagicExporter
from ancestryllm.rootsmagic.reader import RootsMagicReader, sha256_file


def _create_tree(path: Path, script: str) -> Path:
    connection = sqlite3.connect(path)
    connection.executescript(script)
    connection.commit()
    connection.close()
    return path


@pytest.fixture
def comprehensive_tree(tmp_path: Path) -> Path:
    return _create_tree(
        tmp_path / "fictional-comprehensive.rmtree",
        """
        CREATE TABLE PersonTable(
            PersonID INTEGER PRIMARY KEY, Sex TEXT, Living INTEGER,
            Favorite TEXT, Portrait BLOB, EmptyValue TEXT
        );
        CREATE TABLE NameTable(
            NameID INTEGER PRIMARY KEY, OwnerID INTEGER, Surname TEXT,
            Given TEXT, IsPrimary INTEGER
        );
        CREATE TABLE FamilyTable(
            FamilyID INTEGER PRIMARY KEY, FatherID INTEGER, MotherID INTEGER,
            FamilyMark TEXT
        );
        CREATE TABLE ChildTable(FamilyID INTEGER, ChildID INTEGER);
        CREATE TABLE PlaceTable(PlaceID INTEGER PRIMARY KEY, Name TEXT);
        CREATE TABLE EventTable(
            EventID INTEGER PRIMARY KEY, OwnerID INTEGER, FamilyID INTEGER,
            EventType TEXT, Date TEXT, PlaceID INTEGER, Detail TEXT
        );
        CREATE TABLE NoteTable(NoteID INTEGER PRIMARY KEY, OwnerID INTEGER, Text TEXT);
        CREATE TABLE SourceTable(
            SourceID INTEGER PRIMARY KEY, OwnerID INTEGER, Title TEXT, Text TEXT
        );
        CREATE TABLE CitationTable(
            CitationID INTEGER PRIMARY KEY, OwnerID INTEGER, SourceID INTEGER,
            Page TEXT, Detail TEXT
        );
        CREATE TABLE MediaTable(
            MediaID INTEGER PRIMARY KEY, OwnerID INTEGER, File TEXT, Caption TEXT
        );
        CREATE TABLE UnsupportedTable(Value TEXT, SecretBlob BLOB);

        INSERT INTO PersonTable VALUES
            (10, 'M', 0, 'Blue', X'0102', NULL),
            (2, 'F', 0, 'Green', NULL, NULL),
            (30, 'U', 1, 'PRIVATE-FAVORITE', NULL, NULL);
        INSERT INTO NameTable VALUES
            (4, 10, 'Example', 'Alex', 0),
            (2, 2, 'Example', 'Blair', 1),
            (3, 10, 'Alias', 'A.', 0),
            (1, 10, 'Example', 'Alex', 1),
            (5, 10, 'Alias', 'A.', 0),
            (6, 30, 'Private', 'Living Canary', 1);
        INSERT INTO FamilyTable VALUES
            (100, 10, 2, 'Fictional union marker'),
            (200, 30, 0, 'PRIVATE-FAMILY-CANARY');
        INSERT INTO ChildTable VALUES (100, 30);
        INSERT INTO PlaceTable VALUES (8, 'Fictional City');
        INSERT INTO EventTable VALUES
            (11, 10, NULL, 'Birth', '1 JAN 1900', 8, 'Fictional birth detail'),
            (12, NULL, 100, 'Marriage', '2 FEB 1920', 8, 'Fictional marriage detail'),
            (13, 30, NULL, 'Birth', '3 MAR 2000', 8, 'PRIVATE-EVENT-CANARY');
        INSERT INTO NoteTable VALUES
            (20, 10, 'Fictional public note'),
            (21, 30, 'PRIVATE-NOTE-CANARY');
        INSERT INTO SourceTable VALUES
            (40, 10, 'Fictional register', 'Fictional public source'),
            (41, 30, 'PRIVATE-SOURCE-CANARY', 'PRIVATE-SOURCE-TEXT');
        INSERT INTO CitationTable VALUES
            (50, 10, 40, 'p. 1', 'Fictional citation'),
            (51, 10, 40, 'p. 1', 'Fictional citation'),
            (52, 30, 41, 'PRIVATE-PAGE', 'PRIVATE-CITATION-CANARY');
        INSERT INTO MediaTable VALUES
            (60, 10, 'fictional-public.jpg', 'Fictional public portrait'),
            (61, 30, 'PRIVATE-MEDIA.jpg', 'PRIVATE-MEDIA-CANARY'),
            (62, 10, X'CAFE', NULL);
        INSERT INTO UnsupportedTable VALUES ('fictional unsupported', X'CAFE');
        """,
    )


def _exporter(tmp_path: Path) -> RootsMagicExporter:
    return RootsMagicExporter(RootsMagicReader([tmp_path]))


def test_schema_adapter_is_the_implementation_module_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = object()

    assert schema_adapter_module is schema_module
    monkeypatch.setattr(schema_adapter_module, "_compatibility_marker", marker, raising=False)
    assert schema_module._compatibility_marker is marker


def test_schema_adapter_maps_loss_minimally_and_preserves_duplicates(
    comprehensive_tree: Path,
    tmp_path: Path,
) -> None:
    result = _exporter(tmp_path).export(
        comprehensive_tree,
        tmp_path / "comprehensive.ged",
        profile="preservation",
        living="include",
    )
    text = result.output_path.read_text(encoding="utf-8")
    report = result.report_path.read_text(encoding="utf-8")

    assert text.count("1 NAME Alex /Example/") == 2
    assert text.count("1 NAME A. /Alias/") == 2
    assert "1 BIRT" in text
    assert "2 DATE 1 JAN 1900" in text
    assert "2 PLAC Fictional City" in text
    assert "1 MARR" in text
    assert "Fictional public note" in text
    assert "Fictional register" in text
    assert text.count("2 PAGE p. 1") == 2
    assert "fictional-public.jpg" in text
    assert "Fictional public portrait" in text
    assert "1 _RM_FAVORITE Blue" in text
    assert "1 _RM_FAMILYMARK Fictional union marker" in text
    assert "0102" not in text
    assert "cafe" not in text.casefold()
    assert {
        "PersonTable",
        "NameTable",
        "FamilyTable",
        "ChildTable",
        "PlaceTable",
        "EventTable",
        "NoteTable",
        "SourceTable",
        "CitationTable",
        "MediaTable",
    }.issubset(result.report.mapped_tables)
    assert result.report.unmapped_tables == ["UnsupportedTable"]
    assert "File" in result.report.unmapped_columns["MediaTable"]
    assert "binary" in report.casefold()
    assert "reason:" in report.casefold()
    assert "count:" in report.casefold()
    assert "fictional unsupported" not in report


@pytest.mark.parametrize("living", ("exclude", "redact"))
def test_living_policy_contains_canaries_across_owned_records(
    comprehensive_tree: Path,
    tmp_path: Path,
    living: str,
) -> None:
    result = _exporter(tmp_path).export(
        comprehensive_tree,
        tmp_path / f"privacy-{living}.ged",
        profile="preservation",
        living=living,
    )
    combined = (
        result.output_path.read_text(encoding="utf-8")
        + result.report_path.read_text(encoding="utf-8")
        + repr(result.report)
    )

    for canary in (
        "Living Canary",
        "PRIVATE-FAVORITE",
        "PRIVATE-FAMILY-CANARY",
        "PRIVATE-EVENT-CANARY",
        "PRIVATE-NOTE-CANARY",
        "PRIVATE-SOURCE-CANARY",
        "PRIVATE-SOURCE-TEXT",
        "PRIVATE-PAGE",
        "PRIVATE-CITATION-CANARY",
        "PRIVATE-MEDIA.jpg",
        "PRIVATE-MEDIA-CANARY",
    ):
        assert canary not in combined
    if living == "redact":
        assert "1 NAME Living /Private/" in combined


@pytest.mark.parametrize("living", ("exclude", "redact"))
def test_loss_report_counts_privacy_dispositions_without_private_values(
    comprehensive_tree: Path,
    tmp_path: Path,
    living: str,
) -> None:
    result = _exporter(tmp_path).export(
        comprehensive_tree,
        tmp_path / f"privacy-dispositions-{living}.ged",
        profile="preservation",
        living=living,
    )
    report = result.report_path.read_text(encoding="utf-8")

    for table in (
        "NameTable",
        "FamilyTable",
        "EventTable",
        "NoteTable",
        "SourceTable",
        "CitationTable",
        "MediaTable",
    ):
        assert f"`{table}` records — count:" in report
    assert "reason: ownership, privacy, or selected scope cannot be safely represented" in report
    assert "PRIVATE-" not in report
    assert "Living Canary" not in report


def test_schema_aliases_and_semantic_order_are_stable(
    tmp_path: Path,
) -> None:
    first = _create_tree(
        tmp_path / "aliases-first.rmtree",
        """
        CREATE TABLE People(ID INTEGER PRIMARY KEY, Gender TEXT, IsLiving INTEGER, Memo TEXT);
        CREATE TABLE Names(PersonID INTEGER, GivenName TEXT, LastName TEXT, PrimaryName INTEGER);
        CREATE TABLE Families(ID INTEGER PRIMARY KEY, HusbandID INTEGER, WifeID INTEGER);
        CREATE TABLE Children(FamilyID INTEGER, PersonID INTEGER);
        INSERT INTO People VALUES (10, 'M', 0, 'ten'), (2, 'F', 0, 'two');
        INSERT INTO Names VALUES (10, 'Ten', 'Fixture', 1), (2, 'Two', 'Fixture', 1);
        INSERT INTO Families VALUES (9, 10, 2);
        """,
    )
    second = _create_tree(
        tmp_path / "aliases-second.rmtree",
        """
        CREATE TABLE People(ID INTEGER PRIMARY KEY, Gender TEXT, IsLiving INTEGER, Memo TEXT);
        CREATE TABLE Names(PersonID INTEGER, GivenName TEXT, LastName TEXT, PrimaryName INTEGER);
        CREATE TABLE Families(ID INTEGER PRIMARY KEY, HusbandID INTEGER, WifeID INTEGER);
        CREATE TABLE Children(FamilyID INTEGER, PersonID INTEGER);
        INSERT INTO People VALUES (2, 'F', 0, 'two'), (10, 'M', 0, 'ten');
        INSERT INTO Names VALUES (2, 'Two', 'Fixture', 1), (10, 'Ten', 'Fixture', 1);
        INSERT INTO Families VALUES (9, 10, 2);
        """,
    )

    first_result = _exporter(tmp_path).export(
        first,
        tmp_path / "aliases-first.ged",
        profile="preservation",
        living="include",
    )
    second_result = _exporter(tmp_path).export(
        second,
        tmp_path / "aliases-second.ged",
        profile="preservation",
        living="include",
    )

    assert first_result.output_path.read_bytes() == second_result.output_path.read_bytes()
    assert "0 @I1@ INDI\n1 NAME Two /Fixture/" in first_result.output_path.read_text(
        encoding="utf-8"
    )
    assert "1 _RM_MEMO two" in first_result.output_path.read_text(encoding="utf-8")


def test_gedcom_551_uses_common_structural_validation(
    comprehensive_tree: Path,
    tmp_path: Path,
) -> None:
    source_hash = sha256_file(comprehensive_tree)
    result = _exporter(tmp_path).export(
        comprehensive_tree,
        tmp_path / "compatible-551.ged",
        gedcom_version="5.5.1",
        living="exclude",
    )
    lines = result.output_path.read_text(encoding="utf-8").splitlines()

    assert lines[0] == "0 HEAD"
    assert lines[-1] == "0 TRLR"
    assert lines.count("2 VERS 5.5.1") == 1
    assert sha256_file(comprehensive_tree) == source_hash


def test_scope_normalizes_integral_sqlite_numeric_identifiers() -> None:
    included = RootsMagicExporter._scope_people(
        "1",
        "descendants",
        None,
        [{"FamilyID": 8.0, "FatherID": 1.0, "MotherID": 0.0}],
        [{"FamilyID": 8.0, "ChildID": 2.0}],
    )

    assert included == {"1", "2"}


def test_living_omission_count_is_limited_to_the_selected_scope(tmp_path: Path) -> None:
    tree = _create_tree(
        tmp_path / "fictional-disconnected-living.rmtree",
        """
        CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY, Living INTEGER);
        CREATE TABLE NameTable(
            NameID INTEGER PRIMARY KEY, OwnerID INTEGER, Given TEXT, Surname TEXT
        );
        INSERT INTO PersonTable VALUES (1, 0), (2, 1);
        INSERT INTO NameTable VALUES
            (1, 1, 'Public', 'Root'),
            (2, 2, 'Living', 'Disconnected');
        """,
    )

    result = _exporter(tmp_path).export(
        tree,
        tmp_path / "selected-scope.ged",
        root_person_id="1",
        scope="connected",
        living="exclude",
    )

    assert result.report.people_written == 1
    assert result.report.living_omitted == 0


@pytest.mark.parametrize("profile", ("portable", "preservation"))
@pytest.mark.parametrize("gedcom_version", ("5.5.5", "5.5.1"))
@pytest.mark.parametrize("destination", ("generic", "ancestry", "geni", "myheritage"))
@pytest.mark.parametrize("living", ("exclude", "redact", "include"))
def test_export_contract_matrix_is_structural_private_and_deterministic(
    comprehensive_tree: Path,
    tmp_path: Path,
    profile: str,
    gedcom_version: str,
    destination: str,
    living: str,
) -> None:
    stem = f"{profile}-{gedcom_version}-{destination}-{living}"
    exporter = _exporter(tmp_path)
    first = exporter.export(
        comprehensive_tree,
        tmp_path / f"{stem}-first.ged",
        profile=profile,
        gedcom_version=gedcom_version,
        destination=destination,
        living=living,
    )
    second = exporter.export(
        comprehensive_tree,
        tmp_path / f"{stem}-second.ged",
        profile=profile,
        gedcom_version=gedcom_version,
        destination=destination,
        living=living,
    )
    first_text = first.output_path.read_text(encoding="utf-8")
    second_text = second.output_path.read_text(encoding="utf-8")

    assert first_text == second_text
    assert first_text.startswith("0 HEAD\n")
    assert first_text.endswith("0 TRLR\n")
    assert first_text.count(f"2 VERS {gedcom_version}") == 1
    assert first.report.destination == destination
    if profile == "portable":
        assert "_RM_" not in first_text
    elif living == "include":
        assert "1 _RM_FAMILYMARK Fictional union marker" in first_text
    else:
        assert "1 _RM_FAVORITE Blue" in first_text
    if living != "include":
        assert "PRIVATE-" not in first_text


@pytest.mark.parametrize(
    ("boundary", "occurrence"),
    (
        ("write", 1),
        ("write", 2),
        ("claim", 1),
        ("claim", 2),
    ),
)
def test_staging_and_claim_failures_preserve_the_complete_prior_pair(
    comprehensive_tree: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    occurrence: int,
) -> None:
    output = tmp_path / f"{boundary}-{occurrence}.ged"
    report = output.with_suffix(".export.md")
    previous_output = b"previous fictional GEDCOM\n"
    previous_report = b"previous fictional mapping report\n"
    output.write_bytes(previous_output)
    report.write_bytes(previous_report)
    exporter = _exporter(tmp_path)

    if boundary == "write":
        original_write = exporter._atomic_write
        calls = 0

        def fail_selected_write(path: Path, payload: str) -> publication_module.StagedFileToken:
            nonlocal calls
            calls += 1
            if calls == occurrence:
                raise OSError("fictional staging write failure")
            return original_write(path, payload)

        monkeypatch.setattr(exporter, "_atomic_write", fail_selected_write)
    else:
        original_claim = exporter_module.claim_staged_path
        calls = 0

        def fail_selected_claim(
            path: Path,
            token: publication_module.StagedFileToken | None = None,
        ) -> None:
            nonlocal calls
            calls += 1
            if calls == occurrence:
                raise OSError("fictional ownership claim failure")
            original_claim(path, token)

        monkeypatch.setattr(exporter_module, "claim_staged_path", fail_selected_claim)

    with pytest.raises(OSError, match=r"fictional .* failure"):
        exporter.export(comprehensive_tree, output, living="include")

    assert output.read_bytes() == previous_output
    assert report.read_bytes() == previous_report
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_source_revalidation_failure_rolls_back_the_complete_prior_pair(
    comprehensive_tree: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "source-revalidation.ged"
    report = output.with_suffix(".export.md")
    previous_output = b"previous fictional GEDCOM\n"
    previous_report = b"previous fictional mapping report\n"
    output.write_bytes(previous_output)
    report.write_bytes(previous_report)
    reader = RootsMagicReader([tmp_path])

    def reject_revalidation(_path: Path, _fingerprint: object) -> None:
        raise FileIngressError(
            "FILE_INPUT_CHANGED",
            "The fictional source changed during export.",
        )

    monkeypatch.setattr(reader, "verify_source", reject_revalidation)
    with pytest.raises(AncestryError, match="database changed during export") as raised:
        RootsMagicExporter(reader).export(comprehensive_tree, output, living="include")

    assert raised.value.code == "ROOTSMAGIC_FILE_CHANGED"
    assert output.read_bytes() == previous_output
    assert report.read_bytes() == previous_report
    assert not list(tmp_path.glob(".ancestry-publish-*"))


@pytest.mark.parametrize("interrupt_rollback_once", (False, True))
def test_second_artifact_publication_failure_restores_the_complete_prior_pair(
    comprehensive_tree: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interrupt_rollback_once: bool,
) -> None:
    output = tmp_path / f"publication-{interrupt_rollback_once}.ged"
    report = output.with_suffix(".export.md")
    previous_output = b"previous fictional GEDCOM\n"
    previous_report = b"previous fictional mapping report\n"
    output.write_bytes(previous_output)
    report.write_bytes(previous_report)
    native_replace = exporter_module.os.replace

    def fail_report_publication(source: str | Path, destination: str | Path) -> None:
        if Path(source) == Path(destination) == report.resolve():
            raise OSError("fictional report publication failure")
        native_replace(source, destination)

    monkeypatch.setattr(exporter_module.os, "replace", fail_report_publication)

    if interrupt_rollback_once:
        original_restore = publication_module._restore_original
        restore_calls = 0

        def interrupt_first_restore(
            artifact: Any,
        ) -> OSError | None:
            nonlocal restore_calls
            restore_calls += 1
            if restore_calls == 1:
                raise KeyboardInterrupt("fictional rollback interruption")
            return original_restore(artifact)

        monkeypatch.setattr(publication_module, "_restore_original", interrupt_first_restore)

    with pytest.raises(OSError, match="fictional report publication failure"):
        _exporter(tmp_path).export(comprehensive_tree, output, living="include")

    assert output.read_bytes() == previous_output
    assert report.read_bytes() == previous_report
    assert not list(tmp_path.glob(".ancestry-publish-*"))
