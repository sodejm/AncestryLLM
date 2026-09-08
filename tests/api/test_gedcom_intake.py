"""Private native intake retains no filesystem authority after inspection."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from ancestryllm.api.gedcom_intake import GedcomIntake
from ancestryllm.application._artifacts import _ArtifactRegistry
from ancestryllm.application.jobs import JobLifecycleService, MemoryJobEventRepository
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.jobs import JobManager, JobState
from ancestryllm.gedcom.service import GedcomService

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

CONTENT = b"0 HEAD\n1 GEDC\n2 VERS 5.5.5\n1 CHAR UTF-8\n0 @I1@ INDI\n1 NAME Ada /Example/\n0 TRLR\n"
IntakeFixture = tuple[GedcomIntake, JobLifecycleService]


@pytest.fixture
def intake(tmp_path: Path) -> Iterator[IntakeFixture]:
    artifacts = _ArtifactRegistry()
    jobs = JobLifecycleService(JobManager(max_workers=1), MemoryJobEventRepository())
    boundary = GedcomIntake(
        directory=tmp_path,
        artifacts=artifacts,
        service=GedcomService(artifacts=artifacts),
        jobs=jobs,
    )
    try:
        yield boundary, jobs
    finally:
        boundary.close()
        jobs.close()


def stage(tmp_path: Path, number: int = 1) -> str:
    stage_id = f"{number:064x}"
    (tmp_path / f"{stage_id}.ged").write_bytes(CONTENT)
    return stage_id


def test_intake_inspects_and_removes_staged_source(intake: IntakeFixture, tmp_path: Path) -> None:
    boundary, jobs = intake
    stage_id = stage(tmp_path)
    job = boundary.submit(stage_id, len(CONTENT), hashlib.sha256(CONTENT).hexdigest())
    assert jobs.manager.wait(job.job_id, timeout=5).state is JobState.COMPLETED
    assert boundary.result(job.job_id).summary.individual_count == 1
    assert boundary.roots(job.job_id, query="Ada").total_count == 1
    assert not (tmp_path / f"{stage_id}.ged").exists()
    boundary.discard(job.job_id)
    boundary.discard(job.job_id)
    assert jobs.manager.get(job.job_id).result is None
    with pytest.raises(AncestryError) as error:
        boundary.result(job.job_id)
    assert error.value.code == "GEDCOM_JOB_RESULT_UNAVAILABLE"


def test_intake_rejects_replaced_contents(intake: IntakeFixture, tmp_path: Path) -> None:
    boundary, jobs = intake
    job = boundary.submit(stage(tmp_path), len(CONTENT), "0" * 64)
    snapshot = jobs.manager.wait(job.job_id, timeout=5)
    assert snapshot.state is JobState.FAILED
    assert snapshot.result is None
    assert not list(tmp_path.glob("*.ged"))


@pytest.mark.parametrize("stage_id", ["../outside", "/tmp/source", "a" * 65, "A" * 64])
def test_intake_rejects_non_capabilities(intake: IntakeFixture, stage_id: str) -> None:
    boundary, _ = intake
    with pytest.raises(AncestryError) as error:
        boundary.submit(stage_id, len(CONTENT), "0" * 64)
    assert error.value.code == "GEDCOM_INTAKE_INVALID"


def test_intake_bounds_retained_sources(intake: IntakeFixture, tmp_path: Path) -> None:
    boundary, jobs = intake
    for number in range(8):
        job = boundary.submit(
            stage(tmp_path, number), len(CONTENT), hashlib.sha256(CONTENT).hexdigest()
        )
        jobs.manager.wait(job.job_id, timeout=5)
    with pytest.raises(AncestryError) as error:
        boundary.submit(stage(tmp_path, 9), len(CONTENT), hashlib.sha256(CONTENT).hexdigest())
    assert error.value.code == "GEDCOM_INTAKE_CAPACITY"


def test_intake_cannot_query_unowned_job(intake: IntakeFixture) -> None:
    boundary, jobs = intake
    job = jobs.manager.submit("unrelated", lambda: "private")
    jobs.manager.wait(job.job_id, timeout=5)
    with pytest.raises(AncestryError) as error:
        boundary.result(job.job_id)
    assert error.value.code == "GEDCOM_JOB_RESULT_UNAVAILABLE"
    with pytest.raises(AncestryError) as error:
        boundary.roots(job.job_id)
    assert error.value.code == "GEDCOM_JOB_RESULT_UNAVAILABLE"
    boundary.discard(job.job_id)
    assert jobs.manager.get(job.job_id).result == "private"
