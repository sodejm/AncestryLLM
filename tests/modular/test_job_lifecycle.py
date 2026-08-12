"""Verify restart-safe, bounded, transport-neutral job lifecycle behavior."""

from __future__ import annotations

import threading
import time
from dataclasses import replace

import pytest

from ancestryllm.application.dto import ArtifactRef, ArtifactStatus
from ancestryllm.application.jobs import (
    JobEvent,
    JobEventKind,
    JobLifecycleService,
    JobLifecycleState,
    JobReplay,
    MemoryJobEventRepository,
    PublicJobProgress,
    PublicJobSnapshot,
    ShutdownAssessment,
)
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.jobs import JobManager, JobReporter, JobSnapshot, JobState


def _core_snapshot(
    *,
    job_id: str = "j000001",
    state: JobState = JobState.RUNNING,
) -> JobSnapshot:
    return JobSnapshot(
        job_id=job_id,
        name="fictional import",
        state=state,
        submitted_at="2026-08-12T12:00:00+00:00",
        started_at="2026-08-12T12:00:01+00:00",
        finished_at=None,
        resource_keys=("resource_" + "a" * 64,),
    )


def test_repository_bounds_replay_and_requires_resync_after_expiry() -> None:
    repository = MemoryJobEventRepository(max_events_per_job=2)
    repository.record_core(_core_snapshot(state=JobState.QUEUED))
    repository.record_core(_core_snapshot())
    repository.record_core(
        replace(
            _core_snapshot(),
            state=JobState.COMPLETED,
            finished_at="2026-08-12T12:00:03+00:00",
            outcome_summary="The fictional import completed.",
            next_action="Inspect the artifact reference.",
        )
    )

    current = repository.get("j000001")
    assert current.sequence == 3
    assert current.state is JobLifecycleState.COMPLETED
    assert [event.sequence for event in repository.replay("j000001", after=1).events] == [2, 3]
    with pytest.raises(AncestryError) as raised:
        repository.replay("j000001", after=0)
    assert raised.value.code == "JOB_EVENT_REPLAY_EXPIRED"


def test_restart_reconciliation_persists_exactly_one_failed_terminal_event() -> None:
    repository = MemoryJobEventRepository(max_events_per_job=8)
    repository.record_core(_core_snapshot())

    reconciled = repository.reconcile_active()
    assert len(reconciled) == 1
    assert reconciled[0].kind is JobEventKind.TERMINAL
    assert reconciled[0].snapshot.state is JobLifecycleState.FAILED
    assert reconciled[0].snapshot.error_code == "JOB_INTERRUPTED"
    assert repository.reconcile_active() == ()
    assert [event.kind for event in repository.replay("j000001", after=0).events].count(
        JobEventKind.TERMINAL
    ) == 1


def test_only_opaque_artifact_results_cross_the_public_job_boundary() -> None:
    repository = MemoryJobEventRepository(max_events_per_job=8)
    private_result = replace(
        _core_snapshot(),
        state=JobState.COMPLETED,
        finished_at="2026-08-12T12:00:03+00:00",
        result={"path": "/private/fictional-family.ged"},
    )
    repository.record_core(private_result)
    assert repository.get("j000001").artifact is None

    artifact = ArtifactRef(
        artifact_id="art_" + "a" * 32,
        media_type="application/x-gedcom",
        artifact_type="gedcom",
        size_bytes=123,
        status=ArtifactStatus.READY,
        sha256="b" * 64,
    )
    repository.record_core(replace(private_result, job_id="j000002", result=artifact))
    assert repository.get("j000002").artifact == artifact
    assert "/private" not in repository.get("j000001").to_json()


