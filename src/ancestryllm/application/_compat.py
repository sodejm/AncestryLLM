"""Private compatibility adapters for current CLI/REPL infrastructure.

These shims are intentionally not part of the public application contract.
They let the shared executor consume the current cancellation and job-progress
objects while those implementations remain in ``core``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ancestryllm.core.cancellation import CancellationError
from ancestryllm.domain.errors import DomainFailure, DomainFailureCode

if TYPE_CHECKING:
    from ancestryllm.application.events import ProgressEvent


class _LegacyCancellation(Protocol):
    def check_cancelled(self) -> None: ...


class _LegacyReporter(_LegacyCancellation, Protocol):
    def update(
        self,
        operation: str,
        *,
        completed: int | None = None,
        total: int | None = None,
    ) -> None: ...


class _CurrentCancellationAdapter:
    """Adapt the current job/token cancellation method to ``CancellationPort``."""

    __slots__ = ("_source",)

    def __init__(self, source: _LegacyCancellation) -> None:
        self._source = source

    def check_cancelled(self) -> None:
        """Translate legacy cancellation into the stable coded domain failure."""
        try:
            self._source.check_cancelled()
        except CancellationError as exc:
            raise DomainFailure(DomainFailureCode.CANCELLED) from exc


class _CurrentProgressAdapter:
    """Adapt bounded application progress to the current job reporter."""

    __slots__ = ("_reporter",)

    def __init__(self, reporter: _LegacyReporter) -> None:
        self._reporter = reporter

    def emit(self, event: ProgressEvent) -> None:
        """Forward bounded progress through the legacy reporter contract."""
        self._reporter.update(
            f"{event.operation}.{event.stage}",
            completed=event.completed,
            total=event.total,
        )


__all__ = ["_CurrentCancellationAdapter", "_CurrentProgressAdapter"]
