"""Shared terminal composition for transport-neutral command execution."""

from __future__ import annotations

import argparse
from typing import Callable

from ancestryllm.application._secrets import SecretGrantRegistry
from ancestryllm.application.executor import CommandOutcome
from ancestryllm.application.ports import ProgressPort
from ancestryllm.application.results import CommandResult
from ancestryllm.core.context import AppContext
from ancestryllm.core.errors import AncestryError
from ancestryllm.execution.runtime import create_command_executor
from ancestryllm.terminal.parser import invocation_from_namespace
from ancestryllm.terminal.presentation import PresentationAdapter

Emit = Callable[[CommandResult, bool], None]


def _emit(result: CommandResult, json_output: bool = False) -> None:
    PresentationAdapter().render(result, json_output=json_output)


def dispatch(
    namespace: argparse.Namespace,
    context: AppContext,
    *,
    emit: Emit = _emit,
    secret_value: str | None = None,
    progress: ProgressPort | None = None,
) -> int:
    """Translate terminal state once and execute it through the shared registry."""

    grants = SecretGrantRegistry()
    try:
        secret_grant = None
        if secret_value is not None:
            command = getattr(namespace, "command", None)
            action = getattr(namespace, "action", None)
            name = getattr(namespace, "name", None)
            if command != "secrets" or action != "set" or not isinstance(name, str):
                raise AncestryError(
                    "ARGUMENT_INVALID",
                    "A secret value may only accompany a secrets set invocation.",
                    exit_code=2,
                )
            secret_grant = grants.issue(name, secret_value)

        invocation = invocation_from_namespace(
            namespace,
            secret_grant=secret_grant,
        )
        outcome: CommandOutcome = create_command_executor(
            context,
            grants,
            progress=progress,
        ).execute(invocation)
        emit(outcome.result, invocation.json_output)
        return outcome.exit_code
    finally:
        grants.revoke_all()


__all__ = ["dispatch"]
