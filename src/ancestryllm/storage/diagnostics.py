"""Read-only first-run diagnostics for encrypted local storage."""

from __future__ import annotations

import os
import platform
import stat
import sys
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from ancestryllm.core.errors import StorageError
from ancestryllm.storage.database import DATABASE_SECRET

if TYPE_CHECKING:
    from pathlib import Path

    from ancestryllm.core.secrets import SecretStore


@dataclass(frozen=True)
class StorageDiagnostic:
    code: str
    status: str
    message: str
    remediation: str | None = None


@dataclass(frozen=True, slots=True)
class StartupConfigurationFailure:
    """Sanitized configuration failure supplied by the packaged host."""

    code: str


@dataclass(frozen=True, slots=True)
class StartupPlatformDetails:
    """Non-sensitive platform identity used to select relevant remediation."""

    operating_system: str
    architecture: str


@dataclass(frozen=True, slots=True)
class StartupDiagnosticComponent:
    """One deterministic, renderer-safe startup diagnostic."""

    component: str
    status: str
    code: str
    message: str
    remediation: str | None
    restart_required: bool
    blocks_mutations: bool


@dataclass(frozen=True, slots=True)
class StartupDiagnosticReport:
    """Versioned startup state with a fail-closed mutation decision."""

    schema_version: int
    status: str
    platform: StartupPlatformDetails
    components: tuple[StartupDiagnosticComponent, ...]

    @property
    def mutations_allowed(self) -> bool:
        return not any(component.blocks_mutations for component in self.components)


_CONFIGURATION_FAILURE_CATALOG = {
    "CONFIG_INVALID": (
        "The desktop configuration could not be validated.",
        "Repair or restore config.toml, then retry startup diagnostics.",
    ),
    "CONFIGURATION_UNAVAILABLE": (
        "The desktop configuration could not be read safely.",
        "Check owner permissions for the configuration, then retry startup diagnostics.",
    ),
}


def _platform_details(operating_system: str, machine: str) -> StartupPlatformDetails:
    normalized_os = {
        "darwin": "macos",
        "linux": "linux",
        "win32": "windows",
    }.get(operating_system.casefold(), "unsupported")
    normalized_architecture = {
        "aarch64": "arm64",
        "amd64": "x64",
        "arm64": "arm64",
        "x86_64": "x64",
    }.get(machine.casefold(), "unsupported")
    return StartupPlatformDetails(normalized_os, normalized_architecture)


def _configuration_component(
    failure: StartupConfigurationFailure | None,
) -> StartupDiagnosticComponent:
    if failure is None:
        return StartupDiagnosticComponent(
            component="configuration",
            status="ready",
            code="CONFIGURATION_READY",
            message="The desktop configuration is ready.",
            remediation=None,
            restart_required=False,
            blocks_mutations=False,
        )
    code = (
        failure.code
        if failure.code in _CONFIGURATION_FAILURE_CATALOG
        else "CONFIGURATION_UNAVAILABLE"
    )
    message, remediation = _CONFIGURATION_FAILURE_CATALOG[code]
    return StartupDiagnosticComponent(
        component="configuration",
        status="blocked",
        code=code,
        message=message,
        remediation=remediation,
        restart_required=False,
        blocks_mutations=True,
    )


def _sqlcipher_component(diagnostic: StorageDiagnostic) -> StartupDiagnosticComponent:
    ready = diagnostic.status == "ok"
    return StartupDiagnosticComponent(
        component="sqlcipher",
        status="ready" if ready else "blocked",
        code=diagnostic.code,
        message=diagnostic.message,
        remediation=diagnostic.remediation,
        restart_required=not ready,
        blocks_mutations=not ready,
    )


