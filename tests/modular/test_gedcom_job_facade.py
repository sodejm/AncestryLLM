"""Asynchronous transport-neutral GEDCOM job facade coverage."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn, cast

import pytest

from ancestryllm.application._artifacts import _ArtifactRegistry
from ancestryllm.application.dto import ArtifactStatus
from ancestryllm.application.gedcom_jobs import GedcomJobFacade
from ancestryllm.application.jobs import (
    JobLifecycleService,
    JobLifecycleState,
    MemoryJobEventRepository,
)
from ancestryllm.application.operations import GedcomInspectRequest, GedcomInspectResult
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.jobs import JobManager
from ancestryllm.domain.errors import DomainFailure, DomainFailureCode
from ancestryllm.gedcom.service import GedcomService

if TYPE_CHECKING:
    from pathlib import Path

    from ancestryllm.application.ports import GedcomOperationsPort


def _write_person(path: Path) -> None:
    path.write_text(
        "\n".join(
            (
                "0 HEAD",
                "1 SOUR AncestryLLM-Fictional-Job-Contract",
                "1 GEDC",
                "2 VERS 5.5.5",
                "1 CHAR UTF-8",
                "0 @I1@ INDI",
                "1 NAME Ada /Example/",
                "0 TRLR",
                "",
            )
        ),
        encoding="utf-8",
    )


class _FailingGedcomService:
    """Minimal typed test double for failures crossing the job boundary."""

    def __init__(self, failure: DomainFailure) -> None:
        self._failure = failure

    def execute_inspect(
        self,
        request: GedcomInspectRequest,
        *,
        cancellation: object | None = None,
    ) -> NoReturn:
        del request, cancellation
        raise self._failure


def _failing_inspect_job(
    tmp_path: Path,
    failure_code: DomainFailureCode,
) -> tuple[JobLifecycleService, GedcomJobFacade, GedcomInspectRequest]:
    source = tmp_path / "fictional-family.ged"
    _write_person(source)
    artifacts = _ArtifactRegistry()
    request = GedcomInspectRequest(
        source=artifacts.grant_input(
            source,
            operation="gedcom.inspect",
            media_type="text/vnd.gedcom",
            artifact_type="gedcom",
        )
    )
    jobs = JobLifecycleService(
        JobManager(max_workers=1, max_pending=1),
        MemoryJobEventRepository(),
    )
    facade = GedcomJobFacade(
        service=cast(
            "GedcomOperationsPort",
            _FailingGedcomService(DomainFailure(failure_code)),
        ),
        jobs=jobs,
    )
    return jobs, facade, request


def test_inspect_job_publishes_only_opaque_lifecycle_and_typed_result(
    tmp_path: Path,
) -> None:
    source = tmp_path / "fictional-family.ged"
    _write_person(source)
    artifacts = _ArtifactRegistry()
    request = GedcomInspectRequest(
        source=artifacts.grant_input(
            source,
            operation="gedcom.inspect",
            media_type="text/vnd.gedcom",
            artifact_type="gedcom",
        )
    )
    jobs = JobLifecycleService(
        JobManager(max_workers=1, max_pending=1),
        MemoryJobEventRepository(),
    )
    facade = GedcomJobFacade(
        service=GedcomService(artifacts=artifacts),
        jobs=jobs,
    )

    try:
        submitted = facade.submit_inspect(request)
        jobs.manager.wait(submitted.job_id, timeout=5)
        completed = jobs.get(submitted.job_id)
        result = facade.result(submitted.job_id)
    finally:
        jobs.close()

    assert submitted.state in {JobLifecycleState.QUEUED, JobLifecycleState.RUNNING}
    assert completed.state is JobLifecycleState.COMPLETED
    assert len(completed.resource_refs) == 1
    assert completed.resource_refs[0].startswith("resource_")
    assert completed.artifact is None
    assert isinstance(result, GedcomInspectResult)
    assert result.summary.source.status is ArtifactStatus.READY
    assert result.summary.individual_count == 1
    serialized = completed.to_json() + result.to_json()
    assert str(tmp_path) not in serialized
    assert source.name not in serialized
    assert "@I1@" not in serialized
    assert "Ada" not in serialized


def test_completed_job_restored_after_restart_has_stable_unavailable_result(
    tmp_path: Path,
) -> None:
    source = tmp_path / "fictional-family.ged"
    _write_person(source)
    artifacts = _ArtifactRegistry()
    request = GedcomInspectRequest(
        source=artifacts.grant_input(
            source,
            operation="gedcom.inspect",
            media_type="text/vnd.gedcom",
            artifact_type="gedcom",
        )
    )
    repository = MemoryJobEventRepository()
    first_jobs = JobLifecycleService(
        JobManager(max_workers=1, max_pending=1),
        repository,
    )
    first_facade = GedcomJobFacade(
        service=GedcomService(artifacts=artifacts),
        jobs=first_jobs,
    )

    try:
        submitted = first_facade.submit_inspect(request)
        first_jobs.manager.wait(submitted.job_id, timeout=5)
        assert first_jobs.get(submitted.job_id).state is JobLifecycleState.COMPLETED
    finally:
        first_jobs.close()

    restored_jobs = JobLifecycleService(
        JobManager(max_workers=1, max_pending=1),
        repository,
    )
    restored_facade = GedcomJobFacade(
        service=GedcomService(artifacts=artifacts),
        jobs=restored_jobs,
    )
    try:
        with pytest.raises(AncestryError) as unavailable:
            restored_facade.result(submitted.job_id)
        with pytest.raises(AncestryError) as unknown:
            restored_facade.result("j999999")
    finally:
        restored_jobs.close()

    assert unavailable.value.code == "GEDCOM_JOB_RESULT_UNAVAILABLE"
    assert unknown.value.code == "JOB_NOT_FOUND"


def test_job_facade_preserves_stable_domain_failure_codes(tmp_path: Path) -> None:
    jobs, facade, request = _failing_inspect_job(
        tmp_path,
        DomainFailureCode.INVALID_REQUEST,
    )

    try:
        submitted = facade.submit_inspect(request)
        jobs.manager.wait(submitted.job_id, timeout=5)
        failed = jobs.get(submitted.job_id)
    finally:
        jobs.close()

    assert failed.state is JobLifecycleState.FAILED
    assert failed.error_code == "REQUEST_INVALID"
    assert failed.error_message == "The operation request is invalid."
    assert str(tmp_path) not in failed.to_json()


def test_job_facade_preserves_domain_cancellation_state(tmp_path: Path) -> None:
    jobs, facade, request = _failing_inspect_job(
        tmp_path,
        DomainFailureCode.CANCELLED,
    )

    try:
        submitted = facade.submit_inspect(request)
        jobs.manager.wait(submitted.job_id, timeout=5)
        cancelled = jobs.get(submitted.job_id)
    finally:
        jobs.close()

    assert cancelled.state is JobLifecycleState.CANCELLED
    assert cancelled.error_code == "JOB_CANCELLED"
    assert str(tmp_path) not in cancelled.to_json()
