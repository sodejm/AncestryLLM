from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ancestryllm.gedcom.engine import validate_gedcom_555
from ancestryllm.rootsmagic.exporter import RootsMagicExporter
from ancestryllm.rootsmagic.reader import RootsMagicReader, sha256_file


@pytest.fixture
def export_tree(tmp_path: Path) -> Path:
    tree = tmp_path / "fictional.rmtree"
    connection = sqlite3.connect(tree)
    connection.executescript(
        """
        CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY, Sex INTEGER, Living INTEGER, Favorite TEXT);
        CREATE TABLE NameTable(NameID INTEGER PRIMARY KEY, OwnerID INTEGER, Surname TEXT, Given TEXT, IsPrimary INTEGER);
        CREATE TABLE FamilyTable(FamilyID INTEGER PRIMARY KEY, FatherID INTEGER, MotherID INTEGER);
        CREATE TABLE ChildTable(FamilyID INTEGER, ChildID INTEGER);
        CREATE TABLE UnsupportedTable(Value TEXT);
        INSERT INTO PersonTable VALUES(1, 0, 0, 'Blue'), (2, 1, 0, 'Green'), (3, 1, 1, 'Private');
        INSERT INTO NameTable VALUES(1, 1, 'Example', 'Ada', 1), (2, 2, 'Example', 'Bea', 1), (3, 3, 'Example', 'Child', 1);
        INSERT INTO FamilyTable VALUES(1, 1, 2);
        INSERT INTO ChildTable VALUES(1, 3);
        """
    )
    connection.commit()
    connection.close()
    return tree


def test_portable_export_omits_living_and_reports_loss(export_tree: Path, tmp_path: Path) -> None:
    before = sha256_file(export_tree)
    result = RootsMagicExporter(RootsMagicReader([tmp_path])).export(
        export_tree,
        tmp_path / "portable.ged",
        profile="portable",
        destination="ancestry",
        living="exclude",
    )
    lines = result.output_path.read_text(encoding="utf-8").splitlines()
    validate_gedcom_555(lines)
    assert "Child /Example/" not in "\n".join(lines)
    assert "1 SEX M" in lines
    assert "1 SEX F" in lines
    assert "UnsupportedTable" in result.report.unmapped_tables
    assert sha256_file(export_tree) == before


def test_preservation_export_retains_scalar_custom_values(
    export_tree: Path, tmp_path: Path
) -> None:
    result = RootsMagicExporter(RootsMagicReader([tmp_path])).export(
        export_tree,
        tmp_path / "preservation.ged",
        profile="preservation",
        living="include",
    )
    text = result.output_path.read_text(encoding="utf-8")
    assert "1 _RM_FAVORITE Blue" in text
    assert result.report.people_written == 3
