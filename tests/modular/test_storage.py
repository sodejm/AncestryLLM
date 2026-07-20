from __future__ import annotations

import base64
import builtins
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ancestryllm.core.errors import StorageError
from ancestryllm.core.secrets import MemorySecretStore
from ancestryllm.storage.database import DATABASE_SECRET, SQLITE_HEADER, Database
from ancestryllm.storage.diagnostics import diagnose_storage


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


def test_storage_diagnostics_are_read_only_and_serializable(tmp_path: Path) -> None:
    path = tmp_path / "workspace.db"

    diagnostics = diagnose_storage(path, MemorySecretStore({}))

    assert path.exists() is False
    assert {item["code"] for item in diagnostics} >= {"SQLCIPHER_READY", "KEYRING_READY"}


def test_storage_diagnostics_report_keyring_failures_without_secret_values(tmp_path: Path) -> None:
    class BrokenSecretStore:
        def get(self, name: str) -> str | None:
            raise StorageError("KEYRING_READ_FAILED", "credential backend unavailable")

        def set(self, name: str, value: str) -> None:
            raise AssertionError("diagnostics must not write")

        def delete(self, name: str) -> None:
            raise AssertionError("diagnostics must not delete")

        def present(self, name: str) -> bool:
            return self.get(name) is not None

    diagnostics = diagnose_storage(tmp_path / "workspace.db", BrokenSecretStore())

    assert {item["code"] for item in diagnostics} >= {"KEYRING_READ_FAILED"}
    assert all(
        "credential backend unavailable" not in (item.get("remediation") or "")
        for item in diagnostics
    )


def test_storage_diagnostics_report_missing_sqlcipher(monkeypatch, tmp_path: Path) -> None:
    real_import = builtins.__import__

    def missing_sqlcipher(name, *args, **kwargs):
        if name == "sqlcipher3":
            raise ImportError("fictional SQLCipher missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_sqlcipher)

    diagnostics = diagnose_storage(tmp_path / "workspace.db", MemorySecretStore({}))

    sqlcipher = next(item for item in diagnostics if item["code"] == "SQLCIPHER_UNAVAILABLE")
    assert sqlcipher["status"] == "error"
    assert "plaintext" in sqlcipher["remediation"]


def test_storage_diagnostics_report_non_encrypting_sqlite_binding(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeConnection:
        def execute(self, _query):
            return self

        def fetchone(self):
            return (None,)

        def close(self):
            return None

    monkeypatch.setitem(
        sys.modules, "sqlcipher3", SimpleNamespace(connect=lambda _path: FakeConnection())
    )

    diagnostics = diagnose_storage(tmp_path / "workspace.db", MemorySecretStore({}))

    sqlcipher = next(item for item in diagnostics if item["code"] == "SQLCIPHER_UNAVAILABLE")
    assert "does not report SQLCipher" in sqlcipher["message"]


def test_storage_diagnostics_report_missing_directory_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "workspace.db"

    diagnostics = diagnose_storage(path, MemorySecretStore({}))

    directory = next(item for item in diagnostics if item["code"] == "DATABASE_DIRECTORY_MISSING")
    assert directory["status"] == "warning"
    assert path.exists() is False


@pytest.mark.skipif(not hasattr(Path, "chmod"), reason="path permissions unavailable")
def test_storage_diagnostics_report_weak_workspace_permissions(tmp_path: Path) -> None:
    path = tmp_path / "workspace.db"
    path.write_bytes(b"encrypted-looking")
    path.chmod(0o644)

    diagnostics = diagnose_storage(path, MemorySecretStore({}))

    permissions = next(item for item in diagnostics if item["code"] == "DATABASE_PERMISSIONS_WEAK")
    assert permissions["status"] == "warning"
