"""Focused write-only secret-reference command handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ancestryllm.application.executor import CommandInvocation, CommandOutcome
from ancestryllm.application.results import SuccessResult
from ancestryllm.core.errors import AncestryError
from ancestryllm.execution.common import optional_text, structured_result, text

if TYPE_CHECKING:
    from ancestryllm.application._secrets import SecretGrantRegistry
    from ancestryllm.core.context import AppContext

_DEFAULT_NAMES = (
    "openai.api_key",
    "anthropic.api_key",
    "gemini.api_key",
    "openrouter.api_key",
    "openrouter.management_key",
    "database.master_key",
)


class SecretsExecutor:
    """Dispatch secret-management commands without exposing secret values."""

    def __init__(self, context: AppContext, grants: SecretGrantRegistry) -> None:
        self._context = context
        self._grants = grants

    def __call__(self, invocation: CommandInvocation) -> CommandOutcome:
        """Dispatch secret writes, deletion, and presence checks without reading values."""
        action = invocation.key.action
        if action == "set":
            name = text(invocation, "name")
            if invocation.secret_grant is None:
                raise AncestryError(
                    "SECRET_GRANT_REQUIRED",
                    "Secret submission requires a one-time write-only capability.",
                    exit_code=2,
                )
            value = self._grants.consume(invocation.secret_grant, name)
            self._context.secrets.set(name, value)
            return CommandOutcome(SuccessResult(f"Stored secret reference: {name}"))
        if action == "delete":
            name = text(invocation, "name")
            self._context.secrets.delete(name)
            return CommandOutcome(SuccessResult(f"Deleted secret reference: {name}"))
        requested = optional_text(invocation, "name")
        names = (requested,) if requested is not None else _DEFAULT_NAMES
        return CommandOutcome(
            structured_result({name: self._context.secrets.present(name) for name in names})
        )


__all__ = ["SecretsExecutor"]
