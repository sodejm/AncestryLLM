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
    serialized = completed.to_json() + result.summary_result().to_json()
    assert str(tmp_path) not in serialized
    assert source.name not in serialized
    assert "@I1@" not in serialized
    assert "Ada" not in serialized


def test_root_candidate_pages_are_searchable_bounded_and_bound_to_inspection(
    tmp_path: Path,
) -> None:
    source = tmp_path / "fictional-family.ged"
    source.write_text(
        "0 HEAD\n1 GEDC\n2 VERS 5.5.5\n1 CHAR UTF-8\n"
        "0 @I1@ INDI\n1 NAME Ada /Example/\n1 BIRT\n2 DATE 1900\n"
        "0 @I2@ INDI\n1 NAME Ada /Example/\n1 BIRT\n2 DATE 1920\n"
        "0 @I3@ INDI\n1 NAME Grace /Example/\n0 TRLR\n",
        encoding="utf-8",
    )
    artifacts = _ArtifactRegistry()
    request = GedcomInspectRequest(
        source=artifacts.grant_input(
            source,
            operation="gedcom.inspect",
            media_type="text/vnd.gedcom",
            artifact_type="gedcom",
        )
    )
    jobs = JobLifecycleService(JobManager(max_workers=1), MemoryJobEventRepository())
    facade = GedcomJobFacade(service=GedcomService(artifacts=artifacts), jobs=jobs)
    try:
        first_job = facade.submit_inspect(request).job_id
        jobs.manager.wait(first_job, timeout=5)
        empty = facade.root_candidates(first_job, query="", limit=2)
        assert empty.total_count == 3
        assert len(empty.candidates) == 2
        assert empty.next_cursor is not None
        empty_last = facade.root_candidates(
            first_job,
            query="",
            limit=2,
            cursor=empty.next_cursor,
        )
        assert empty_last.total_count == 3
        assert len(empty_last.candidates) == 1
        assert empty_last.next_cursor is None
        first = facade.root_candidates(first_job, query="ADA", limit=1)
        assert first.total_count == 2
        assert len(first.candidates) == 1
        assert first.candidates[0].display_name == "Ada Example"
        assert first.candidates[0].source_identifier == "@I1@"
        assert first.candidates[0].birth_date == "1900"
        assert first.next_cursor is not None
        second = GedcomJobFacade(service=GedcomService(artifacts=artifacts), jobs=jobs)
        last = second.root_candidates(first_job, query="ADA", limit=1, cursor=first.next_cursor)
        assert last.candidates[0].source_identifier == "@I2@"
        assert last.candidates[0].birth_date == "1920"
        assert last.candidates[0].person_ref != first.candidates[0].person_ref
        assert last.next_cursor is None
        assert facade.root_candidates(first_job, query="not present").candidates == ()
        assert facade.root_candidates(first_job, query="@I3@").total_count == 1

        other_job = facade.submit_inspect(request).job_id
        jobs.manager.wait(other_job, timeout=5)
        for job_id, query, cursor in (
            (other_job, "ADA", first.next_cursor),
            (first_job, "Grace", first.next_cursor),
            (first_job, "ADA", first.next_cursor[:-1] + "!"),
        ):
            with pytest.raises(AncestryError) as invalid:
                facade.root_candidates(job_id, query=query, cursor=cursor)
            assert invalid.value.code == "GEDCOM_ROOT_CURSOR_INVALID"
        assert "Ada" not in jobs.get(first_job).to_json()
        assert str(source) not in first.to_json()
    finally:
        jobs.close()


def test_pointerless_individuals_keep_distinct_root_candidate_refs(
    tmp_path: Path,
) -> None:
    source = tmp_path / "fictional-pointerless.ged"
    source.write_text(
        "0 HEAD\n1 GEDC\n2 VERS 5.5.5\n1 CHAR UTF-8\n"
        "0 INDI\n1 NAME Ada /Example/\n"
        "0 INDI\n1 NAME Ada /Example/\n"
        "0 TRLR\n",
        encoding="utf-8",
    )
    artifacts = _ArtifactRegistry()
    request = GedcomInspectRequest(
        source=artifacts.grant_input(
            source,
            operation="gedcom.inspect",
            media_type="text/vnd.gedcom",
            artifact_type="gedcom",
        )
    )
    jobs = JobLifecycleService(JobManager(max_workers=1), MemoryJobEventRepository())
    facade = GedcomJobFacade(service=GedcomService(artifacts=artifacts), jobs=jobs)
    try:
        job_id = facade.submit_inspect(request).job_id
        jobs.manager.wait(job_id, timeout=5)
        page = facade.root_candidates(job_id, query="", limit=25)
        assert len(page.candidates) == 2
        assert page.candidates[0].person_ref != page.candidates[1].person_ref
    finally:
        jobs.close()


def test_finding_anchor_queries_match_only_an_exact_person_in_the_owned_inspection(
    tmp_path: Path,
) -> None:
    fake_ref = "person:" + "0" * 32
    source = tmp_path / "fictional-anchors.ged"
    source.write_text(
        "0 HEAD\n1 GEDC\n2 VERS 5.5.5\n1 CHAR UTF-8\n"
        "0 @I1@ INDI\n1 NAME Ada /Example/\n1 BIRT\n2 DATE invalid\n"
        f"0 @I2@ INDI\n1 NAME {fake_ref} /Example/\n0 TRLR\n",
        encoding="utf-8",
    )
    other_source = tmp_path / "other-fictional.ged"
    _write_person(other_source)
    artifacts = _ArtifactRegistry()
    jobs = JobLifecycleService(JobManager(max_workers=1), MemoryJobEventRepository())
    facade = GedcomJobFacade(service=GedcomService(artifacts=artifacts), jobs=jobs)
    try:
        job_ids = []
        for path in (source, other_source):
            job_id = facade.submit_inspect(
                GedcomInspectRequest(
                    source=artifacts.grant_input(
                        path,
                        operation="gedcom.inspect",
                        media_type="text/vnd.gedcom",
                        artifact_type="gedcom",
                    )
                )
            ).job_id
            jobs.manager.wait(job_id, timeout=5)
            job_ids.append(job_id)
        first_job, other_job = job_ids
        person = facade.root_candidates(first_job, query="Ada").candidates[0]
        anchored = facade.root_candidates(first_job, query=person.person_ref, limit=1)
        assert anchored.candidates == (person,)
        assert anchored.total_count == 1
        assert anchored.next_cursor is None
        for owned_job, reference in ((first_job, fake_ref), (other_job, person.person_ref)):
            missing = facade.root_candidates(owned_job, query=reference, limit=1)
            assert missing.candidates == ()
            assert missing.total_count == 0
            assert missing.next_cursor is None
        assert "Ada" not in jobs.get(first_job).to_json()
        assert person.person_ref not in jobs.get(first_job).to_json()
    finally:
        jobs.close()


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
