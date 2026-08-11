"""Provider-neutral resolver contracts used by deterministic GEDCOM logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ancestryllm.gedcom.identity import IndividualRecord
    from ancestryllm.gedcom.quality import QualityReport


class DuplicateDecision(Protocol):
    """Resolve a low-confidence duplicate without owning transport interaction."""

    def __call__(self, left: IndividualRecord, right: IndividualRecord) -> bool | None: ...


class IdentityResolver(Protocol):
    """Adjudicate one bounded pair without exposing provider implementation details."""

    def __call__(self, left: IndividualRecord, right: IndividualRecord) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class QualityResolution:
    """Validated advisory annotations plus the route used to produce them."""

    annotations: Mapping[str, tuple[str, tuple[str, ...]]]
    provider_id: str
    model: str
    remote: bool


class QualityResolver(Protocol):
    """Annotate a deterministic quality report through an application service."""

    def __call__(self, report: QualityReport) -> QualityResolution: ...


__all__ = [
    "DuplicateDecision",
    "IdentityResolver",
    "QualityResolution",
    "QualityResolver",
]
