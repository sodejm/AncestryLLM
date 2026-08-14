"""Manage reusable person details without replacing the source family tree."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ancestryllm.core.errors import AncestryError
from ancestryllm.domain.models import LivingStatus
from ancestryllm.storage.repositories import ResearchRepository

if TYPE_CHECKING:
    from ancestryllm.storage.database import Database


@dataclass(frozen=True, slots=True)
class ResearchPerson:
    """Represent a person returned by the research application service."""

    person_id: str
    display_name: str
    living_status: LivingStatus
    notes: str


class ResearchService:
    """Coordinate research operations across the application boundary."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def add_person(
        self,
        display_name: str,
        living_status: LivingStatus = LivingStatus.UNKNOWN,
        notes: str = "",
        workspace: str = "default",
    ) -> ResearchPerson:
        """Persist a research person in the selected workspace."""
        if not display_name.strip():
            raise AncestryError("PERSON_NAME_REQUIRED", "A display name is required.")
        with self.database.session() as session:
            model = ResearchRepository(session).add_person(
                display_name.strip(), living_status.value, notes, workspace
            )
        return ResearchPerson(
            model.id, model.display_name, LivingStatus(model.living_status), model.notes
        )

    def list_people(self, workspace: str = "default") -> list[ResearchPerson]:
        """Return research people in deterministic display-name order."""
        with self.database.session() as session:
            models = ResearchRepository(session).list_people(workspace)
            return [
                ResearchPerson(
                    item.id, item.display_name, LivingStatus(item.living_status), item.notes
                )
                for item in models
            ]
