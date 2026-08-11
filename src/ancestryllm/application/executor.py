"""Shared command execution contracts used by every transport adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Protocol, TypeAlias, cast

from ancestryllm.application.results import CommandResult, StructuredResult
from ancestryllm.core.errors import AncestryError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ancestryllm.application.dto import SecretGrantRef
    from ancestryllm.core.commands import DispatchKey

CommandScalar: TypeAlias = str | int | float | bool | None  # noqa: UP040 - public facade
CommandValue: TypeAlias = CommandScalar | tuple[str, ...]  # noqa: UP040 - public facade
_MISSING: Final = object()


@dataclass(frozen=True, slots=True)
class CommandArgument:
    """One named, transport-neutral command argument."""

    name: str
    value: CommandValue

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("_", "").isalnum():
            raise ValueError("Command argument names must be non-empty identifiers.")
        if isinstance(self.value, tuple) and not all(isinstance(item, str) for item in self.value):
            raise TypeError("Command argument sequences may contain strings only.")


@dataclass(frozen=True, slots=True)
class CommandInvocation:
    """A parsed command with no parser, presentation, or host-path objects."""

    key: DispatchKey
    arguments: tuple[CommandArgument, ...] = ()
    json_output: bool = False
    secret_grant: SecretGrantRef | None = None

    def __post_init__(self) -> None:
        names = tuple(argument.name for argument in self.arguments)
        if len(names) != len(set(names)):
            raise ValueError("Command invocation argument names must be unique.")

    def argument(
        self,
        name: str,
        default: CommandValue | object = _MISSING,
    ) -> CommandValue:
        """Return one argument or a stable coded error when it is absent."""

        for argument in self.arguments:
            if argument.name == name:
                return argument.value
        if default is not _MISSING:
            return cast("CommandValue", default)
        raise AncestryError(
            "ARGUMENT_INVALID",
            f"Missing required command argument: {name}.",
            exit_code=2,
        )


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """Presentation-neutral command output and process status."""

    result: CommandResult = field(default_factory=lambda: StructuredResult(None))
    exit_code: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.result, CommandResult):
            raise TypeError("Command outcomes require a declared CommandResult.")
        if not 0 <= self.exit_code <= 255:
            raise ValueError("Command exit codes must be between 0 and 255.")


class CommandHandler(Protocol):
    """Execute one transport-neutral command invocation."""

    def __call__(self, invocation: CommandInvocation) -> CommandOutcome: ...


class CommandExecutor:
    """Resolve stable dispatch metadata through one immutable handler registry."""

    def __init__(
        self,
        handlers: Iterable[tuple[DispatchKey, CommandHandler]],
    ) -> None:
        resolved: dict[DispatchKey, CommandHandler] = {}
        for key, handler in handlers:
            if key in resolved:
                raise ValueError(f"Duplicate command handler registration: {key}.")
            resolved[key] = handler
        self._handlers = resolved

    @property
    def dispatch_keys(self) -> tuple[DispatchKey, ...]:
        """Return registered keys in deterministic order for diagnostics."""

        return tuple(sorted(self._handlers))

    def execute(self, invocation: CommandInvocation) -> CommandOutcome:
        """Execute one command or return the established coded unknown-command error."""

        handler = self._handlers.get(invocation.key)
        if handler is None:
            raise AncestryError(
                "COMMAND_UNKNOWN",
                f"Unknown command action: {invocation.key}.",
                exit_code=2,
            )
        return handler(invocation)


__all__ = [
    "CommandArgument",
    "CommandExecutor",
    "CommandHandler",
    "CommandInvocation",
    "CommandOutcome",
    "CommandResult",
    "CommandScalar",
    "CommandValue",
]
