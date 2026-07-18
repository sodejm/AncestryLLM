"""Security and immutability tests for RootsMagic SQLite access."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ancestryllm.core.errors import AncestryError, SecurityPolicyError
from ancestryllm.rootsmagic.reader import RootsMagicReader, sha256_file


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    path = tmp_path / "fictional.rmtree"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY, Living INTEGER, Sex INTEGER);
        CREATE TABLE NameTable(
            NameID INTEGER PRIMARY KEY,
            OwnerID INTEGER,
            Given TEXT,
            Surname TEXT,
            IsPrimary INTEGER
        );
        INSERT INTO PersonTable VALUES(1, 0, 0), (2, 0, 1);
        INSERT INTO NameTable VALUES(1, 1, 'Ada', 'Example', 1);
        INSERT INTO NameTable VALUES(2, 2, 'Bea', 'Example', 1);
        """
    )
    connection.commit()
    connection.close()
    return path


def test_reader_lists_and_resolves_only_configured_trees(tree: Path) -> None:
    reader = RootsMagicReader([tree.parent])
    assert reader.list_trees() == [tree]
    assert reader.resolve_tree("fictional") == tree
    with pytest.raises(AncestryError, match="No configured RootsMagic"):
        reader.resolve_tree("../private")


def test_select_is_bounded_and_does_not_change_source(tree: Path) -> None:
    reader = RootsMagicReader([tree.parent], max_rows=1)
    before = sha256_file(tree)
    result = reader.query(tree, "SELECT PersonID FROM PersonTable ORDER BY PersonID")
    assert result.rows == ((1,),)
    assert result.truncated is True
    assert sha256_file(tree) == before


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM PersonTable",
        "UPDATE PersonTable SET Living=1",
        "ATTACH DATABASE '/tmp/escape.db' AS x",
        "PRAGMA user_version",
        "SELECT 1; SELECT 2",
        "SELECT load_extension('/tmp/evil')",
    ],
)
def test_forbidden_sql_is_rejected(tree: Path, statement: str) -> None:
    reader = RootsMagicReader([tree.parent])
    with pytest.raises(SecurityPolicyError):
        reader.query(tree, statement)


def test_unknown_table_is_rejected(tree: Path) -> None:
    reader = RootsMagicReader([tree.parent])
    with pytest.raises(SecurityPolicyError, match="outside"):
        reader.query(tree, "SELECT * FROM imaginary")
