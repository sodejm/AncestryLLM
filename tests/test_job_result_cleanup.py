"""Verify private JobManager results are released at every ownership boundary."""

from __future__ import annotations

import logging
import threading

import pytest

from ancestryllm.core.cancellation import CancellationToken
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.jobs import JobManager, JobState


def _assert_manager_lock_is_available(manager: JobManager) -> None:
    acquired = threading.Event()

    def acquire_lock() -> None:
        with manager._lock:
            acquired.set()

    probe = threading.Thread(target=acquire_lock)
    probe.start()
    probe.join(timeout=1)
    assert acquired.is_set()


def test_discard_releases_completed_result_once_outside_manager_lock() -> None:
    manager = JobManager(max_workers=1, max_pending=1)
    result = object()
    released: list[object] = []

    def cleanup(value: object) -> None:
        _assert_manager_lock_is_available(manager)
        released.append(value)

    job = manager.submit("private result", lambda: result, result_cleanup=cleanup)
    assert manager.wait(job.job_id, timeout=2).result is result

    manager.discard_result(job.job_id)
    manager.discard_result(job.job_id)
    manager.shutdown()

    assert released == [result]
    assert manager.get(job.job_id).result is None


def test_discard_releases_late_result_once() -> None:
    manager = JobManager(max_workers=1, max_pending=1)
    started = threading.Event()
    release_work = threading.Event()
    result = object()
    released: list[object] = []

    def work() -> object:
        started.set()
        assert release_work.wait(2)
        return result

    job = manager.submit("late private result", work, result_cleanup=released.append)
    assert started.wait(2)
    manager.discard_result(job.job_id)
    release_work.set()

    completed = manager.wait(job.job_id, timeout=2)
    manager.shutdown()

    assert completed.state is JobState.COMPLETED
    assert completed.result is None
    assert released == [result]


def test_accepted_cancellation_releases_result_after_callable_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = JobManager(max_workers=1, max_pending=1)
    completion_started = threading.Event()
    release_completion = threading.Event()
    result = object()
    released: list[object] = []
    original_transition = manager._transition

    def blocked_transition(job_id: str, state: JobState, **changes: object) -> None:
        if state is JobState.COMPLETED:
            completion_started.set()
            assert release_completion.wait(2)
        original_transition(job_id, state, **changes)

    monkeypatch.setattr(manager, "_transition", blocked_transition)
    job = manager.submit("cancellation race", lambda: result, result_cleanup=released.append)
    assert completion_started.wait(2)
    manager.cancel(job.job_id)
    release_completion.set()

    cancelled = manager.wait(job.job_id, timeout=2)
    manager.shutdown()

    assert cancelled.state is JobState.CANCELLED
    assert cancelled.result is None
    assert released == [result]


def test_cancellation_at_post_callable_checkpoint_releases_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = JobManager(max_workers=1, max_pending=1)
    callable_returning = threading.Event()
    post_callable_checkpoint = threading.Event()
    release_checkpoint = threading.Event()
    result = object()
    released: list[object] = []
    original_raise_if_cancelled = CancellationToken.raise_if_cancelled

    def blocked_raise_if_cancelled(token: CancellationToken) -> None:
        if callable_returning.is_set() and not post_callable_checkpoint.is_set():
            post_callable_checkpoint.set()
            assert release_checkpoint.wait(2)
        original_raise_if_cancelled(token)

    def work() -> object:
        callable_returning.set()
        return result

    monkeypatch.setattr(CancellationToken, "raise_if_cancelled", blocked_raise_if_cancelled)
    job = manager.submit("post-callable cancellation", work, result_cleanup=released.append)
    assert post_callable_checkpoint.wait(2)
    manager.cancel(job.job_id)
    release_checkpoint.set()

    cancelled = manager.wait(job.job_id, timeout=2)
    manager.shutdown()

    assert cancelled.state is JobState.CANCELLED
    assert cancelled.result is None
    assert released == [result]


def test_shutdown_wait_true_releases_completed_results() -> None:
    manager = JobManager(max_workers=1, max_pending=1)
    result = object()
    released: list[object] = []
    job = manager.submit_with_progress(
        "completed private result",
        lambda _reporter: result,
        result_cleanup=released.append,
    )
    assert manager.wait(job.job_id, timeout=2).result is result

    manager.shutdown(wait=True)

    assert released == [result]
    assert manager.get(job.job_id).result is None


def test_shutdown_wait_false_marks_running_results_for_late_cleanup() -> None:
    manager = JobManager(max_workers=1, max_pending=1)
    started = threading.Event()
    release_work = threading.Event()
    result = object()
    released: list[object] = []

    def work() -> object:
        started.set()
        assert release_work.wait(2)
        return result

    job = manager.submit("running private result", work, result_cleanup=released.append)
    assert started.wait(2)
    manager.shutdown(wait=False)
    release_work.set()

    completed = manager.wait(job.job_id, timeout=2)

    assert completed.state is JobState.COMPLETED
    assert completed.result is None
    assert released == [result]


def test_cleanup_failure_logs_only_exception_class(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager = JobManager(max_workers=1, max_pending=1)

    def cleanup(_result: object) -> None:
        raise RuntimeError("PRIVATE CLEANUP DETAIL")

    job = manager.submit("private result", object, result_cleanup=cleanup)
    manager.wait(job.job_id, timeout=2)

    with caplog.at_level(logging.WARNING, logger="ancestryllm.core.jobs"):
        manager.discard_result(job.job_id)
    manager.shutdown()

    assert "RuntimeError" in caplog.text
    assert "PRIVATE CLEANUP DETAIL" not in caplog.text


def test_shutdown_preserves_completed_result_without_cleanup_callback() -> None:
    manager = JobManager(max_workers=1, max_pending=1)
    result = object()
    job = manager.submit("ordinary result", lambda: result)
    assert manager.wait(job.job_id, timeout=2).result is result

    manager.shutdown()

    assert manager.get(job.job_id).result is result


def test_result_cleanup_must_be_callable() -> None:
    manager = JobManager(max_workers=1, max_pending=1)

    with pytest.raises(ValueError, match="result cleanup must be callable"):
        manager.submit("invalid cleanup", object, result_cleanup="invalid")  # type: ignore[arg-type]

    manager.shutdown()


def test_shutdown_rejects_submission_paused_before_record_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutdown cannot miss a result admitted after its cleanup scan."""
    manager = JobManager(max_workers=1, max_pending=1)
    entered = threading.Event()
    resume = threading.Event()
    called = threading.Event()
    failures: list[AncestryError | RuntimeError] = []
    original = manager._public_resource_keys

    def paused_keys(keys: tuple[str, ...]) -> tuple[str, ...]:
        entered.set()
        assert resume.wait(2)
        return original(keys)

    def submit() -> None:
        try:
            manager.submit("shutdown admission race", called.set, resource_keys=("private",))
        except (AncestryError, RuntimeError) as error:
            failures.append(error)

    monkeypatch.setattr(manager, "_public_resource_keys", paused_keys)
    thread = threading.Thread(target=submit)
    thread.start()
    try:
        assert entered.wait(2)
        manager.shutdown(wait=False)
    finally:
        resume.set()
        thread.join(2)
        manager.shutdown()
    assert not thread.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], AncestryError)
    assert failures[0].code == "JOB_MANAGER_CLOSED"
    assert not called.is_set()
    assert manager._records == {}
    assert manager._resource_locks == {}
    assert manager._capacity.acquire(blocking=False)
    manager._capacity.release()
