"""Focused module-registry command handler."""

from __future__ import annotations

from ancestryllm.application.executor import CommandInvocation, CommandOutcome
from ancestryllm.application.results import SuccessResult
from ancestryllm.core.context import AppContext
from ancestryllm.core.modules import ModuleRegistry
from ancestryllm.execution.common import descriptor_payload, structured_result, text


class ModulesExecutor:
    def __init__(self, context: AppContext) -> None:
        self._registry = ModuleRegistry(context)

    def __call__(self, invocation: CommandInvocation) -> CommandOutcome:
        action = invocation.key.action
        if action == "list":
            return CommandOutcome(
                structured_result(
                    [descriptor_payload(item) for item in self._registry.descriptors()]
                )
            )
        elif action == "enable":
            module_id = text(invocation, "module_id")
            self._registry.enable(module_id)
            result = SuccessResult(f"Enabled module: {module_id}")
        else:
            module_id = text(invocation, "module_id")
            self._registry.disable(module_id)
            result = SuccessResult(f"Disabled module: {module_id}")
        return CommandOutcome(result)


__all__ = ["ModulesExecutor"]
