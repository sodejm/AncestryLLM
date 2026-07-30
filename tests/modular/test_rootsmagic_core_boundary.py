"""Physical and transport contracts for the 0.4 RootsMagic query core."""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import sqlite3
from pathlib import Path

import ancestryllm.rootsmagic.reader as reader_compatibility
import ancestryllm.rootsmagic.source as source_implementation
from ancestryllm.rootsmagic.core import DatabaseSchema, RootsMagicReader


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_legacy_reader_import_aliases_the_physical_source_module() -> None:
    assert reader_compatibility is source_implementation
    assert RootsMagicReader.__module__ == "ancestryllm.rootsmagic.source"


def test_query_core_has_no_adapter_policy_or_publication_imports() -> None:
    package = Path(source_implementation.__file__).parent
    forbidden_roots = {
        "ancestryllm.application",
        "ancestryllm.cli",
        "ancestryllm.config",
        "ancestryllm.console",
        "ancestryllm.core.publication",
        "ancestryllm.desktop",
        "ancestryllm.electron",
        "ancestryllm.llm",
        "ancestryllm.terminal",
        "fastapi",
        "keyring",
        "rich",
    }

    for name in ("core.py", "schema.py", "source.py"):
        tree = ast.parse((package / name).read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        assert not {
            dependency
            for dependency in imported
            if any(
                dependency == forbidden or dependency.startswith(f"{forbidden}.")
                for forbidden in forbidden_roots
            )
        }


def test_schema_and_query_dtos_are_deterministic_json_safe_and_immutable(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "fictional.rmtree"
    connection = sqlite3.connect(tree)
    connection.executescript(
        """
        CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY, Payload BLOB);
        INSERT INTO PersonTable VALUES (1, X'0001');
        INSERT INTO PersonTable VALUES (2, X'0203');
        INSERT INTO PersonTable VALUES (3, X'0405');
        """
    )
    connection.commit()
    connection.close()
    before = _sha256(tree)
    reader = RootsMagicReader([tmp_path], max_rows=2)

    schema = reader.inspect_schema(tree)
    result = reader.query(
        tree,
        "SELECT PersonID, Payload FROM PersonTable ORDER BY PersonID",
    )

    assert schema == DatabaseSchema(
        tables=(
            source_implementation.TableSchema(
                name="PersonTable",
                columns=("PersonID", "Payload"),
                declared_types=(("PersonID", "INTEGER"), ("Payload", "BLOB")),
            ),
        )
    )
    assert schema.as_mapping() == {"PersonTable": ("PersonID", "Payload")}
    assert result.rows == (
        (1, {"encoding": "base64", "data": "AAE="}),
        (2, {"encoding": "base64", "data": "AgM="}),
    )
    assert result.truncated is True
    assert result.truncation == source_implementation.TruncationMetadata(
        row_limit=2,
        returned_rows=2,
        has_more=True,
    )
    json.dumps(dataclasses.asdict(schema), allow_nan=False)
    json.dumps(dataclasses.asdict(result), allow_nan=False)
    assert _sha256(tree) == before
