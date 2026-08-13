"""SQLCipher engine creation, integrity verification, and schema bootstrap."""

from __future__ import annotations

import base64
import os
import secrets
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import Engine, Table, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import SingletonThreadPool
from sqlalchemy.schema import CreateIndex, CreateTable

from ancestryllm.core.cancellation import cancellation_checkpoint
from ancestryllm.core.errors import StorageError
from ancestryllm.core.publication import (
    claim_staged_path,
    cleanup_staged_path,
    publish_staged_bundle,
    seal_staged_path,
    staging_path,
)
from ancestryllm.storage.models import Base, JobEventModel, JobModel

if TYPE_CHECKING:
    from pathlib import Path

    from ancestryllm.core.secrets import SecretStore

SQLITE_HEADER = b"SQLite format 3\x00"
DATABASE_SECRET = "database.master_key"  # noqa: S105 - keyring reference, not a credential
PREVIOUS_SCHEMA_REVISION = "0001"
SCHEMA_REVISION = "0002"


def _schema_table_names(connection: Any) -> frozenset[str]:
    """Return user-defined table names without per-table SQLCipher reflection."""
    return frozenset(
        str(name)
        for name in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT GLOB 'sqlite_*' ORDER BY name"
        ).scalars()
    )


def _expected_schema_tables(*, revision: str) -> frozenset[str]:
    tables = frozenset(str(name) for name in Base.metadata.tables)
    if revision == PREVIOUS_SCHEMA_REVISION:
        tables -= {JobModel.__tablename__, JobEventModel.__tablename__}
    return tables | {"alembic_version"}


def _create_tables_on_native_connection(connection: Any, tables: tuple[Table, ...]) -> None:
    """Compile authoritative metadata and execute DDL through SQLCipher directly.

    The SQLAlchemy DDL execution visitor can exhaust the native stack in the
    bundled Windows ARM64 runtime. Compiling from the mapped tables preserves
    the single schema definition while bypassing that failing execution path.
    """
    native_connection = connection.connection.driver_connection
    assert native_connection is not None
    for table in tables:
        native_connection.execute(str(CreateTable(table).compile(dialect=connection.dialect)))
        for index in sorted(table.indexes, key=lambda candidate: candidate.name or ""):
            native_connection.execute(str(CreateIndex(index).compile(dialect=connection.dialect)))


def _migration_required(message: str) -> StorageError:
    return StorageError(
        "DATABASE_MIGRATION_REQUIRED",
        message,
        "Restore a verified encrypted backup or contact support before modifying the workspace.",
    )


def _integrity_result(connection: Any) -> str | None:
    """Run the strongest integrity check supported by the SQLCipher build."""
    cipher_result = connection.execute("PRAGMA cipher_integrity_check").fetchone()
    if cipher_result and cipher_result[0]:
        return str(cipher_result[0])
    fallback = connection.execute("PRAGMA integrity_check").fetchone()
    return str(fallback[0]) if fallback and fallback[0] else None


