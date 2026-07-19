"""Read-only first-run diagnostics for encrypted local storage."""

from __future__ import annotations

import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ancestryllm.core.errors import StorageError
from ancestryllm.core.secrets import SecretStore
from ancestryllm.storage.database import DATABASE_SECRET


@dataclass(frozen=True)
class StorageDiagnostic:
    code: str
    status: str
    message: str
    remediation: str | None = None


def _sqlcipher_diagnostic() -> StorageDiagnostic:
    version: Any | None = None
    try:
        import sqlcipher3

        connection = sqlcipher3.connect(":memory:")
        try:
            version = connection.execute("PRAGMA cipher_version").fetchone()
        finally:
            connection.close()
    except Exception:  # noqa: BLE001 - database driver errors vary by operating system
        return StorageDiagnostic(
            "SQLCIPHER_UNAVAILABLE",
            "error",
            "SQLCipher could not be imported for this Python environment.",
            "Install the supported ancestryllm package with SQLCipher; plaintext fallback is prohibited.",
        )
    if not version or not version[0]:
        return StorageDiagnostic(
            "SQLCIPHER_UNAVAILABLE",
            "error",
            "The installed SQLite driver does not report SQLCipher encryption support.",
            "Install a SQLCipher-enabled driver; do not use a plaintext SQLite driver.",
        )
    return StorageDiagnostic("SQLCIPHER_READY", "ok", "SQLCipher encryption support is available.")


def _keyring_diagnostic(secret_store: SecretStore) -> StorageDiagnostic:
    try:
        secret_store.present(DATABASE_SECRET)
    except StorageError as exc:
        return StorageDiagnostic(exc.code, "error", exc.message, exc.remediation)
    return StorageDiagnostic(
        "KEYRING_READY",
        "ok",
        "The configured credential-store backend can be queried without writing a secret.",
    )


def _path_diagnostics(path: Path) -> list[StorageDiagnostic]:
    diagnostics: list[StorageDiagnostic] = []
    parent = path.parent
    if not parent.exists():
        diagnostics.append(
            StorageDiagnostic(
                "DATABASE_DIRECTORY_MISSING",
                "warning",
                f"Workspace directory does not exist yet: {parent}",
                "Create the directory with owner-only permissions before first use.",
            )
        )
        return diagnostics
    if not os.access(parent, os.W_OK | os.X_OK):
        diagnostics.append(
            StorageDiagnostic(
                "DATABASE_DIRECTORY_UNWRITABLE",
                "error",
                f"Workspace directory is not writable: {parent}",
                "Choose a writable local directory owned by the current user.",
            )
        )
    else:
        diagnostics.append(
            StorageDiagnostic("DATABASE_DIRECTORY_READY", "ok", "Workspace directory is writable.")
        )
    if path.exists() and stat.S_IMODE(path.stat().st_mode) & (stat.S_IRWXG | stat.S_IRWXO):
        diagnostics.append(
            StorageDiagnostic(
                "DATABASE_PERMISSIONS_WEAK",
                "warning",
                "The encrypted workspace grants group or other permissions.",
                "Restrict the workspace file to its owner (chmod 600).",
            )
        )
    return diagnostics


def diagnose_storage(path: Path, secret_store: SecretStore) -> list[dict[str, Any]]:
    """Return serializable, payload-free diagnostics without creating a workspace."""
    diagnostics = [
        _sqlcipher_diagnostic(),
        _keyring_diagnostic(secret_store),
        *_path_diagnostics(path),
    ]
    return [asdict(item) for item in diagnostics]
