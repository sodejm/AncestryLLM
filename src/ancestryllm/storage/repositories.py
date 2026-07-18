"""Small repository boundaries over the encrypted SQLAlchemy session."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ancestryllm.storage.models import (
    ConsentProfileModel,
    PersonModel,
    PromptTemplateModel,
    PromptVersionModel,
    ProviderProfileModel,
    WorkspaceModel,
)


@dataclass(slots=True)
class ResearchRepository:
    session: Session

    def get_or_create_workspace(self, name: str = "default") -> WorkspaceModel:
        workspace = self.session.scalar(select(WorkspaceModel).where(WorkspaceModel.name == name))
        if workspace is None:
            workspace = WorkspaceModel(name=name)
            self.session.add(workspace)
            self.session.commit()
        return workspace

    def add_person(
        self,
        display_name: str,
        living_status: str = "unknown",
        notes: str = "",
        workspace: str = "default",
    ) -> PersonModel:
        target = self.get_or_create_workspace(workspace)
        person = PersonModel(
            workspace_id=target.id,
            display_name=display_name,
            living_status=living_status,
            notes=notes,
        )
        self.session.add(person)
        self.session.commit()
        return person

    def list_people(self, workspace: str = "default") -> list[PersonModel]:
        return list(
            self.session.scalars(
                select(PersonModel)
                .join(WorkspaceModel)
                .where(WorkspaceModel.name == workspace)
                .order_by(PersonModel.display_name)
            )
        )


@dataclass(slots=True)
class PromptRepository:
    session: Session

    def save_version(
        self,
        name: str,
        purpose: str,
        body: str,
        variables: list[str],
        response_schema: dict[str, object] | None,
        tags: list[str] | None = None,
    ) -> PromptVersionModel:
        template = self.session.scalar(
            select(PromptTemplateModel).where(PromptTemplateModel.name == name)
        )
        if template is None:
            template = PromptTemplateModel(
                name=name,
                purpose=purpose,
                tags_json=json.dumps(sorted(set(tags or []))),
            )
            self.session.add(template)
            self.session.flush()
        next_version = (
            self.session.scalar(
                select(func.max(PromptVersionModel.version)).where(
                    PromptVersionModel.template_id == template.id
                )
            )
            or 0
        ) + 1
        version = PromptVersionModel(
            template_id=template.id,
            version=next_version,
            body=body,
            variables_json=json.dumps(sorted(set(variables))),
            response_schema_json=json.dumps(response_schema, sort_keys=True)
            if response_schema
            else None,
        )
        self.session.add(version)
        self.session.commit()
        return version

    def get(
        self, name: str, version: int | None = None
    ) -> tuple[PromptTemplateModel, PromptVersionModel] | None:
        template = self.session.scalar(
            select(PromptTemplateModel).where(PromptTemplateModel.name == name)
        )
        if template is None:
            return None
        query = select(PromptVersionModel).where(PromptVersionModel.template_id == template.id)
        query = (
            query.where(PromptVersionModel.version == version)
            if version is not None
            else query.order_by(PromptVersionModel.version.desc()).limit(1)
        )
        selected = self.session.scalar(query)
        return (template, selected) if selected else None

    def list_templates(self) -> list[PromptTemplateModel]:
        return list(
            self.session.scalars(select(PromptTemplateModel).order_by(PromptTemplateModel.name))
        )


@dataclass(slots=True)
class ProviderRepository:
    session: Session

    def list_profiles(self) -> list[ProviderProfileModel]:
        return list(
            self.session.scalars(select(ProviderProfileModel).order_by(ProviderProfileModel.name))
        )

    def get_profile(self, name: str) -> ProviderProfileModel | None:
        return self.session.scalar(
            select(ProviderProfileModel).where(ProviderProfileModel.name == name)
        )

    def list_consents(self) -> list[ConsentProfileModel]:
        return list(
            self.session.scalars(select(ConsentProfileModel).order_by(ConsentProfileModel.name))
        )

    def get_consent(self, name: str) -> ConsentProfileModel | None:
        return self.session.scalar(
            select(ConsentProfileModel).where(ConsentProfileModel.name == name)
        )
