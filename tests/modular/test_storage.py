"""Verify encrypted storage, safe backups, and redacted read-only diagnostics."""

from __future__ import annotations

import base64
import builtins
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ancestryllm.core.errors import StorageError
from ancestryllm.core.jobs import JobManager, JobState
from ancestryllm.core.secrets import (
    REDACTED_VALUE,
    KeyringSecretStore,
    MemorySecretStore,
)
from ancestryllm.storage import database as database_module
from ancestryllm.storage.database import DATABASE_SECRET, SQLITE_HEADER, Database
from ancestryllm.storage.diagnostics import (
    StartupConfigurationFailure,
    StartupDiagnosticReport,
    diagnose_startup,
    diagnose_storage,
)
from ancestryllm.storage.models import Base


def test_workspace_is_encrypted_and_has_schema_revision(tmp_path: Path) -> None:
    secrets = MemorySecretStore({})
    path = tmp_path / "workspace.db"
    database = Database(path, secrets)
    database.initialize()
    assert path.read_bytes()[: len(SQLITE_HEADER)] != SQLITE_HEADER
    with database.engine.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0002"
        )
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
    assert secrets.present(DATABASE_SECRET)


def test_schema_bootstrap_and_reuse_do_not_reflect_sqlcipher_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(tmp_path / "workspace.db", MemorySecretStore({}))

    def reject_table_reflection(*args: object, **kwargs: object) -> bool:
        del args, kwargs
        raise AssertionError("schema bootstrap must not reflect individual SQLCipher tables")

    monkeypatch.setattr(database.engine.dialect, "has_table", reject_table_reflection)

    database.initialize()
    database.initialize()


def test_schema_bootstrap_bypasses_sqlalchemy_ddl_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(tmp_path / "workspace.db", MemorySecretStore({}))
    database.open()
    original_do_execute = database.engine.dialect.do_execute

    def reject_sqlalchemy_ddl(
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any = None,
    ) -> None:
        if statement.lstrip().upper().startswith(("CREATE TABLE", "CREATE INDEX")):
            raise AssertionError("schema DDL must execute through the native SQLCipher connection")
        original_do_execute(cursor, statement, parameters, context)

    monkeypatch.setattr(database.engine.dialect, "do_execute", reject_sqlalchemy_ddl)

    database.initialize()

    with database.engine.connect() as connection:
        table_names = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT GLOB 'sqlite_*'"
            )
        }
        assert table_names == {
            *Base.metadata.tables,
            "alembic_version",
        }


def test_native_schema_ddl_failure_rolls_back_partial_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(tmp_path / "workspace.db", MemorySecretStore({}))
    database.open()
    create_table = database_module.CreateTable
    compile_count = 0

    class FailSecondCreateTable:
        def __init__(self, table: Any) -> None:
            self.statement = create_table(table)

        def compile(self, *, dialect: Any) -> Any:
            nonlocal compile_count
            compile_count += 1
            if compile_count == 2:
                raise RuntimeError("fictional interrupted native schema bootstrap")
            return self.statement.compile(dialect=dialect)

    monkeypatch.setattr(database_module, "CreateTable", FailSecondCreateTable)

    with (
        pytest.raises(RuntimeError, match="interrupted native schema bootstrap"),
        database.engine.begin() as connection,
    ):
        database_module._create_tables_on_native_connection(
            connection,
            tuple(Base.metadata.sorted_tables[:2]),
        )

    with database.engine.connect() as connection:
        table_names = set(
            connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT GLOB 'sqlite_*'"
            ).scalars()
        )
    assert table_names == set()


def test_sqlcipher_logging_is_disabled_before_memory_security() -> None:
    statements: list[str] = []

    class FakeResult:
        def __init__(self, row: tuple[str, ...] | None = None) -> None:
            self.row = row

        def fetchone(self) -> tuple[str, ...] | None:
            return self.row

    class FakeConnection:
        def execute(self, statement: str) -> FakeResult:
            statements.append(statement)
            if statement == "PRAGMA cipher_version":
                return FakeResult(("4.14.0 community",))
            return FakeResult()

        def close(self) -> None:
            pass

    connection = FakeConnection()

    database_module._configure_sqlcipher_connection(connection, "00" * 32)

    assert statements == [
        f"PRAGMA key = \"x'{'00' * 32}'\"",
        "PRAGMA cipher_version",
        "PRAGMA cipher_log_level = NONE",
        "PRAGMA cipher_memory_security = ON",
        "PRAGMA foreign_keys = ON",
        "PRAGMA secure_delete = ON",
        "PRAGMA journal_mode = DELETE",
    ]


