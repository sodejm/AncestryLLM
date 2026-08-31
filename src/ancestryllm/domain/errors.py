"""Pure genealogy/application failure types without transport dependencies."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

DomainScalar = str | int | float | bool | None


class DomainFailureCode(StrEnum):
    """Complete stable categories emitted by the 0.3 application boundary."""

    CANCELLED = "CANCELLED"
    INVALID_REQUEST = "INVALID_REQUEST"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    DECISION_REQUIRED = "DECISION_REQUIRED"
    IDENTITY_AMBIGUOUS = "IDENTITY_AMBIGUOUS"
    QUALITY_REJECTED = "QUALITY_REJECTED"
    ARTIFACT_INVALID = "ARTIFACT_INVALID"
    ARTIFACT_FORBIDDEN = "ARTIFACT_FORBIDDEN"
    ARTIFACT_TOO_LARGE = "ARTIFACT_TOO_LARGE"
    PUBLICATION_FAILED = "PUBLICATION_FAILED"
    PROVIDER_CONSENT_REQUIRED = "PROVIDER_CONSENT_REQUIRED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INTERNAL = "INTERNAL"


@dataclass(frozen=True, slots=True)
class DomainFailureDetail:
    """Safe scalar failure metadata owned by the domain layer."""

    name: str
    value: DomainScalar

    def __post_init__(self) -> None:
        if not 1 <= len(self.name) <= 96:
            raise ValueError("domain failure detail names must be bounded")
        if not all(character.isalnum() or character in "._:-" for character in self.name):
            raise ValueError("domain failure detail names must use stable code characters")
        if isinstance(self.value, str) and (
            len(self.value) > 256
            or any(marker in self.value for marker in ("/", "\\", "\n", "\r", "\x00"))
        ):
            raise ValueError("domain failure detail values must be bounded and path-free")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("domain failure detail numbers must be finite")


@dataclass(slots=True)
class DomainFailure(Exception):
    """A safe domain failure identified by code and allowlisted scalar metadata."""

    code: DomainFailureCode
    details: tuple[DomainFailureDetail, ...] = ()

    def __str__(self) -> str:
        return self.code.value


__all__ = ["DomainFailure", "DomainFailureCode", "DomainFailureDetail", "DomainScalar"]
