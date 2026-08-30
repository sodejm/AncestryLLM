"""Narrow application-service ports with no transport or provider dependency."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ancestryllm.application.dto import (
        DecisionRequest,
        DecisionResponse,
        IdentityResolutionRequest,
        IdentityResolutionResult,
        QualityResolutionRequest,
        QualityResolutionResult,
    )
    from ancestryllm.application.events import ProgressEvent
    from ancestryllm.application.operations import (
        GedcomInspectRequest,
        GedcomInspectResult,
        GedcomMergeRequest,
        GedcomMergeResult,
        GedcomQualityRequest,
        GedcomQualityResult,
        GedcomSubtreeRequest,
        GedcomSubtreeResult,
        GedcomSyncRequest,
        GedcomSyncResult,
    )


@runtime_checkable
class CancellationPort(Protocol):
    """Cooperative check at an interruptible application boundary."""

    def check_cancelled(self) -> None:
        """Raise the adapter's cancellation signal when cancellation is requested."""


@runtime_checkable
class ProgressPort(Protocol):
    """Receive bounded structural progress without private operation inputs."""

    def emit(self, event: ProgressEvent) -> None:
        """Publish one validated progress update."""


@runtime_checkable
class DecisionPort(Protocol):
    """Obtain an explicit user decision without terminal input."""

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        """Return one declared option or explicit cancellation."""


@runtime_checkable
class IdentityResolutionPort(Protocol):
    """Resolve an ambiguous identity using only opaque record references."""

    def resolve_identity(
        self,
        request: IdentityResolutionRequest,
    ) -> IdentityResolutionResult:
        """Choose an existing identity, create a new identity, or cancel."""


@runtime_checkable
class QualityResolutionPort(Protocol):
    """Resolve a coded genealogy-quality finding without framework objects."""

    def resolve_quality(
        self,
        request: QualityResolutionRequest,
    ) -> QualityResolutionResult:
        """Choose one declared quality action or cancel."""


class GedcomOperationsPort(Protocol):
    """Execute typed GEDCOM requests without exposing an adapter implementation."""

    def execute_inspect(
        self,
        request: GedcomInspectRequest,
        *,
        cancellation: CancellationPort | None = None,
    ) -> GedcomInspectResult:
        """Inspect one granted GEDCOM source."""

    def execute_merge(
        self,
        request: GedcomMergeRequest,
        *,
        cancellation: CancellationPort | None = None,
    ) -> GedcomMergeResult:
        """Merge granted GEDCOM sources into a granted output."""

    def execute_subtree(
        self,
        request: GedcomSubtreeRequest,
        *,
        cancellation: CancellationPort | None = None,
    ) -> GedcomSubtreeResult:
        """Export one rooted subtree into a granted output."""

    def execute_quality(
        self,
        request: GedcomQualityRequest,
        *,
        cancellation: CancellationPort | None = None,
    ) -> GedcomQualityResult:
        """Publish a quality report for one granted source."""

    def execute_sync(
        self,
        request: GedcomSyncRequest,
        *,
        cancellation: CancellationPort | None = None,
    ) -> GedcomSyncResult:
        """Run one typed update or rebase operation."""


class NeverCancelled:
    """Default cancellation port for synchronous operations without a job."""

    __slots__ = ()

    def check_cancelled(self) -> None:
        """Raise when cooperative cancellation has been requested."""
        return


class DiscardProgress:
    """Default progress port for adapters that do not display progress."""

    __slots__ = ()

    def emit(self, event: ProgressEvent) -> None:
        """Discard a progress event without side effects."""
        del event


__all__ = [
    "CancellationPort",
    "DecisionPort",
    "DiscardProgress",
    "GedcomOperationsPort",
    "IdentityResolutionPort",
    "NeverCancelled",
    "ProgressPort",
    "QualityResolutionPort",
]
