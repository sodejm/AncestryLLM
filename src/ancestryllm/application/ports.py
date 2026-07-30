"""Narrow application-service ports with no transport or provider dependency."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ancestryllm.application.dto import (
    DecisionRequest,
    DecisionResponse,
    IdentityResolutionRequest,
    IdentityResolutionResult,
    ProgressUpdate,
    QualityResolutionRequest,
    QualityResolutionResult,
)


@runtime_checkable
class CancellationPort(Protocol):
    """Cooperative check at an interruptible application boundary."""

    def check_cancelled(self) -> None:
        """Raise the adapter's cancellation signal when cancellation is requested."""


@runtime_checkable
class ProgressPort(Protocol):
    """Receive bounded structural progress without private operation inputs."""

    def emit(self, update: ProgressUpdate) -> None:
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


class NeverCancelled:
    """Default cancellation port for synchronous operations without a job."""

    __slots__ = ()

    def check_cancelled(self) -> None:
        return


class DiscardProgress:
    """Default progress port for adapters that do not display progress."""

    __slots__ = ()

    def emit(self, update: ProgressUpdate) -> None:
        del update


__all__ = [
    "CancellationPort",
    "DecisionPort",
    "DiscardProgress",
    "IdentityResolutionPort",
    "NeverCancelled",
    "ProgressPort",
    "QualityResolutionPort",
]
