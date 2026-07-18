"""Manage reusable person details without replacing the source family tree."""

from __future__ import annotations

from dataclasses import dataclass

from ancestryllm.core.errors import AncestryError
from ancestryllm.domain.models import LivingStatus
from ancestryllm.storage.database import Database
from ancestryllm.storage.repositories import ResearchRepository


@dataclass(frozen=True, slots=True)
class ResearchPerson:
    person_id: str
    display_name: str
    living_status: LivingStatus
    notes: str


class ResearchService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def add_person(
        self,
        display_name: str,
        living_status: LivingStatus = LivingStatus.UNKNOWN,
        notes: str = "",
        workspace: str = "default",
    ) -> ResearchPerson:
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
        with self.database.session() as session:
            models = ResearchRepository(session).list_people(workspace)
            return [
                ResearchPerson(
                    item.id, item.display_name, LivingStatus(item.living_status), item.notes
                )
                for item in models
            ]
