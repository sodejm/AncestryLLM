"""Focused encrypted-database maintenance command handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ancestryllm.application._artifacts import _ArtifactRegistry
from ancestryllm.application.executor import CommandInvocation, CommandOutcome
from ancestryllm.application.results import FileArtifactResult
from ancestryllm.execution.common import path, table_result

if TYPE_CHECKING:
    from ancestryllm.core.context import AppContext

_BACKUP_OPERATION = "database.backup"
_BACKUP_MEDIA_TYPE = "application/octet-stream"
_DIAGNOSTIC_COLUMNS = ("code", "status", "message", "remediation")


class DatabaseExecutor:
    def __init__(self, context: AppContext) -> None:
        self._context = context

    def __call__(self, invocation: CommandInvocation) -> CommandOutcome:
        if invocation.key.action == "diagnose":
            from ancestryllm.storage.diagnostics import diagnose_storage

            return CommandOutcome(
                table_result(
                    _DIAGNOSTIC_COLUMNS,
                    diagnose_storage(self._context.database.path, self._context.secrets),
                )
            )
        destination = path(invocation, "destination").expanduser().resolve()
        self._context.database.backup(destination)
        registry = _ArtifactRegistry()
        backup_grant = registry.grant_output(
            destination,
            operation=_BACKUP_OPERATION,
            media_type=_BACKUP_MEDIA_TYPE,
            artifact_type="encrypted_database_backup",
        )
        return CommandOutcome(
            FileArtifactResult(registry.describe_output(backup_grant, operation=_BACKUP_OPERATION))
        )


__all__ = ["DatabaseExecutor"]
