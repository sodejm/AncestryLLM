"""Built-in module descriptors and enablement registry."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ancestryllm.core.commands import (
    BUILTIN_MODULES as BUILTIN_MODULES,
)
from ancestryllm.core.commands import (
    COMMAND_SPECIFICATIONS as COMMAND_SPECIFICATIONS,
)
from ancestryllm.core.commands import (
    GLOBAL_ARGUMENTS as GLOBAL_ARGUMENTS,
)
from ancestryllm.core.commands import (
    ActionSpec as ActionSpec,
)
from ancestryllm.core.commands import (
    ArgumentAction as ArgumentAction,
)
from ancestryllm.core.commands import (
    ArgumentCardinality as ArgumentCardinality,
)
from ancestryllm.core.commands import (
    ArgumentDefault as ArgumentDefault,
)
from ancestryllm.core.commands import (
    ArgumentSpec as ArgumentSpec,
)
from ancestryllm.core.commands import (
    ArgumentType as ArgumentType,
)
from ancestryllm.core.commands import (
    CommandRoute as CommandRoute,
)
from ancestryllm.core.commands import (
    CommandSpec as CommandSpec,
)
from ancestryllm.core.commands import (
    CompletionKind as CompletionKind,
)
from ancestryllm.core.commands import (
    DispatchKey as DispatchKey,
)
from ancestryllm.core.commands import (
    ExclusiveArgumentGroup as ExclusiveArgumentGroup,
)
from ancestryllm.core.commands import (
    ModuleDescriptor as ModuleDescriptor,
)

if TYPE_CHECKING:
    from ancestryllm.core.context import AppContext

__all__ = [
    "BUILTIN_MODULES",
    "COMMAND_SPECIFICATIONS",
    "GLOBAL_ARGUMENTS",
    "ActionSpec",
    "ArgumentAction",
    "ArgumentCardinality",
    "ArgumentDefault",
    "ArgumentSpec",
    "ArgumentType",
    "CommandRoute",
    "CommandSpec",
    "CompletionKind",
    "DispatchKey",
    "ExclusiveArgumentGroup",
    "ModuleDescriptor",
    "ModuleRegistry",
    "ToolModule",
]


class ToolModule(Protocol):
    """Minimum contract implemented by each built-in console module."""

    context: AppContext
    descriptor: ModuleDescriptor


class ModuleRegistry:
    """Manage explicit built-in enablement without importing implementations."""

    def __init__(self, context: AppContext) -> None:
        self.context = context

    def descriptors(self) -> list[ModuleDescriptor]:
        """Return the descriptors exposed by the module registry."""
        return [
            BUILTIN_MODULES[name]
            for name in sorted(self.context.config.enabled_modules)
            if name in BUILTIN_MODULES
        ]

    def load(self) -> list[ModuleDescriptor]:
        """Return enabled descriptors without importing obsolete console adapters."""

        return self.descriptors()

    def enable(self, module_id: str) -> None:
        """Enable a registered module by its stable identifier."""
        if module_id not in BUILTIN_MODULES:
            raise KeyError(module_id)
        self.context.config.enabled_modules.add(module_id)
        self.context.config.save()

    def disable(self, module_id: str) -> None:
        """Disable a registered module by its stable identifier."""
        self.context.config.enabled_modules.discard(module_id)
        self.context.config.save()