def _decode_key(encoded: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(encoded.encode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise StorageError(
            "DATABASE_KEY_INVALID",
            "The database key stored in the credential manager is malformed.",
            "Restore the original key from a secure backup; do not create a replacement key.",
        ) from exc
    if len(key) != 32:
        raise StorageError(
            "DATABASE_KEY_INVALID",
            "The database key stored in the credential manager has an invalid length.",
            "Restore the original 256-bit key from a secure backup.",
        )
    return key


@dataclass(slots=True)
class Database:
    """Own the encrypted writable database and nothing else."""

    path: Path
    secret_store: SecretStore
    _engine: Engine | None = field(init=False, default=None, repr=False)
    _sessions: sessionmaker[Session] | None = field(init=False, default=None, repr=False)

    def _database_key(self) -> bytes:
        encoded = self.secret_store.get(DATABASE_SECRET)
        if encoded:
            return _decode_key(encoded)
        if self.path.exists() and self.path.stat().st_size:
            raise StorageError(
                "DATABASE_KEY_MISSING",
                "The encrypted workspace exists but its key is missing from the OS keyring.",
                "Restore the original key. Creating a new key would make the workspace unreadable.",
            )
        key = secrets.token_bytes(32)
        self.secret_store.set(DATABASE_SECRET, base64.urlsafe_b64encode(key).decode("ascii"))
        return key

    def _reject_plaintext(self) -> None:
        if not self.path.exists() or self.path.stat().st_size < len(SQLITE_HEADER):
            return
        with self.path.open("rb") as handle:
            if handle.read(len(SQLITE_HEADER)) == SQLITE_HEADER:
                raise StorageError(
                    "PLAINTEXT_DATABASE_REJECTED",
                    "The configured workspace is an unencrypted SQLite database.",
                    "Move it aside and use the documented encrypted migration process.",
                )

    def open(self) -> Database:
        if self._engine is not None:
            return self
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._reject_plaintext()
        key = self._database_key()
        existed = self.path.exists() and self.path.stat().st_size > 0

        try:
            import sqlcipher3
        except ImportError as exc:  # pragma: no cover - package dependency
            raise StorageError(
                "SQLCIPHER_UNAVAILABLE",
                "SQLCipher support is not installed; plaintext fallback is prohibited.",
                "Install the supported ancestryllm package for this platform.",
            ) from exc

        key_hex = key.hex()

        def connect() -> Any:
            connection = sqlcipher3.connect(str(self.path), check_same_thread=False)
            connection.execute(f"PRAGMA key = \"x'{key_hex}'\"")
            version = connection.execute("PRAGMA cipher_version").fetchone()
            if not version or not version[0]:
                connection.close()
                raise StorageError(
                    "SQLCIPHER_UNAVAILABLE",
                    "The SQLite driver does not provide SQLCipher encryption.",
                )
            connection.execute("PRAGMA cipher_memory_security = ON")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("PRAGMA journal_mode = DELETE")
            return connection

        try:
            self._engine = create_engine(
                "sqlite://",
                creator=connect,
                poolclass=SingletonThreadPool,
                future=True,
            )
            with self._engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                if existed:
                    result = _integrity_result(connection.connection.driver_connection)
                    if result != "ok":
                        raise StorageError(
                            "DATABASE_INTEGRITY_FAILED",
                            "The encrypted workspace failed its SQLCipher integrity check.",
                            "Stop using the file and restore the latest verified encrypted backup.",
                        )
        except StorageError:
            if self._engine is not None:
                self._engine.dispose()
                self._engine = None
            raise
        except Exception as exc:
            if self._engine is not None:
                self._engine.dispose()
                self._engine = None
            raise StorageError(
                "DATABASE_OPEN_FAILED",
                "The encrypted workspace could not be opened with its stored key.",
                "Verify the keyring entry and restore a matching encrypted backup if necessary.",
                details={"error_type": type(exc).__name__},
            ) from exc

        with suppress(OSError):
            self.path.chmod(0o600)
        event.listen(
            self._engine, "connect", lambda dbapi, _: dbapi.execute("PRAGMA foreign_keys=ON")
        )
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        return self

    @property
    def engine(self) -> Engine:
        self.open()
        assert self._engine is not None
        return self._engine

    def initialize(self) -> None:
        with self.engine.begin() as connection:
            schema_tables = _schema_table_names(connection)
            version_table_exists = "alembic_version" in schema_tables
            if version_table_exists:
                revisions = tuple(
                    connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalars()
                )
                if len(revisions) > 1 or (
                    revisions and revisions[0] not in {PREVIOUS_SCHEMA_REVISION, SCHEMA_REVISION}
                ):
                    rendered = revisions[0] if len(revisions) == 1 else "multiple revisions"
                    raise _migration_required(
                        f"Workspace schema {rendered!r} is not supported by this release.",
                    )
            else:
                revisions = ()

            current = revisions[0] if revisions else None
            if current is None:
                if schema_tables:
                    raise _migration_required(
                        "The encrypted workspace contains an incomplete unversioned schema."
                    )
                _create_tables_on_native_connection(
                    connection,
                    tuple(Base.metadata.sorted_tables),
                )
                native_connection = connection.connection.driver_connection
                assert native_connection is not None
                native_connection.execute(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
                native_connection.execute(
                    "INSERT INTO alembic_version(version_num) VALUES (?)", (SCHEMA_REVISION,)
                )
                return

            if schema_tables != _expected_schema_tables(revision=current):
                raise _migration_required(
                    f"Workspace schema {current!r} has an incomplete or unexpected table layout."
                )

            if current == PREVIOUS_SCHEMA_REVISION:
                _create_tables_on_native_connection(
                    connection,
                    (
                        cast("Table", JobModel.__table__),
                        cast("Table", JobEventModel.__table__),
                    ),
                )
                connection.exec_driver_sql(
                    "UPDATE alembic_version SET version_num = ?",
                    (SCHEMA_REVISION,),
                )
                return

    def session(self) -> Session:
        self.initialize()
        assert self._sessions is not None
        return self._sessions()

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
        self._engine = None
        self._sessions = None

    def backup(self, destination: Path) -> None:
        """Create an encrypted backup using SQLCipher's online backup API."""
        cancellation_checkpoint()
        if os.path.lexists(destination):
            raise StorageError(
                "BACKUP_EXISTS",
                "The backup destination already exists.",
                "Choose a different destination or remove the existing item before retrying.",
            )
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        staged = staging_path(destination)
        try:
            raw = self.engine.raw_connection()
            try:
                import sqlcipher3

                target = sqlcipher3.connect(str(staged))
                try:
                    encoded = self.secret_store.get(DATABASE_SECRET)
                    if not encoded:
                        raise StorageError(
                            "DATABASE_KEY_MISSING", "The database key is unavailable."
                        )
                    target.execute(f"PRAGMA key = \"x'{_decode_key(encoded).hex()}'\"")
                    driver_connection = raw.driver_connection
                    assert driver_connection is not None
                    driver_connection.backup(
                        target,
                        pages=128,
                        progress=lambda _status, _remaining, _total: cancellation_checkpoint(),
                    )
                finally:
                    target.close()
            finally:
                raw.close()
            cancellation_checkpoint()
            token = seal_staged_path(staged)
            claim_staged_path(staged, token)
            publish_staged_bundle(((staged, destination),), replace=os.replace)
            destination.chmod(0o600)
        except BaseException:
            cleanup_staged_path(staged)
            raise
