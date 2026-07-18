"""SQLCipher engine creation, integrity verification, and schema bootstrap."""

from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import SingletonThreadPool

from ancestryllm.core.errors import StorageError
from ancestryllm.core.secrets import SecretStore
from ancestryllm.storage.models import Base

SQLITE_HEADER = b"SQLite format 3\x00"
DATABASE_SECRET = "database.master_key"  # noqa: S105 - keyring reference, not a credential
SCHEMA_REVISION = "0001"


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

        try:
            self.path.chmod(0o600)
        except OSError:
            pass
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
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
            current = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
            if current is None:
                connection.exec_driver_sql(
                    "INSERT INTO alembic_version(version_num) VALUES (?)", (SCHEMA_REVISION,)
                )
            elif current != SCHEMA_REVISION:
                raise StorageError(
                    "DATABASE_MIGRATION_REQUIRED",
                    f"Workspace schema {current!r} is not supported by this release.",
                    "Run the documented encrypted database migration command.",
                )

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
        if destination.exists():
            raise StorageError("BACKUP_EXISTS", f"Backup destination already exists: {destination}")
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        raw = self.engine.raw_connection()
        try:
            import sqlcipher3

            target = sqlcipher3.connect(str(destination))
            try:
                encoded = self.secret_store.get(DATABASE_SECRET)
                if not encoded:
                    raise StorageError("DATABASE_KEY_MISSING", "The database key is unavailable.")
                target.execute(f"PRAGMA key = \"x'{_decode_key(encoded).hex()}'\"")
                driver_connection = raw.driver_connection
                assert driver_connection is not None
                driver_connection.backup(target)
            finally:
                target.close()
        finally:
            raw.close()
        destination.chmod(0o600)
