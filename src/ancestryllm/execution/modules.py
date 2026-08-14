"""Focused module-registry command handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ancestryllm.application.executor import CommandInvocation, CommandOutcome
from ancestryllm.application.results import SuccessResult
from ancestryllm.core.modules import ModuleRegistry
from ancestryllm.execution.common import descriptor_payload, table_result, text

if TYPE_CHECKING:
    from ancestryllm.core.context import AppContext

_MODULE_COLUMNS = (
    "module_id",
    "name",
    "summary",
    "actions",
    "implementation",
    "configuration",
    "required_services",
)


class ModulesExecutor:
    """Dispatch module-management commands through the application boundary."""

    def __init__(self, context: AppContext) -> None:
        self._registry = ModuleRegistry(context)

    def __call__(self, invocation: CommandInvocation) -> CommandOutcome:
        """Dispatch module listing or enablement changes through the registry."""
        action = invocation.key.action
        if action == "list":
            return CommandOutcome(
                table_result(
                    _MODULE_COLUMNS,
                    (descriptor_payload(item) for item in self._registry.descriptors()),
                )
            )
        if action == "enable":
            module_id = text(invocation, "module_id")
            self._registry.enable(module_id)
            result = SuccessResult(f"Enabled module: {module_id}")
        else:
            module_id = text(invocation, "module_id")
            self._registry.disable(module_id)
            result = SuccessResult(f"Disabled module: {module_id}")
        return CommandOutcome(result)


__all__ = ["ModulesExecutor"]