def _keyring_component(diagnostic: StorageDiagnostic) -> StartupDiagnosticComponent:
    ready = diagnostic.status == "ok"
    return StartupDiagnosticComponent(
        component="keyring",
        status="ready" if ready else "blocked",
        code=diagnostic.code,
        message=(
            diagnostic.message
            if ready
            else "The operating-system credential store could not be queried safely."
        ),
        remediation=(
            diagnostic.remediation
            if ready
            else "Unlock or repair the operating-system credential store, then retry."
        ),
        restart_required=False,
        blocks_mutations=not ready,
    )


def _workspace_component(diagnostics: list[StorageDiagnostic]) -> StartupDiagnosticComponent:
    priority = {"error": 3, "warning": 2, "ok": 1}
    selected = max(diagnostics, key=lambda item: priority.get(item.status, 3))
    blocks_mutations = selected.status == "error" or selected.code == "DATABASE_PERMISSIONS_WEAK"
    return StartupDiagnosticComponent(
        component="workspace",
        status="blocked"
        if blocks_mutations
        else ("warning" if selected.status == "warning" else "ready"),
        code=selected.code,
        message=selected.message,
        remediation=selected.remediation,
        restart_required=False,
        blocks_mutations=blocks_mutations,
    )


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
    try:
        parent_exists = parent.exists()
    except OSError:
        return [
            StorageDiagnostic(
                "DATABASE_DIRECTORY_UNAVAILABLE",
                "error",
                "The configured workspace directory could not be inspected safely.",
                "Check owner permissions for the local workspace directory, then retry.",
            )
        ]
    if not parent_exists:
        diagnostics.append(
            StorageDiagnostic(
                "DATABASE_DIRECTORY_MISSING",
                "warning",
                "The configured workspace directory does not exist yet.",
                "Create the directory with owner-only permissions before first use.",
            )
        )
        return diagnostics
    if not os.access(parent, os.W_OK | os.X_OK):
        diagnostics.append(
            StorageDiagnostic(
                "DATABASE_DIRECTORY_UNWRITABLE",
                "error",
                "The configured workspace directory is not writable.",
                "Choose a writable local directory owned by the current user.",
            )
        )
    else:
        diagnostics.append(
            StorageDiagnostic("DATABASE_DIRECTORY_READY", "ok", "Workspace directory is writable.")
        )
    try:
        workspace_exists = path.exists()
        workspace_mode = path.stat().st_mode if workspace_exists else None
    except OSError:
        diagnostics.append(
            StorageDiagnostic(
                "DATABASE_FILE_UNAVAILABLE",
                "error",
                "The encrypted workspace could not be inspected safely.",
                "Check owner permissions for the encrypted workspace, then retry.",
            )
        )
        return diagnostics
    if workspace_mode is not None and stat.S_IMODE(workspace_mode) & (stat.S_IRWXG | stat.S_IRWXO):
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


def diagnose_startup(
    path: Path,
    secret_store: SecretStore,
    *,
    configuration_failure: StartupConfigurationFailure | None = None,
    operating_system: str = sys.platform,
    machine: str | None = None,
) -> StartupDiagnosticReport:
    """Compose sanitized startup diagnostics without writing configuration or storage."""

    storage_diagnostics = [
        _sqlcipher_diagnostic(),
        _keyring_diagnostic(secret_store),
        *_path_diagnostics(path),
    ]
    components = (
        _configuration_component(configuration_failure),
        _sqlcipher_component(storage_diagnostics[0]),
        _keyring_component(storage_diagnostics[1]),
        _workspace_component(storage_diagnostics[2:]),
    )
    status = "degraded" if any(component.blocks_mutations for component in components) else "ready"
    return StartupDiagnosticReport(
        schema_version=1,
        status=status,
        platform=_platform_details(operating_system, machine or platform.machine()),
        components=components,
    )


__all__ = [
    "StartupConfigurationFailure",
    "StartupDiagnosticComponent",
    "StartupDiagnosticReport",
    "StartupPlatformDetails",
    "StorageDiagnostic",
    "diagnose_startup",
    "diagnose_storage",
]
