"""SQLAlchemy models for the encrypted local research workspace."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    """Generate a new stable identifier for a persistence model."""
    return str(uuid.uuid4())


def utc_now() -> str:
    """Return the current timezone-aware UTC timestamp."""
    return dt.datetime.now(dt.UTC).isoformat()


class Base(DeclarativeBase):
    """Provide the declarative base shared by SQLAlchemy persistence models."""

    pass


class WorkspaceModel(Base):
    """Map a persisted research workspace and its related people."""

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now, nullable=False)


class PersonModel(Base):
    """Map a persisted research person and its genealogical records."""

    __tablename__ = "people"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(500), nullable=False)
    living_status: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), default=utc_now, nullable=False)
    identifiers: Mapped[list[PersonIdentifierModel]] = relationship(cascade="all, delete-orphan")
    facts: Mapped[list[FactModel]] = relationship(cascade="all, delete-orphan")

    __table_args__ = (Index("ix_people_workspace_name", "workspace_id", "display_name"),)


class PersonIdentifierModel(Base):
    """Map an external source identifier associated with a persisted person."""

    __tablename__ = "person_identifiers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    person_id: Mapped[str] = mapped_column(
        ForeignKey("people.id", ondelete="CASCADE"), nullable=False
    )
    system: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str] = mapped_column(String(500), nullable=False)
    tree_fingerprint: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        UniqueConstraint("system", "value", "tree_fingerprint", name="uq_person_source_identifier"),
    )


class FactModel(Base):
    """Map a persisted genealogical fact and its provenance fields."""

    __tablename__ = "facts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    person_id: Mapped[str] = mapped_column(
        ForeignKey("people.id", ondelete="CASCADE"), nullable=False
    )
    fact_type: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str] = mapped_column(Text, default="", nullable=False)
    date_text: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    place: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    provenance_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)


class RelationshipModel(Base):
    """Map a persisted relationship between two people."""

    __tablename__ = "relationships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_person_id: Mapped[str] = mapped_column(
        ForeignKey("people.id", ondelete="CASCADE"), nullable=False
    )
    target_person_id: Mapped[str] = mapped_column(
        ForeignKey("people.id", ondelete="CASCADE"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(String(100), nullable=False)
    provenance_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "source_person_id", "target_person_id", "relationship_type", name="uq_relationship"
        ),
    )


class PromptTemplateModel(Base):
    """Map a saved prompt template and its immutable versions."""

    __tablename__ = "prompt_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    purpose: Mapped[str] = mapped_column(String(200), nullable=False)
    tags_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now, nullable=False)
    versions: Mapped[list[PromptVersionModel]] = relationship(cascade="all, delete-orphan")


class PromptVersionModel(Base):
    """Map one immutable version of a saved prompt template."""

    __tablename__ = "prompt_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    template_id: Mapped[str] = mapped_column(
        ForeignKey("prompt_templates.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    variables_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    response_schema_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now, nullable=False)

    __table_args__ = (UniqueConstraint("template_id", "version", name="uq_prompt_version"),)


class ProviderProfileModel(Base):
    """Map a provider profile without storing credential material."""

    __tablename__ = "provider_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    provider_id: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(300), nullable=False)
    secret_reference: Mapped[str | None] = mapped_column(String(300))
    settings_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ConsentProfileModel(Base):
    """Map the explicit data-disclosure consent for a provider profile."""

    __tablename__ = "consent_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    provider_profile_id: Mapped[str] = mapped_column(
        ForeignKey("provider_profiles.id", ondelete="CASCADE"), nullable=False
    )
    allowed_modules_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    allowed_purposes_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    allowed_data_classes_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    model_allowlist_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    max_cost_usd: Mapped[float | None] = mapped_column(Float)
    retain_payloads: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now, nullable=False)
    revoked_at: Mapped[str | None] = mapped_column(String(40))


class LlmRunModel(Base):
    """Map sanitized metadata for one LLM generation run."""

    __tablename__ = "llm_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    prompt_version_id: Mapped[str | None] = mapped_column(ForeignKey("prompt_versions.id"))
    consent_profile_id: Mapped[str | None] = mapped_column(ForeignKey("consent_profiles.id"))
    provider_id: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(300), nullable=False)
    purpose: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_hash: Mapped[str | None] = mapped_column(String(64))
    input_payload: Mapped[str | None] = mapped_column(Text)
    output_payload: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[str] = mapped_column(String(40), default=utc_now, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String(40))


class JobModel(Base):
    """Latest sanitized snapshot for one restart-safe background job."""

    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(13), primary_key=True)
    job_number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_at: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_jobs_number", "job_number"),
        Index("ix_jobs_state", "state"),
    )


class JobEventModel(Base):
    """One bounded, replayable sanitized event for a background job."""

    __tablename__ = "job_events"

    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.job_id", ondelete="CASCADE"),
        primary_key=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    event_json: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ix_job_events_replay", "job_id", "sequence"),)
