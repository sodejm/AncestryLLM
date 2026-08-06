"""Tests for encrypted storage behavior and diagnostics."""

from __future__ import annotations

import base64
import builtins
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from ancestryllm.core.errors import StorageError
from ancestryllm.core.jobs import JobManager, JobState
from ancestryllm.core.secrets import (
    REDACTED_VALUE,
    KeyringSecretStore,
    MemorySecretStore,
)
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


def test_backup_rejects_and_preserves_a_dangling_destination_symlink(tmp_path: Path) -> None:
    destination = tmp_path / "backup.db"
    try:
        destination.symlink_to(tmp_path / "missing-target.db")
    except OSError:
        pytest.skip("Symbolic links are unavailable on this filesystem.")
    link_target = destination.readlink()
    database = Database(tmp_path / "workspace.db", MemorySecretStore({}))

    with pytest.raises(StorageError) as raised:
        database.backup(destination)

    assert raised.value.code == "BACKUP_EXISTS"
    assert destination.is_symlink()
    assert destination.readlink() == link_target
    assert not list(tmp_path.glob(".ancestry-publish-*"))


def test_cancelled_backup_removes_unpublished_staging_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets = MemorySecretStore({})
    database = Database(tmp_path / "workspace.db", secrets)
    database.initialize()
    started = threading.Event()
    release = threading.Event()
    closed: list[bool] = []

    class FakeDriverConnection:
        @staticmethod
        def backup(target, *, pages, progress) -> None:
            del target, pages
            started.set()
            assert release.wait(2)
            progress(0, 1, 1)

    class FakeRawConnection:
        driver_connection = FakeDriverConnection()

        @staticmethod
        def close() -> None:
            closed.append(True)

    monkeypatch.setattr(database.engine, "raw_connection", lambda: FakeRawConnection())
    manager = JobManager(max_workers=1, max_pending=1)
    destination = tmp_path / "cancelled-backup.db"
    try:
        job = manager.submit("database backup", lambda: database.backup(destination))
        assert started.wait(2)
        manager.cancel(job.job_id)
        release.set()
        cancelled = manager.wait(job.job_id, timeout=2)
    finally:
        release.set()
        manager.shutdown()
        database.close()

    assert cancelled.state is JobState.CANCELLED
    assert closed == [True]
    assert destination.exists() is False
    assert list(tmp_path.glob(".ancestry-publish-*")) == []


def test_storage_diagnostics_are_read_only_and_serializable(tmp_path: Path) -> None:
    private_parent = tmp_path / "PRIVATE-HOST-DIAGNOSTIC-PATH"
    path = private_parent / "workspace.db"

    diagnostics = diagnose_storage(path, MemorySecretStore({}))

    assert path.exists() is False
    assert {item["code"] for item in diagnostics} >= {"SQLCIPHER_READY", "KEYRING_READY"}
    assert "DATABASE_DIRECTORY_MISSING" in {item["code"] for item in diagnostics}
    assert all(item["code"] and item["message"] for item in diagnostics)
    serialized = json.dumps(diagnostics)
    assert str(private_parent) not in serialized
    assert "PRIVATE-HOST-DIAGNOSTIC-PATH" not in serialized


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


def test_headless_environment_fallback_is_read_only_and_redacted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_value = "fictional-ci-database-key"

    class UnavailableKeyring:
        @staticmethod
        def get_password(_service_name: str, _name: str) -> str | None:
            raise RuntimeError("fictional headless backend")

        @staticmethod
        def set_password(_service_name: str, _name: str, _value: str) -> None:
            raise AssertionError("environment fallback must not write to the keyring")

        @staticmethod
        def delete_password(_service_name: str, _name: str) -> None:
            raise AssertionError("environment fallback must not delete from the keyring")

    monkeypatch.setitem(sys.modules, "keyring", UnavailableKeyring())
    monkeypatch.setenv("ANCESTRYLLM_DATABASE_KEY", secret_value)
    secret_store = KeyringSecretStore()

    diagnostics = diagnose_storage(tmp_path / "workspace.db", secret_store)

    assert {item["code"] for item in diagnostics} >= {"KEYRING_READY"}
    assert secret_value not in repr(diagnostics)
    assert secret_store.redact(f"database-key={secret_value}") == (f"database-key={REDACTED_VALUE}")
    captured = capsys.readouterr()
    assert secret_value not in captured.out
    assert secret_value not in captured.err
    assert list(tmp_path.iterdir()) == []


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
