"""Focused encrypted-database maintenance command handler."""

from __future__ import annotations

from ancestryllm.application.executor import CommandInvocation, CommandOutcome
from ancestryllm.application.results import SuccessResult
from ancestryllm.core.context import AppContext
from ancestryllm.execution.common import path, structured_result


class DatabaseExecutor:
    def __init__(self, context: AppContext) -> None:
        self._context = context

    def __call__(self, invocation: CommandInvocation) -> CommandOutcome:
        if invocation.key.action == "diagnose":
            from ancestryllm.storage.diagnostics import diagnose_storage

            return CommandOutcome(
                structured_result(
                    diagnose_storage(self._context.database.path, self._context.secrets)
                )
            )
        destination = path(invocation, "destination")
        self._context.database.backup(destination.expanduser().resolve())
        return CommandOutcome(SuccessResult(f"Encrypted backup created: {destination}"))


__all__ = ["DatabaseExecutor"]
