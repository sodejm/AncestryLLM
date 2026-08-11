"""Runtime composition for the complete shared command-executor registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ancestryllm.application.executor import (
    CommandExecutor,
    CommandHandler,
    CommandInvocation,
    CommandOutcome,
)
from ancestryllm.core.commands import BUILTIN_MODULES, COMMAND_SPECIFICATIONS, DispatchKey
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.ingress import FileIngressPolicy
from ancestryllm.execution.database import DatabaseExecutor
from ancestryllm.execution.gedcom import GedcomExecutor
from ancestryllm.execution.modules import ModulesExecutor
from ancestryllm.execution.ocr import OcrExecutor
from ancestryllm.execution.people import PeopleExecutor
from ancestryllm.execution.prompts import PromptsExecutor
from ancestryllm.execution.providers import ProvidersExecutor
from ancestryllm.execution.rootsmagic import RootsMagicExecutor
from ancestryllm.execution.secrets import SecretsExecutor

if TYPE_CHECKING:
    from ancestryllm.application._secrets import SecretGrantRegistry
    from ancestryllm.application.ports import ProgressPort
    from ancestryllm.core.context import AppContext


class _EnabledModuleExecutor:
    def __init__(
        self,
        context: AppContext,
        module_id: str,
        handler: CommandHandler,
    ) -> None:
        self._context = context
        self._module_id = module_id
        self._handler = handler

    def __call__(self, invocation: CommandInvocation) -> CommandOutcome:
        if self._module_id not in self._context.config.enabled_modules:
            raise AncestryError(
                "MODULE_DISABLED",
                f"Module is not enabled: {self._module_id}.",
                remediation=(
                    "Enable it with `ancestry modules enable MODULE` before running an action."
                ),
                exit_code=2,
            )
        return self._handler(invocation)


def create_command_executor(
    context: AppContext,
    grants: SecretGrantRegistry,
    *,
    progress: ProgressPort | None = None,
) -> CommandExecutor:
    """Compose one handler per family from the single command specification."""

    ingress = FileIngressPolicy(context.config.file_ingress)
    families: dict[str, CommandHandler] = {
        "modules": ModulesExecutor(context),
        "rootsmagic": RootsMagicExecutor(context, progress=progress),
        "gedcom": GedcomExecutor(context, ingress),
        "prompts": PromptsExecutor(context, ingress),
        "people": PeopleExecutor(context),
        "providers": ProvidersExecutor(context),
        "secrets": SecretsExecutor(context, grants),
        "ocr": OcrExecutor(context, ingress),
        "database": DatabaseExecutor(context),
    }
    registrations: list[tuple[DispatchKey, CommandHandler]] = []
    for command_name, specification in COMMAND_SPECIFICATIONS.items():
        handler = families[command_name]
        if command_name in BUILTIN_MODULES:
            handler = _EnabledModuleExecutor(context, command_name, handler)
        registrations.extend((route.key, handler) for route in specification.routes)
    return CommandExecutor(registrations)


__all__ = ["create_command_executor"]
