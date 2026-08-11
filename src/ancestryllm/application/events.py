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
# This must remain a callable class alias; a PEP 695 TypeAliasType is not callable.
ProgressEvent: TypeAlias = ProgressUpdate  # noqa: UP040


__all__ = ["CommandEvent", "ProgressEvent"]