def test_unversioned_partial_schema_fails_closed_before_bootstrap(tmp_path: Path) -> None:
    database = Database(tmp_path / "workspace.db", MemorySecretStore({}))
    database.open()
    with database.engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE interrupted_bootstrap (value INTEGER)")

    with pytest.raises(StorageError) as raised:
        database.initialize()

    assert raised.value.code == "DATABASE_MIGRATION_REQUIRED"
    assert raised.value.remediation == (
        "Restore a verified encrypted backup or contact support before modifying the workspace."
    )
    with database.engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert tables == {"interrupted_bootstrap"}


def test_versioned_partial_schema_is_not_repaired_implicitly(tmp_path: Path) -> None:
    database = Database(tmp_path / "workspace.db", MemorySecretStore({}))
    database.initialize()
    with database.engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE job_events")

    with pytest.raises(StorageError) as raised:
        database.initialize()

    assert raised.value.code == "DATABASE_MIGRATION_REQUIRED"
    with database.engine.connect() as connection:
        assert not connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'job_events'"
        ).scalar()


def test_unexpected_sqlite_prefixed_user_table_fails_closed(tmp_path: Path) -> None:
    database = Database(tmp_path / "workspace.db", MemorySecretStore({}))
    database.initialize()
    with database.engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE sqliteXshadow (value INTEGER)")

    with pytest.raises(StorageError) as raised:
        database.initialize()

    assert raised.value.code == "DATABASE_MIGRATION_REQUIRED"


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


def test_backup_collision_error_and_logs_do_not_disclose_destination(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_parent = tmp_path / "PRIVATE-USERNAME-CANARY" / "PRIVATE-HOME-CANARY"
    private_parent.mkdir(parents=True)
    destination = private_parent / "PRIVATE-FICTIONAL-FAMILY-BACKUP.db"
    destination.write_bytes(b"existing encrypted backup sentinel")
    database = Database(
        tmp_path / "PRIVATE-DATABASE-PATH" / "workspace.db",
        MemorySecretStore({}),
    )

    with caplog.at_level("DEBUG"), pytest.raises(StorageError) as raised:
        database.backup(destination)

    assert raised.value.code == "BACKUP_EXISTS"
    assert raised.value.message == "The backup destination already exists."
    assert raised.value.remediation == (
        "Choose a different destination or remove the existing item before retrying."
    )
    rendered = raised.value.render() + caplog.text
    for private_value in (
        str(destination),
        destination.name,
        "PRIVATE-USERNAME-CANARY",
        "PRIVATE-HOME-CANARY",
        str(database.path),
        "PRIVATE-DATABASE-PATH",
    ):
        assert private_value not in rendered
    assert destination.read_bytes() == b"existing encrypted backup sentinel"
    assert not list(private_parent.glob(".ancestry-publish-*"))


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


def test_startup_diagnostics_are_typed_deterministic_and_ready_without_writes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "workspace.db"

    report = diagnose_startup(
        path, MemorySecretStore({}), operating_system="darwin", machine="arm64"
    )

    assert isinstance(report, StartupDiagnosticReport)
    assert report.schema_version == 1
    assert report.status == "ready"
    assert report.platform.operating_system == "macos"
    assert report.platform.architecture == "arm64"
    assert [component.component for component in report.components] == [
        "configuration",
        "sqlcipher",
        "keyring",
        "workspace",
    ]
    assert report.mutations_allowed is True
    assert path.exists() is False


def test_startup_diagnostics_block_mutations_for_corrupt_configuration_without_leaks(
    tmp_path: Path,
) -> None:
    private_marker = "PRIVATE-CONFIG-PAYLOAD-MARKER"

    report = diagnose_startup(
        tmp_path / private_marker / "workspace.db",
        MemorySecretStore({}),
        configuration_failure=StartupConfigurationFailure(
            code="CONFIG_INVALID",
        ),
        operating_system="linux",
        machine="x86_64",
    )

    configuration = next(
        component for component in report.components if component.component == "configuration"
    )
    assert report.status == "degraded"
    assert report.mutations_allowed is False
    assert configuration.status == "blocked"
    assert configuration.blocks_mutations is True
    assert private_marker not in repr(report)


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

    diagnostics = diagnose_storage(path, MemorySecretStore({}), operating_system="linux")

    permissions = next(item for item in diagnostics if item["code"] == "DATABASE_PERMISSIONS_WEAK")
    assert permissions["status"] == "warning"


@pytest.mark.skipif(not hasattr(Path, "chmod"), reason="path permissions unavailable")
def test_startup_diagnostics_do_not_apply_posix_modes_to_windows_acls(tmp_path: Path) -> None:
    path = tmp_path / "workspace.db"
    path.write_bytes(b"encrypted-looking")
    path.chmod(0o644)

    report = diagnose_startup(
        path,
        MemorySecretStore({}),
        operating_system="win32",
        machine="arm64",
    )

    workspace = next(
        component for component in report.components if component.component == "workspace"
    )
    assert report.status == "ready"
    assert workspace.status == "ready"
    assert workspace.code != "DATABASE_PERMISSIONS_WEAK"