def test_slow_subscriber_never_blocks_worker_completion() -> None:
    manager = JobManager(max_workers=1, max_pending=1)
    repository = MemoryJobEventRepository(max_events_per_job=32)
    service = JobLifecycleService(
        manager,
        repository,
        subscriber_limit=2,
        subscriber_queue_size=1,
    )

    entered = threading.Event()
    release = threading.Event()

    def work(reporter: JobReporter) -> None:
        entered.set()
        assert release.wait(2)
        for index in range(20):
            reporter.update("Scanning fictional rows", completed=index + 1, total=20)

    try:
        submitted = manager.submit_with_progress("bounded subscriber", work)
        assert entered.wait(2)
        subscription = service.subscribe(submitted.job_id, after=0)
        started = time.monotonic()
        release.set()
        completed = manager.wait(submitted.job_id, timeout=2)
        elapsed = time.monotonic() - started
    finally:
        release.set()
        service.close()

    assert completed.state is JobState.COMPLETED
    assert elapsed < 1
    with pytest.raises(AncestryError) as raised:
        subscription.next(timeout=0.1)
    assert raised.value.code == "JOB_EVENT_REPLAY_EXPIRED"


def test_shutdown_timeout_vetoes_exit_until_cooperative_job_finishes() -> None:
    manager = JobManager(max_workers=1, max_pending=1)
    service = JobLifecycleService(manager, MemoryJobEventRepository())
    entered = threading.Event()
    release = threading.Event()

    def work() -> None:
        entered.set()
        assert release.wait(2)

    try:
        submitted = manager.submit("bounded shutdown", work)
        assert entered.wait(2)
        with pytest.raises(AncestryError) as raised:
            service.prepare_shutdown(action="cancel", timeout_seconds=0.01)
        assert raised.value.code == "JOB_SHUTDOWN_TIMEOUT"
        release.set()
        manager.wait(submitted.job_id, timeout=2)
        assessment = service.prepare_shutdown(action="cancel", timeout_seconds=0.1)
    finally:
        release.set()
        service.close()

    assert assessment.safe_to_quit is True
    assert assessment.active_jobs == ()


def test_public_snapshot_rejects_unknown_schema_versions() -> None:
    repository = MemoryJobEventRepository()
    repository.record_core(_core_snapshot())
    payload = (
        repository.get("j000001").to_json().replace('"schema_version":1', '"schema_version":2')
    )
    with pytest.raises(ValueError, match="schema version"):
        PublicJobSnapshot.from_json(payload)


def test_public_job_dtos_reject_python_type_confusion() -> None:
    repository = MemoryJobEventRepository()
    repository.record_core(_core_snapshot())
    snapshot = repository.get("j000001")

    with pytest.raises(ValueError, match="sequence"):
        replace(snapshot, sequence=True)
    with pytest.raises(ValueError, match="progress values"):
        PublicJobProgress(
            schema_version=1,
            operation="Scanning fictional rows",
            timestamp="2026-08-12T12:00:02+00:00",
            completed=True,
            total=1,
        )
    with pytest.raises(ValueError, match="snapshot"):
        JobEvent(
            schema_version=1,
            sequence=1,
            kind=JobEventKind.SNAPSHOT,
            created_at="2026-08-12T12:00:02+00:00",
            snapshot="not-a-snapshot",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="events"):
        JobReplay(
            schema_version=1,
            job_id="j000001",
            acknowledged_sequence=0,
            oldest_available_sequence=1,
            latest_sequence=1,
            events=("not-an-event",),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="safe_to_quit"):
        ShutdownAssessment(
            schema_version=1,
            safe_to_quit=1,  # type: ignore[arg-type]
            active_jobs=(),
        )


def test_job_lifecycle_limits_reject_boolean_integers() -> None:
    with pytest.raises(ValueError, match="Retained job events"):
        MemoryJobEventRepository(max_events_per_job=True)

    manager = JobManager(max_workers=1, max_pending=1)
    try:
        with pytest.raises(ValueError, match="Job subscribers"):
            JobLifecycleService(
                manager,
                MemoryJobEventRepository(),
                subscriber_limit=True,
            )
    finally:
        manager.shutdown()
