from __future__ import annotations

import threading

import pytest

from ancestryllm.core.errors import AncestryError
from ancestryllm.core.jobs import JobManager, JobReporter, JobSnapshot, JobState


def test_job_manager_tracks_success_and_failure_with_sanitized_snapshots() -> None:
    secret = "fictional-private-value"
    manager = JobManager(
        max_workers=2, max_pending=4, redact=lambda text: text.replace(secret, "X")
    )
    try:
        completed = manager.submit("successful operation", lambda: {"ok": True})

        def fail() -> None:
            raise AncestryError("FICTIONAL_FAILURE", f"provider rejected {secret}")

        failed = manager.submit("failed operation", fail)
        completed_snapshot = manager.wait(completed.job_id, timeout=2)
        failed_snapshot = manager.wait(failed.job_id, timeout=2)
    finally:
        manager.shutdown()

    assert completed_snapshot.state is JobState.COMPLETED
    assert completed_snapshot.result == {"ok": True}
    assert completed_snapshot.started_at is not None
    assert completed_snapshot.finished_at is not None
    assert failed_snapshot.state is JobState.FAILED
    assert failed_snapshot.error_code == "FICTIONAL_FAILURE"
    assert failed_snapshot.error_message == "provider rejected X"
    assert secret not in repr(failed_snapshot)
    assert [item.job_id for item in manager.list(JobState.FAILED)] == [failed.job_id]


def test_job_manager_serializes_same_resource_but_allows_different_resources() -> None:
    manager = JobManager(max_workers=3, max_pending=6)
    first_started = threading.Event()
    release_first = threading.Event()
    different_started = threading.Event()
    overlap: list[str] = []

    def first() -> str:
        overlap.append("first-start")
        first_started.set()
        assert release_first.wait(2)
        overlap.append("first-end")
        return "first"

    def same_resource() -> str:
        overlap.append("same-start")
        return "same"

    def different_resource() -> str:
        overlap.append("different-start")
        different_started.set()
        return "different"

    try:
        first_job = manager.submit("first", first, resource_keys=("tree.ged",))
        assert first_started.wait(2)
        same_job = manager.submit("same", same_resource, resource_keys=("tree.ged",))
        different_job = manager.submit(
            "different", different_resource, resource_keys=("other.ged",)
        )
        assert different_started.wait(2)
        assert manager.get(same_job.job_id).state is JobState.QUEUED
        release_first.set()
        for job in (first_job, same_job, different_job):
            assert manager.wait(job.job_id, timeout=2).state is JobState.COMPLETED
    finally:
        release_first.set()
        manager.shutdown()

    assert overlap.index("first-end") < overlap.index("same-start")
    assert overlap.index("different-start") < overlap.index("first-end")


def test_job_manager_rejects_work_beyond_bounded_capacity() -> None:
    manager = JobManager(max_workers=1, max_pending=1)
    started = threading.Event()
    release = threading.Event()

    def blocking() -> None:
        started.set()
        assert release.wait(2)

    try:
        job = manager.submit("blocking", blocking)
        assert started.wait(2)
        with pytest.raises(AncestryError) as raised:
            manager.submit("overflow", lambda: None)
        assert raised.value.code == "JOB_QUEUE_FULL"
        release.set()
        manager.wait(job.job_id, timeout=2)
    finally:
        release.set()
        manager.shutdown()


def test_queued_cancellation_is_visible_in_job_snapshots() -> None:
    manager = JobManager(max_workers=1, max_pending=2)
    started = threading.Event()
    release = threading.Event()
    observed: list[JobSnapshot] = []
    unsubscribe = manager.subscribe(observed.append)

    def blocking() -> None:
        started.set()
        assert release.wait(2)

    try:
        running = manager.submit("running", blocking)
        assert started.wait(2)
        queued = manager.submit("queued", lambda: None)
        cancelled = manager.cancel_queued(queued.job_id)
        assert cancelled.state is JobState.CANCELLED
        assert manager.get(queued.job_id).error_code == "JOB_CANCELLED"
        assert manager.list(JobState.CANCELLED) == [cancelled]
        release.set()
        manager.wait(running.job_id, timeout=2)
    finally:
        unsubscribe()
        release.set()
        manager.shutdown()

    assert any(
        snapshot.job_id == queued.job_id and snapshot.state is JobState.CANCELLED
        for snapshot in observed
    )


