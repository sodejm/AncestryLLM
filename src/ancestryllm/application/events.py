"""Transport-neutral events emitted during command execution."""

from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from ancestryllm.application.dto import JSONValue, ProgressUpdate


@runtime_checkable
class CommandEvent(Protocol):
    """One bounded event that any adapter can serialize."""

    def to_serializable(self) -> JSONValue:
        """Return this event as a strict-JSON value."""


# Preserve the established ProgressUpdate contract while naming its event role.
ProgressEvent: TypeAlias = ProgressUpdate


__all__ = ["CommandEvent", "ProgressEvent"]
