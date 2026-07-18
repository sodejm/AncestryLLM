from __future__ import annotations

import base64
from pathlib import Path

import pytest

from ancestryllm.core.errors import StorageError
from ancestryllm.core.secrets import MemorySecretStore
from ancestryllm.storage.database import DATABASE_SECRET, SQLITE_HEADER, Database


def test_workspace_is_encrypted_and_has_schema_revision(tmp_path: Path) -> None:
    secrets = MemorySecretStore({})
    path = tmp_path / "workspace.db"
    database = Database(path, secrets)
    database.initialize()
    assert path.read_bytes()[: len(SQLITE_HEADER)] != SQLITE_HEADER
    with database.engine.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0001"
        )
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
    assert secrets.present(DATABASE_SECRET)


def test_plaintext_database_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "workspace.db"
    path.write_bytes(SQLITE_HEADER + bytes(200))
    database = Database(
        path, MemorySecretStore({DATABASE_SECRET: base64.urlsafe_b64encode(bytes(32)).decode()})
    )
    with pytest.raises(StorageError, match="unencrypted"):
        database.open()


def test_existing_database_without_key_is_not_rekeyed(tmp_path: Path) -> None:
    path = tmp_path / "workspace.db"
    path.write_bytes(b"encrypted-looking" * 20)
    with pytest.raises(StorageError, match="key is missing"):
        Database(path, MemorySecretStore({})).open()


def test_wrong_key_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "workspace.db"
    first = MemorySecretStore({})
    Database(path, first).initialize()
    wrong = base64.urlsafe_b64encode(b"x" * 32).decode("ascii")
    with pytest.raises(StorageError, match="could not be opened"):
        Database(path, MemorySecretStore({DATABASE_SECRET: wrong})).open()


def test_backup_remains_encrypted(tmp_path: Path) -> None:
    secrets = MemorySecretStore({})
    database = Database(tmp_path / "workspace.db", secrets)
    database.initialize()
    backup = tmp_path / "backup.db"
    database.backup(backup)
    assert backup.read_bytes()[: len(SQLITE_HEADER)] != SQLITE_HEADER
    restored = Database(backup, secrets)
    restored.initialize()
