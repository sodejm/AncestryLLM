"""Strict authenticated native workbench contracts and thin HTTP routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ancestryllm.api.contracts import (
    JobSnapshotResponse,  # noqa: TC001 - FastAPI resolves runtime response types
)
from ancestryllm.application.operations import RootsMagicPresetQueryRequest

if TYPE_CHECKING:
    from collections.abc import Callable

    from ancestryllm.api.rootsmagic_workbench import NativeRootsMagicWorkbench
    from ancestryllm.application.jobs import PublicJobSnapshot

Capability = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
PersonId = Annotated[int, Field(ge=1, le=2**53 - 1)]


class VersionedRequest(BaseModel):
    """Strict version discriminator for native workbench requests."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal[1]


class SourceInspectRequest(VersionedRequest):
    """One Main-issued, single-use source capability."""

    source_capability: Capability


class PresetQueryRequest(VersionedRequest):
    """Only fixed presets and bounded parameters are accepted."""

    source_ref: Capability
    query_id: Literal["people", "family_links", "events"]
    person_id: PersonId | None = None
    name_filter: str = Field(default="", max_length=200)
    offset: int = Field(default=0, ge=0, le=1_000_000)
    page_size: int = Field(default=25, ge=1, le=100)

    @model_validator(mode="after")
    def validate_preset_parameters(self) -> PresetQueryRequest:
        """Reject parameters that do not belong to the selected preset."""
        if "\x00" in self.name_filter:
            raise ValueError("Name filter contains a null character.")
        if self.query_id != "people" and (self.person_id is None or self.name_filter):
            raise ValueError("This preset requires a person and no name filter.")
        if self.query_id == "people" and self.person_id is not None:
            raise ValueError("People preset does not accept a person parameter.")
        return self


class FolderExportRequest(VersionedRequest):
    """A rooted export into one newly authorized folder."""

    source_ref: Capability
    output_capability: Capability
    root_person_id: PersonId
    scope: Literal["connected", "ancestors", "descendants"] = "connected"
    generations: Annotated[int, Field(ge=1, le=100)] | None = None
    living: Literal["exclude", "include", "anonymize"] = "exclude"


def rootsmagic_router(
    boundary: Callable[[], NativeRootsMagicWorkbench],
    snapshot: Callable[[PublicJobSnapshot], JobSnapshotResponse],
    assert_allowed: Callable[[], None],
) -> APIRouter:
    """Create routes only when the native private mediation boundary exists."""
    router = APIRouter(prefix="/api/v1/rootsmagic", tags=["rootsmagic"])

    @router.post("/sources", operation_id="inspectInternalRootsMagicSource")
    def inspect_source(request: SourceInspectRequest) -> JobSnapshotResponse:
        assert_allowed()
        return snapshot(boundary().inspect(request.source_capability))

    @router.post("/sources/{source_ref}/discard", operation_id="discardInternalRootsMagicSource")
    def discard_source(source_ref: Capability, request: VersionedRequest) -> dict[str, int]:
        boundary().discard(source_ref)
        return {"schema_version": 1}

    @router.get("/presets", operation_id="listInternalRootsMagicPresets")
    def presets() -> dict[str, Any]:
        return boundary().presets()

    @router.post("/queries", operation_id="queryInternalRootsMagicPreset")
    def query(request: PresetQueryRequest) -> JobSnapshotResponse:
        assert_allowed()
        return snapshot(
            boundary().query(
                RootsMagicPresetQueryRequest(
                    request.source_ref,
                    request.query_id,
                    request.person_id,
                    request.name_filter,
                    request.offset,
                    request.page_size,
                )
            )
        )

    @router.post("/exports", operation_id="exportInternalRootsMagicFolder")
    def export(request: FolderExportRequest) -> JobSnapshotResponse:
        assert_allowed()
        return snapshot(
            boundary().export(
                request.source_ref,
                request.output_capability,
                root_person_id=request.root_person_id,
                scope=request.scope,
                generations=request.generations,
                living=request.living,
            )
        )

    @router.get("/jobs/{job_id}/result", operation_id="getInternalRootsMagicResult")
    def result(job_id: str) -> dict[str, Any]:
        return boundary().result(job_id)

    return router
