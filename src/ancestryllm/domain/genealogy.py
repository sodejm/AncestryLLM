"""Deterministic genealogy identity, change, quality, and provenance values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


def _validate_stable_value(label: str, value: str, *, maximum: int = 128) -> None:
    if not 1 <= len(value) <= maximum:
        raise ValueError(f"{label} length is outside its bounded range.")
    if not all(character.isalnum() or character in "._:-" for character in value):
        raise ValueError(f"{label} contains unsupported characters.")
    if any(marker in value for marker in ("/", "\\", "..", "\n", "\r", "\x00")):
        raise ValueError(f"{label} must not contain paths or control characters.")


def _canonical_refs(label: str, values: tuple[str, ...]) -> tuple[str, ...]:
    canonical = tuple(sorted(set(values)))
    if not canonical:
        raise ValueError(f"{label} must contain at least one reference.")
    for value in canonical:
        _validate_stable_value(label, value)
    return canonical


class ChangeKind(StrEnum):
    """Mutually exclusive deterministic change dispositions."""

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    CONFLICT = "conflict"


class QualityKind(StrEnum):
    """Stable quality finding categories returned by genealogy services."""

    INFORMATION = "information"
    WARNING = "warning"
    ERROR = "error"
    RESOLVED = "resolved"


@dataclass(frozen=True, slots=True)
class GenealogyIdentity:
    """One canonical genealogy identity and every equivalent source identity."""

    canonical_ref: str
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_stable_value("canonical_ref", self.canonical_ref)
        object.__setattr__(
            self,
            "source_refs",
            _canonical_refs("source_refs", self.source_refs),
        )


@dataclass(frozen=True, slots=True)
class GenealogyChange:
    """One final change disposition with zero or more coded warnings."""

    subject_ref: str
    kind: ChangeKind
    warning_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_stable_value("subject_ref", self.subject_ref)
        warning_codes = tuple(sorted(set(self.warning_codes)))
        for code in warning_codes:
            _validate_stable_value("warning_code", code, maximum=96)
        object.__setattr__(self, "warning_codes", warning_codes)


@dataclass(frozen=True, slots=True)
class GenealogyQualityFinding:
    """One coded quality finding for an opaque genealogy subject."""

    subject_ref: str
    kind: QualityKind
    rule_code: str

    def __post_init__(self) -> None:
        _validate_stable_value("subject_ref", self.subject_ref)
        _validate_stable_value("rule_code", self.rule_code, maximum=96)


@dataclass(frozen=True, slots=True)
class GenealogyProvenance:
    """One coded derivation from source identities to a result identity."""

    result_ref: str
    source_refs: tuple[str, ...]
    rule_code: str

    def __post_init__(self) -> None:
        _validate_stable_value("result_ref", self.result_ref)
        object.__setattr__(
            self,
            "source_refs",
            _canonical_refs("source_refs", self.source_refs),
        )
        _validate_stable_value("rule_code", self.rule_code, maximum=96)


__all__ = [
    "ChangeKind",
    "GenealogyChange",
    "GenealogyIdentity",
    "GenealogyProvenance",
    "GenealogyQualityFinding",
    "QualityKind",
]
