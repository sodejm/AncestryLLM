"""Bounded, UI-independent background job execution and state tracking."""

from __future__ import annotations

import hmac
import logging
import secrets
import threading
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ancestryllm.core.cancellation import (
    CancellationError,
    CancellationState,
    CancellationToken,
    bind_cancellation_token,
)
from ancestryllm.core.errors import AncestryError

if TYPE_CHECKING:
    import contextlib
    from collections.abc import Callable

logger = logging.getLogger(__name__)


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    operation: str
    timestamp: str
    completed: int | None = None
    total: int | None = None


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
    outcome_summary: str | None = None
    next_action: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_remediation: str | None = None
    progress: ProgressEvent | None = None
    cancellation_requested_at: str | None = None
    cancellation_pending: bool = False
    cancellation_deferred_by: str | None = None


@dataclass(slots=True)
class _JobRecord:
    snapshot: JobSnapshot
    token: CancellationToken
    future: Future[Any] | None = None
    cancellation_was_deferred: bool = False
    cancellation_accepted_at: str | None = None


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class JobReporter:
    """Publish structured progress for the currently executing job."""

    _manager: JobManager
    job_id: str

    @property
    def cancellation_requested(self) -> bool:
        return self._manager._token(self.job_id).requested

    @property
    def cancellation_pending(self) -> bool:
        return self._manager._token(self.job_id).state.pending

    def check_cancelled(self) -> None:
        """Stop at a safe cooperative boundary when cancellation was requested."""

        self._manager._token(self.job_id).raise_if_cancelled()

    def non_interruptible(self, operation: str) -> contextlib.AbstractContextManager[None]:
        """Defer cancellation through an atomic operation."""

        return self._manager._token(self.job_id).defer(operation)

    def update(
        self,
        operation: str,
        *,
        completed: int | None = None,
        total: int | None = None,
    ) -> None:
        if not operation.strip():
            raise ValueError("Progress operation must not be empty.")
        if (completed is None) is not (total is None):
            raise ValueError("Determinate progress requires both completed and total values.")
        if (
            completed is not None
            and total is not None
            and (completed < 0 or total < 1 or completed > total)
        ):
            raise ValueError("Progress values require 0 <= completed <= total and total >= 1.")
        self.check_cancelled()
        self._manager._report_progress(
            self.job_id,
            ProgressEvent(operation, _timestamp(), completed, total),
        )

    def set_outcome(self, summary: str, *, next_action: str) -> None:
        """Retain a redacted, transport-neutral success summary and next action."""

        if not summary.strip():
            raise ValueError("Job outcome summary must not be empty.")
        if not next_action.strip():
            raise ValueError("Job next action must not be empty.")
        self.check_cancelled()
        self._manager._report_outcome(self.job_id, summary, next_action)


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
        self._resource_identifier_key = secrets.token_bytes(32)
        self._listeners: list[Callable[[JobSnapshot], None]] = []
        self._next_id = 1
        self._closed = False

    def submit(
        self,
        name: str,
        function: Callable[[], Any],
        *,
        resource_keys: tuple[str, ...] = (),
    ) -> JobSnapshot:
        return self._submit(
            name,
            lambda _reporter: function(),
            resource_keys=resource_keys,
        )

    def submit_with_progress(
        self,
        name: str,
        function: Callable[[JobReporter], Any],
        *,
        resource_keys: tuple[str, ...] = (),
    ) -> JobSnapshot:
        return self._submit(name, function, resource_keys=resource_keys)

    def _submit(
        self,
        name: str,
        function: Callable[[JobReporter], Any],
        *,
        resource_keys: tuple[str, ...],
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
        public_resource_keys = self._public_resource_keys(normalized_keys)
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
                resource_keys=public_resource_keys,
            )
            token = CancellationToken()
            record = _JobRecord(snapshot, token)
            self._records[job_id] = record
            token.subscribe(lambda state: self._sync_cancellation(job_id, state))
        self._notify(snapshot)
        try:
            future = self._executor.submit(
                self._execute,
                job_id,
                function,
                normalized_keys,
            )
        except BaseException:
            with self._lock:
                self._records.pop(job_id, None)
            self._capacity.release()
            raise
        with self._lock:
            record.future = future
        return snapshot

    def _public_resource_keys(self, resource_keys: tuple[str, ...]) -> tuple[str, ...]:
        """Return manager-local opaque identifiers for public job snapshots."""

        return tuple(
            "resource_"
            + hmac.digest(
                self._resource_identifier_key,
                resource_key.encode("utf-8", errors="surrogatepass"),
                "sha256",
            ).hex()
            for resource_key in resource_keys
        )

    def _execute(
        self,
        job_id: str,
        function: Callable[[JobReporter], Any],
        resource_keys: tuple[str, ...],
    ) -> None:
        locks = [self._resource_lock(key) for key in resource_keys]
        acquired: list[threading.Lock] = []
        token = self._token(job_id)
        try:
            with bind_cancellation_token(token):
                try:
                    for lock in locks:
                        while not lock.acquire(timeout=0.05):
                            token.raise_if_cancelled()
                        acquired.append(lock)
                    token.raise_if_cancelled()
                    self._transition(job_id, JobState.RUNNING, started_at=_timestamp())
                    result = function(JobReporter(self, job_id))
                    token.raise_if_cancelled()
                except CancellationError:
                    self._transition_cancelled(job_id)
                except BaseException as exc:  # noqa: BLE001 - job boundary normalizes failures
                    if (
                        token.requested
                        and isinstance(exc, AncestryError)
                        and exc.code == "PROVIDER_CANCELLED"
                    ):
                        self._transition_cancelled(job_id)
                    else:
                        if isinstance(exc, AncestryError):
                            code = exc.code
                            message = self._redact(exc.message)
                            remediation = self._redact(
                                exc.remediation
                                or "Review the coded failure before retrying manually."
                            )
                        else:
                            code = "JOB_FAILED"
                            message = "The background job failed."
                            remediation = "Review the coded failure before retrying manually."
                        self._transition(
                            job_id,
                            JobState.FAILED,
                            finished_at=_timestamp(),
                            error_code=code,
                            error_message=message,
                            error_remediation=remediation,
                            outcome_summary=None,
                            next_action=None,
                        )
                else:
                    self._transition(
                        job_id,
                        JobState.COMPLETED,
                        finished_at=_timestamp(),
                        result=result,
                    )
        finally:
            for lock in reversed(acquired):
                lock.release()
            self._capacity.release()

    def _resource_lock(self, resource_key: str) -> threading.Lock:
        with self._lock:
            return self._resource_locks.setdefault(resource_key, threading.Lock())

    def _transition(self, job_id: str, state: JobState, **changes: Any) -> None:
        with self._lock:
            record = self._records[job_id]
            current = record.snapshot
            if state is JobState.COMPLETED and record.cancellation_accepted_at is not None:
                state = JobState.CANCELLED
                changes.update(
                    result=None,
                    outcome_summary=None,
                    next_action=None,
                    error_code="JOB_CANCELLED",
                    error_message=self._cancellation_message(record.cancellation_was_deferred),
                    error_remediation=None,
                    cancellation_requested_at=record.cancellation_accepted_at,
                    cancellation_pending=False,
                    cancellation_deferred_by=None,
                )
            elif state is JobState.COMPLETED:
                result = changes.get("result")
                changes.setdefault(
                    "outcome_summary",
                    current.outcome_summary
                    or (
                        "The background job completed and retained its result."
                        if result is not None
                        else "The background job completed."
                    ),
                )
                changes.setdefault(
                    "next_action",
                    current.next_action
                    or (
                        "Inspect the retained job result."
                        if result is not None
                        else "No follow-up action is required."
                    ),
                )
                changes.update(
                    error_code=None,
                    error_message=None,
                    error_remediation=None,
                )
            values = {
                "job_id": current.job_id,
                "name": current.name,
                "state": state,
                "submitted_at": current.submitted_at,
                "started_at": current.started_at,
                "finished_at": current.finished_at,
                "resource_keys": current.resource_keys,
                "result": current.result,
                "outcome_summary": current.outcome_summary,
                "next_action": current.next_action,
                "error_code": current.error_code,
                "error_message": current.error_message,
                "error_remediation": current.error_remediation,
                "progress": current.progress,
                "cancellation_requested_at": current.cancellation_requested_at,
                "cancellation_pending": current.cancellation_pending,
                "cancellation_deferred_by": current.cancellation_deferred_by,
            }
            values.update(changes)
            record.snapshot = JobSnapshot(**values)
            snapshot = record.snapshot
        self._notify(snapshot)

    def _sync_cancellation(self, job_id: str, state: CancellationState) -> None:
        with self._lock:
            record = self._records.get(job_id)
            if record is None or record.snapshot.state not in {
                JobState.QUEUED,
                JobState.RUNNING,
            }:
                return
            if state.pending:
                record.cancellation_was_deferred = True
            record.snapshot = replace(
                record.snapshot,
                cancellation_requested_at=state.requested_at,
                cancellation_pending=state.pending,
                cancellation_deferred_by=(
                    self._redact(state.deferred_by) if state.pending and state.deferred_by else None
                ),
            )
            snapshot = record.snapshot
        self._notify(snapshot)

    def _token(self, job_id: str) -> CancellationToken:
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                raise AncestryError(
                    "JOB_NOT_FOUND",
                    f"Background job not found: {job_id}",
                    exit_code=2,
                )
            return record.token

    def _transition_cancelled(self, job_id: str) -> None:
        state = self._token(job_id).state
        with self._lock:
            was_deferred = self._records[job_id].cancellation_was_deferred
        self._transition(
            job_id,
            JobState.CANCELLED,
            finished_at=_timestamp(),
            result=None,
            outcome_summary=None,
            next_action=None,
            error_code="JOB_CANCELLED",
            error_message=self._cancellation_message(was_deferred),
            error_remediation=None,
            cancellation_requested_at=state.requested_at,
            cancellation_pending=False,
            cancellation_deferred_by=None,
        )

    @staticmethod
    def _cancellation_message(was_deferred: bool) -> str:
        return (
            "Cancellation was acknowledged after a protected operation reached a safe boundary."
            if was_deferred
            else "The background job acknowledged cancellation at a safe boundary."
        )

    def _report_progress(self, job_id: str, event: ProgressEvent) -> None:
        with self._lock:
            record = self._records[job_id]
            state = record.snapshot.state
            if state not in {JobState.QUEUED, JobState.RUNNING}:
                return
            record.snapshot = replace(
                record.snapshot,
                progress=ProgressEvent(
                    operation=self._redact(event.operation),
                    timestamp=event.timestamp,
                    completed=event.completed,
                    total=event.total,
                ),
            )
            snapshot = record.snapshot
        self._notify(snapshot)

    def _report_outcome(self, job_id: str, summary: str, next_action: str) -> None:
        with self._lock:
            record = self._records[job_id]
            if record.snapshot.state not in {JobState.QUEUED, JobState.RUNNING}:
                return
            record.snapshot = replace(
                record.snapshot,
                outcome_summary=self._redact(summary),
                next_action=self._redact(next_action),
            )
            snapshot = record.snapshot
        self._notify(snapshot)

    def subscribe(self, listener: Callable[[JobSnapshot], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def _notify(self, snapshot: JobSnapshot) -> None:
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(snapshot)
            except BaseException as exc:  # noqa: BLE001 - listeners cannot break job execution
                logger.warning("Job listener failed: %s", type(exc).__name__)

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
            with suppress(CancelledError):
                future.result(timeout=timeout)
        return self.get(job_id)

    def active(self) -> tuple[JobSnapshot, ...]:
        """Return active jobs in submission order."""

        return tuple(
            snapshot
            for snapshot in self.list()
            if snapshot.state in {JobState.QUEUED, JobState.RUNNING}
        )

    def foreground(self) -> JobSnapshot | None:
        """Return the most recently submitted active job."""

        active = self.active()
        return active[-1] if active else None

    def cancel_foreground(self) -> JobSnapshot | None:
        """Request cancellation of the most recently submitted active job."""

        foreground = self.foreground()
        return self.cancel(foreground.job_id) if foreground is not None else None

    def cancel_all(self) -> tuple[JobSnapshot, ...]:
        """Request cancellation of every active job."""

        return tuple(self.cancel(snapshot.job_id) for snapshot in self.active())

    def cancel(self, job_id: str) -> JobSnapshot:
        """Cancel queued work immediately or request cooperative running cancellation."""

        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                return self.get(job_id)
            current = record.snapshot
            if current.state not in {JobState.QUEUED, JobState.RUNNING}:
                return current
            if record.cancellation_accepted_at is None:
                record.cancellation_accepted_at = _timestamp()
            future = record.future
            if current.state is JobState.QUEUED and future is not None and future.cancel():
                record.snapshot = replace(
                    record.snapshot,
                    state=JobState.CANCELLED,
                    finished_at=_timestamp(),
                    result=None,
                    outcome_summary=None,
                    next_action=None,
                    error_code="JOB_CANCELLED",
                    error_message="The queued background job was cancelled.",
                    error_remediation=None,
                    cancellation_requested_at=record.cancellation_accepted_at,
                    cancellation_pending=False,
                    cancellation_deferred_by=None,
                )
                self._capacity.release()
                snapshot = record.snapshot
            else:
                record.snapshot = replace(
                    record.snapshot,
                    cancellation_requested_at=record.cancellation_accepted_at,
                )
                snapshot = None
        record.token.request()
        if snapshot is not None:
            self._notify(snapshot)
            return snapshot
        return self.get(job_id)

    def shutdown(self, *, wait: bool = True, cancel: bool = False) -> None:
        with self._lock:
            self._closed = True
        if cancel:
            self.cancel_all()
        self._executor.shutdown(wait=wait, cancel_futures=False)