def test_job_lookup_uses_stable_error() -> None:
    manager = JobManager(max_workers=1, max_pending=1)
    try:
        with pytest.raises(AncestryError) as raised:
            manager.get("j999999")
    finally:
        manager.shutdown()
    assert raised.value.code == "JOB_NOT_FOUND"


def test_jobs_publish_indeterminate_and_determinate_progress_events() -> None:
    secret = "fictional-progress-secret"
    manager = JobManager(
        max_workers=1,
        max_pending=2,
        redact=lambda text: text.replace(secret, "[REDACTED]"),
    )
    observed: list[JobSnapshot] = []
    unsubscribe = manager.subscribe(observed.append)

    def work(reporter: JobReporter) -> str:
        reporter.update(f"Connecting with {secret}")
        reporter.update("Scanning rows", completed=3, total=7)
        return "done"

    try:
        job = manager.submit_with_progress("progress", work)
        completed = manager.wait(job.job_id, timeout=2)
    finally:
        unsubscribe()
        manager.shutdown()

    progress = [snapshot.progress for snapshot in observed if snapshot.progress is not None]
    assert [snapshot.state for snapshot in observed] == [
        JobState.QUEUED,
        JobState.RUNNING,
        JobState.RUNNING,
        JobState.RUNNING,
        JobState.COMPLETED,
    ]
    assert progress[0] is not None
    assert progress[0].operation == "Connecting with [REDACTED]"
    assert progress[0].timestamp
    assert progress[0].total is None
    assert progress[1] is not None and progress[1].completed == 3
    assert progress[1].total == 7
    assert completed.progress == progress[-1]
    assert secret not in repr(observed)


@pytest.mark.parametrize(
    ("operation", "completed", "total"),
    (
        (" ", None, None),
        ("Working", 1, None),
        ("Working", None, 1),
        ("Working", -1, 2),
        ("Working", 3, 2),
        ("Working", 0, 0),
    ),
)
def test_job_reporter_rejects_invalid_progress(
    operation: str,
    completed: int | None,
    total: int | None,
) -> None:
    manager = JobManager(max_workers=1, max_pending=1)
    reporter = JobReporter(manager, "j999999")
    try:
        with pytest.raises(ValueError):
            reporter.update(operation, completed=completed, total=total)
    finally:
        manager.shutdown()


def test_job_listeners_run_outside_locks_and_failures_are_isolated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager = JobManager(max_workers=1, max_pending=2)
    observed: list[JobSnapshot] = []

    def broken_listener(_snapshot: JobSnapshot) -> None:
        raise RuntimeError("private listener detail")

    def inspecting_listener(snapshot: JobSnapshot) -> None:
        inspected = threading.Event()

        def inspect_from_another_thread() -> None:
            manager.list()
            inspected.set()

        thread = threading.Thread(target=inspect_from_another_thread)
        thread.start()
        assert inspected.wait(1)
        thread.join()
        observed.append(snapshot)

    unsubscribe_broken = manager.subscribe(broken_listener)
    unsubscribe_observer = manager.subscribe(inspecting_listener)
    try:
        job = manager.submit("observable", lambda: "done")
        assert manager.wait(job.job_id, timeout=2).state is JobState.COMPLETED
        unsubscribe_broken()
        unsubscribe_broken()
        unsubscribe_observer()
        unsubscribe_observer()
        observed_count = len(observed)
        second = manager.submit("after unsubscribe", lambda: "done")
        manager.wait(second.job_id, timeout=2)
    finally:
        manager.shutdown()

    assert [snapshot.state for snapshot in observed] == [
        JobState.QUEUED,
        JobState.RUNNING,
        JobState.COMPLETED,
    ]
    assert len(observed) == observed_count
    assert "Job listener failed: RuntimeError" in caplog.text
    assert "private listener detail" not in caplog.text
