"""Bounded, UI-independent background job execution and state tracking."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable

from ancestryllm.core.errors import AncestryError


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    job_id: str
    name: str
    state: JobState
    submitted_at: str
    started_at: str | None
    finished_at: str | None
    resource_keys: tuple[str, ...]
    result: Any = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(slots=True)
class _JobRecord:
    snapshot: JobSnapshot
    future: Future[Any] | None = None


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


class JobManager:
    """Run jobs in bounded worker threads and serialize shared mutations."""

    def __init__(
        self,
        *,
        max_workers: int = 4,
        max_pending: int = 64,
        redact: Callable[[str], str] | None = None,
    ) -> None:
        if max_workers < 1 or max_pending < max_workers:
            raise ValueError("Job limits require max_pending >= max_workers >= 1.")
        self.max_workers = max_workers
        self.max_pending = max_pending
        self._redact = redact or (lambda value: value)
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="ancestry-job",
        )
        self._capacity = threading.BoundedSemaphore(max_pending)
        self._lock = threading.RLock()
        self._records: dict[str, _JobRecord] = {}
        self._resource_locks: dict[str, threading.Lock] = {}
        self._next_id = 1
        self._closed = False

    def submit(
        self,
        name: str,
        function: Callable[[], Any],
        *,
        resource_keys: tuple[str, ...] = (),
    ) -> JobSnapshot:
        with self._lock:
            if self._closed:
                raise AncestryError("JOB_MANAGER_CLOSED", "The background job manager is closed.")
        if not self._capacity.acquire(blocking=False):
            raise AncestryError(
                "JOB_QUEUE_FULL",
                f"The background job queue reached its {self.max_pending}-job limit.",
                "Wait for a job to finish, then retry.",
            )
        normalized_keys = tuple(sorted(set(resource_keys)))
        with self._lock:
            job_id = f"j{self._next_id:06d}"
            self._next_id += 1
            snapshot = JobSnapshot(
                job_id=job_id,
                name=name,
                state=JobState.QUEUED,
                submitted_at=_timestamp(),
                started_at=None,
                finished_at=None,
                resource_keys=normalized_keys,
            )
            record = _JobRecord(snapshot)
            self._records[job_id] = record
            try:
                record.future = self._executor.submit(
                    self._execute,
                    job_id,
                    function,
                    normalized_keys,
                )
            except BaseException:
                self._records.pop(job_id, None)
                self._capacity.release()
                raise
            return snapshot

    def _execute(
        self,
        job_id: str,
        function: Callable[[], Any],
        resource_keys: tuple[str, ...],
    ) -> None:
        locks = [self._resource_lock(key) for key in resource_keys]
        try:
            for lock in locks:
                lock.acquire()
            self._transition(job_id, JobState.RUNNING, started_at=_timestamp())
            try:
                result = function()
            except BaseException as exc:  # noqa: BLE001 - job boundary normalizes failures
                if isinstance(exc, AncestryError):
                    code = exc.code
                    message = self._redact(exc.message)
                else:
                    code = "JOB_FAILED"
                    message = "The background job failed."
                self._transition(
                    job_id,
                    JobState.FAILED,
                    finished_at=_timestamp(),
                    error_code=code,
                    error_message=message,
                )
            else:
                self._transition(
                    job_id,
                    JobState.COMPLETED,
                    finished_at=_timestamp(),
                    result=result,
                )
        finally:
            for lock in reversed(locks):
                lock.release()
            self._capacity.release()

    def _resource_lock(self, resource_key: str) -> threading.Lock:
        with self._lock:
            return self._resource_locks.setdefault(resource_key, threading.Lock())

    def _transition(self, job_id: str, state: JobState, **changes: Any) -> None:
        with self._lock:
            record = self._records[job_id]
            current = record.snapshot
            values = {
                "job_id": current.job_id,
                "name": current.name,
                "state": state,
                "submitted_at": current.submitted_at,
                "started_at": current.started_at,
                "finished_at": current.finished_at,
                "resource_keys": current.resource_keys,
                "result": current.result,
                "error_code": current.error_code,
                "error_message": current.error_message,
            }
            values.update(changes)
            record.snapshot = JobSnapshot(**values)

    def get(self, job_id: str) -> JobSnapshot:
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                raise AncestryError(
                    "JOB_NOT_FOUND",
                    f"Background job not found: {job_id}",
                    exit_code=2,
                )
            return record.snapshot

    def list(self, state: JobState | None = None) -> list[JobSnapshot]:
        with self._lock:
            snapshots = [record.snapshot for record in self._records.values()]
        if state is not None:
            snapshots = [snapshot for snapshot in snapshots if snapshot.state is state]
        return snapshots

    def wait(self, job_id: str, timeout: float | None = None) -> JobSnapshot:
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                return self.get(job_id)
            future = record.future
        if future is not None:
            future.result(timeout=timeout)
        return self.get(job_id)

    def cancel_queued(self, job_id: str) -> JobSnapshot:
        """Cancel work that has not started; running cancellation is cooperative."""

        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                return self.get(job_id)
            future = record.future
            if future is None or not future.cancel():
                raise AncestryError(
                    "JOB_ALREADY_RUNNING",
                    f"Background job is already running: {job_id}",
                    "Request cooperative cancellation instead.",
                )
            current = record.snapshot
            record.snapshot = JobSnapshot(
                job_id=current.job_id,
                name=current.name,
                state=JobState.CANCELLED,
                submitted_at=current.submitted_at,
                started_at=None,
                finished_at=_timestamp(),
                resource_keys=current.resource_keys,
                error_code="JOB_CANCELLED",
                error_message="The queued background job was cancelled.",
            )
            self._capacity.release()
            return record.snapshot

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=False)
