"""Provider- and tool-independent genealogy value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "Citation",
    "Fact",
    "LivingStatus",
    "Person",
    "PersonName",
    "Provenance",
    "Relationship",
    "SourceIdentifier",
]


class LivingStatus(StrEnum):
    """Conservative living status used by privacy policy."""

    LIVING = "living"
    DECEASED = "deceased"
    POSSIBLY_LIVING = "possibly_living"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PersonName:
    """Represent the structured name fields associated with a person."""

    given: str = ""
    surname: str = ""
    prefix: str = ""
    suffix: str = ""
    nickname: str = ""
    name_type: str = "birth"
    primary: bool = True

    @property
    def display(self) -> str:
        """Render the person name as a human-readable name."""
        return " ".join(
            value for value in (self.prefix, self.given, self.surname, self.suffix) if value
        )


@dataclass(frozen=True, slots=True)
class SourceIdentifier:
    """Identify a person or record within an immutable source system."""

    system: str
    value: str
    tree_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class Provenance:
    """Record where a genealogical fact originated and how it was imported."""

    source_type: str
    source_reference: str
    captured_at: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Citation:
    """Associate a source reference and note with a genealogical claim."""

    title: str = ""
    page: str = ""
    text: str = ""
    repository: str = ""
    provenance: Provenance | None = None


@dataclass(frozen=True, slots=True)
class Fact:
    """Represent a dated genealogical fact with provenance and place data."""

    fact_type: str
    value: str = ""
    date: str = ""
    place: str = ""
    confidence: float | None = None
    citations: tuple[Citation, ...] = ()
    provenance: Provenance | None = None


@dataclass(frozen=True, slots=True)
class Relationship:
    """Represent a typed relationship between two people."""

    source_person_id: str
    target_person_id: str
    relationship_type: str
    provenance: Provenance | None = None


@dataclass(frozen=True, slots=True)
class Person:
    """Represent a person and their loss-minimal genealogical attributes."""

    person_id: str
    names: tuple[PersonName, ...]
    living_status: LivingStatus = LivingStatus.UNKNOWN
    facts: tuple[Fact, ...] = ()
    identifiers: tuple[SourceIdentifier, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def display_name(self) -> str:
        """Render the person's structured name for presentation."""
        primary = next((name for name in self.names if name.primary), None)
        return (primary or (self.names[0] if self.names else PersonName())).display
