"""Restart-safe, transport-neutral lifecycle contracts for background jobs."""

from __future__ import annotations

import math
import queue
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from ancestryllm.application.dto import ArtifactRef, BoundaryDTO
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.jobs import JobManager, JobSnapshot, JobState, ProgressEvent

if TYPE_CHECKING:
    from collections.abc import Callable

JOB_SCHEMA_VERSION = 1
MAX_JOB_EVENTS_PER_JOB = 256
MAX_JOB_LIST_RESULTS = 1_000
MAX_JOB_SUBSCRIBERS = 32
MAX_SUBSCRIBER_QUEUE = 64
MAX_SHUTDOWN_SECONDS = 30.0

_TERMINAL_STATES = {
    "completed",
    "failed",
    "cancelled",
}
_ACTIVE_STATES = {
    "queued",
    "running",
    "cancelling",
    "pending-safe-point",
}


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _validate_schema_version(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != JOB_SCHEMA_VERSION:
        raise ValueError(f"Unsupported {label} schema version.")


def _validate_bounded_int(
    value: object,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{label} is outside its bounded range.")


def _validate_job_id(job_id: object) -> None:
    if not isinstance(job_id, str):
        raise ValueError("job_id must be a bounded opaque job identifier.")
    suffix = job_id[1:] if job_id.startswith("j") else ""
    if not 6 <= len(suffix) <= 12 or any(character not in "0123456789" for character in suffix):
        raise ValueError("job_id must be a bounded opaque job identifier.")


def _require_job_id(job_id: str) -> None:
    try:
        _validate_job_id(job_id)
    except (AttributeError, ValueError) as exc:
        raise AncestryError(
            "JOB_ID_INVALID",
            "The background job identifier is invalid.",
            exit_code=2,
        ) from exc


def _validate_timestamp(value: object, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO-8601 timestamp.")
    if len(value) > 64 or "\x00" in value:
        raise ValueError(f"{label} is not a bounded timestamp.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone.")


def _validate_text(value: object, label: str, maximum: int) -> None:
    if value is None:
        return
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise ValueError(f"{label} exceeds its public boundary limit.")


def _validate_code(value: object, label: str) -> None:
    if value is None:
        return
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 96
        or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
            for character in value
        )
    ):
        raise ValueError(f"{label} is not a bounded stable code.")


def _bounded_text(value: str | None, maximum: int) -> str | None:
    if value is None:
        return None
    return value.replace("\x00", "\ufffd")[:maximum]


class JobLifecycleState(StrEnum):
    """Public lifecycle states, including cooperative cancellation phases."""

    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    PENDING_SAFE_POINT = "pending-safe-point"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobEventKind(StrEnum):
    """Stable event categories used by replay and SSE transports."""

    SNAPSHOT = "snapshot"
    PROGRESS = "progress"
    CANCELLATION = "cancellation"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class PublicJobProgress(BoundaryDTO):
    """Bounded progress data that cannot carry arbitrary job payloads."""

    schema_version: int
    operation: str
    timestamp: str
    completed: int | None
    total: int | None

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version, "job progress")
        _validate_text(self.operation, "progress operation", 512)
        if not self.operation.strip():
            raise ValueError("progress operation must not be empty.")
        _validate_timestamp(self.timestamp, "progress timestamp")
        if (self.completed is None) is not (self.total is None):
            raise ValueError("progress requires both completed and total values.")
        if (
            self.completed is not None
            and self.total is not None
            and (
                isinstance(self.completed, bool)
                or not isinstance(self.completed, int)
                or isinstance(self.total, bool)
                or not isinstance(self.total, int)
                or not 0 <= self.completed <= self.total <= 1_000_000_000
                or self.total < 1
            )
        ):
            raise ValueError("progress values are outside their bounded range.")

    @classmethod
    def from_core(cls, progress: ProgressEvent) -> PublicJobProgress:
        return cls(
            schema_version=JOB_SCHEMA_VERSION,
            operation=_bounded_text(progress.operation, 512) or "Background work",
            timestamp=progress.timestamp,
            completed=progress.completed,
            total=progress.total,
        )


@dataclass(frozen=True, slots=True)
class PublicJobSnapshot(BoundaryDTO):
    """Sanitized job snapshot safe for HTTP, desktop, and persisted evidence."""

    schema_version: int
    sequence: int
    job_id: str
    name: str
    state: JobLifecycleState
    submitted_at: str
    started_at: str | None
    finished_at: str | None
    resource_refs: tuple[str, ...]
    artifact: ArtifactRef | None
    outcome_summary: str | None
    next_action: str | None
    error_code: str | None
    error_message: str | None
    error_remediation: str | None
    progress: PublicJobProgress | None
    cancellation_requested_at: str | None
    cancellation_deferred_by: str | None

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version, "job snapshot")
        _validate_bounded_int(
            self.sequence,
            "job event sequence",
            minimum=1,
            maximum=9_999_999_999,
        )
        _validate_job_id(self.job_id)
        _validate_text(self.name, "job name", 256)
        if not self.name.strip():
            raise ValueError("job name must not be empty.")
        if not isinstance(self.state, JobLifecycleState):
            raise ValueError("job state must use a supported lifecycle state.")
        for label, value in (
            ("submitted_at", self.submitted_at),
            ("started_at", self.started_at),
            ("finished_at", self.finished_at),
            ("cancellation_requested_at", self.cancellation_requested_at),
        ):
            _validate_timestamp(value, label)
        if not isinstance(self.resource_refs, tuple) or len(self.resource_refs) > 32:
            raise ValueError("job resource references exceed their bounded count.")
        for resource_ref in self.resource_refs:
            if not isinstance(resource_ref, str):
                raise ValueError("job resource references must be opaque digests.")
            suffix = resource_ref.removeprefix("resource_")
            if (
                not resource_ref.startswith("resource_")
                or len(suffix) != 64
                or any(character not in "0123456789abcdef" for character in suffix)
            ):
                raise ValueError("job resource references must be opaque digests.")
        _validate_text(self.outcome_summary, "job outcome", 2_048)
        _validate_text(self.next_action, "job next action", 2_048)
        _validate_code(self.error_code, "job error code")
        _validate_text(self.error_message, "job error message", 2_048)
        _validate_text(self.error_remediation, "job error remediation", 2_048)
        _validate_text(self.cancellation_deferred_by, "cancellation safe point", 512)
        if self.artifact is not None and not isinstance(self.artifact, ArtifactRef):
            raise ValueError("job artifact must be an opaque artifact reference.")
        if self.progress is not None and not isinstance(self.progress, PublicJobProgress):
            raise ValueError("job progress must use the public progress contract.")
        if self.state.value in _TERMINAL_STATES and self.finished_at is None:
            raise ValueError("terminal jobs require a finished timestamp.")
        if self.state.value in _ACTIVE_STATES and self.finished_at is not None:
            raise ValueError("active jobs cannot have a finished timestamp.")
        if self.artifact is not None and self.state is not JobLifecycleState.COMPLETED:
            raise ValueError("only completed jobs may publish an artifact reference.")

    @classmethod
    def from_core(cls, snapshot: JobSnapshot, *, sequence: int) -> PublicJobSnapshot:
        state = _public_state(snapshot)
        return cls(
            schema_version=JOB_SCHEMA_VERSION,
            sequence=sequence,
            job_id=snapshot.job_id,
            name=_bounded_text(snapshot.name, 256) or "Background job",
            state=state,
            submitted_at=snapshot.submitted_at,
            started_at=snapshot.started_at,
            finished_at=snapshot.finished_at,
            resource_refs=snapshot.resource_keys[:32],
            artifact=(snapshot.result if isinstance(snapshot.result, ArtifactRef) else None),
            outcome_summary=_bounded_text(snapshot.outcome_summary, 2_048),
            next_action=_bounded_text(snapshot.next_action, 2_048),
            error_code=snapshot.error_code,
            error_message=_bounded_text(snapshot.error_message, 2_048),
            error_remediation=_bounded_text(snapshot.error_remediation, 2_048),
            progress=(
                PublicJobProgress.from_core(snapshot.progress)
                if snapshot.progress is not None
                else None
            ),
            cancellation_requested_at=snapshot.cancellation_requested_at,
            cancellation_deferred_by=_bounded_text(snapshot.cancellation_deferred_by, 512),
        )


def _public_state(snapshot: JobSnapshot) -> JobLifecycleState:
    if snapshot.state is JobState.QUEUED:
        return JobLifecycleState.QUEUED
    if snapshot.state is JobState.RUNNING:
        if snapshot.cancellation_pending:
            return JobLifecycleState.PENDING_SAFE_POINT
        if snapshot.cancellation_requested_at is not None:
            return JobLifecycleState.CANCELLING
        return JobLifecycleState.RUNNING
    return JobLifecycleState(snapshot.state.value)


@dataclass(frozen=True, slots=True)
class JobEvent(BoundaryDTO):
    """One monotonically ordered persisted job event."""

    schema_version: int
    sequence: int
    kind: JobEventKind
    created_at: str
    snapshot: PublicJobSnapshot

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version, "job event")
        _validate_bounded_int(
            self.sequence,
            "job event sequence",
            minimum=1,
            maximum=9_999_999_999,
        )
        if not isinstance(self.kind, JobEventKind):
            raise ValueError("job event kind must use a supported event kind.")
        if not isinstance(self.snapshot, PublicJobSnapshot):
            raise ValueError("job event snapshot must use the public snapshot contract.")
        if self.sequence != self.snapshot.sequence:
            raise ValueError("job event and snapshot sequences must match.")
        _validate_timestamp(self.created_at, "job event timestamp")
        if self.kind is JobEventKind.TERMINAL and self.snapshot.state.value not in _TERMINAL_STATES:
            raise ValueError("terminal events require a terminal job state.")
        if self.snapshot.state.value in _TERMINAL_STATES and self.kind is not JobEventKind.TERMINAL:
            raise ValueError("terminal job states require a terminal event.")


@dataclass(frozen=True, slots=True)
class JobReplay(BoundaryDTO):
    """Bounded replay response followed by live subscriber events."""

    schema_version: int
    job_id: str
    acknowledged_sequence: int
    oldest_available_sequence: int
    latest_sequence: int
    events: tuple[JobEvent, ...]

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version, "job replay")
        _validate_job_id(self.job_id)
        _validate_bounded_int(
            self.acknowledged_sequence,
            "acknowledged sequence",
            minimum=0,
            maximum=9_999_999_999,
        )
        _validate_bounded_int(
            self.oldest_available_sequence,
            "oldest available sequence",
            minimum=1,
            maximum=9_999_999_999,
        )
        _validate_bounded_int(
            self.latest_sequence,
            "latest sequence",
            minimum=1,
            maximum=9_999_999_999,
        )
        if not self.oldest_available_sequence <= self.latest_sequence:
            raise ValueError("job replay availability bounds are invalid.")
        if (
            not self.oldest_available_sequence - 1
            <= self.acknowledged_sequence
            <= self.latest_sequence
        ):
            raise ValueError("job replay acknowledgement is outside the retained event window.")
        if not isinstance(self.events, tuple):
            raise ValueError("job replay events must use a bounded tuple.")
        if len(self.events) > MAX_JOB_EVENTS_PER_JOB:
            raise ValueError("job replay exceeds the event count limit.")
        if any(not isinstance(event, JobEvent) for event in self.events):
            raise ValueError("job replay events must use the public event contract.")
        expected_sequences = tuple(
            range(
                max(self.acknowledged_sequence + 1, self.oldest_available_sequence),
                self.latest_sequence + 1,
            )
        )
        if tuple(event.sequence for event in self.events) != expected_sequences:
            raise ValueError("job replay events must form one contiguous sequence.")
        if any(event.snapshot.job_id != self.job_id for event in self.events):
            raise ValueError("job replay events must belong to the requested job.")


@dataclass(frozen=True, slots=True)
class ShutdownAssessment(BoundaryDTO):
    """Fail-closed result authorizing or vetoing sidecar termination."""

    schema_version: int
    safe_to_quit: bool
    active_jobs: tuple[PublicJobSnapshot, ...]

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version, "job shutdown")
        if not isinstance(self.safe_to_quit, bool):
            raise ValueError("safe_to_quit must be a boolean.")
        if not isinstance(self.active_jobs, tuple) or len(self.active_jobs) > MAX_JOB_LIST_RESULTS:
            raise ValueError("shutdown assessment exceeds the active-job limit.")
        if any(not isinstance(snapshot, PublicJobSnapshot) for snapshot in self.active_jobs):
            raise ValueError("shutdown active jobs must use the public snapshot contract.")
        if any(snapshot.state.value not in _ACTIVE_STATES for snapshot in self.active_jobs):
            raise ValueError("shutdown active jobs must contain only active lifecycle states.")
        if self.safe_to_quit != (not self.active_jobs):
            raise ValueError("shutdown safety must match the active-job set.")


class JobEventRepository(Protocol):
    """Persistence boundary used by the lifecycle service."""

    def record_core(self, snapshot: JobSnapshot) -> JobEvent | None: ...

    def get(self, job_id: str) -> PublicJobSnapshot: ...

    def list(self, *, limit: int = 100) -> tuple[PublicJobSnapshot, ...]: ...

    def replay(self, job_id: str, *, after: int) -> JobReplay: ...

    def reconcile_active(self) -> tuple[JobEvent, ...]: ...

    def next_job_number(self) -> int: ...


class MemoryJobEventRepository:
    """Thread-safe reference repository used by tests and ephemeral adapters."""

    def __init__(self, *, max_events_per_job: int = MAX_JOB_EVENTS_PER_JOB) -> None:
        if (
            isinstance(max_events_per_job, bool)
            or not isinstance(max_events_per_job, int)
            or not 1 <= max_events_per_job <= MAX_JOB_EVENTS_PER_JOB
        ):
            raise ValueError("Retained job events must be between 1 and 256 per job.")
        self.max_events_per_job = max_events_per_job
        self._lock = threading.RLock()
        self._snapshots: dict[str, PublicJobSnapshot] = {}
        self._events: dict[str, deque[JobEvent]] = defaultdict(
            lambda: deque(maxlen=max_events_per_job)
        )

    def record_core(self, snapshot: JobSnapshot) -> JobEvent | None:
        with self._lock:
            current = self._snapshots.get(snapshot.job_id)
            sequence = 1 if current is None else current.sequence + 1
            candidate = PublicJobSnapshot.from_core(snapshot, sequence=sequence)
            return self._record(candidate)

    def _record(
        self,
        candidate: PublicJobSnapshot,
        *,
        forced_kind: JobEventKind | None = None,
    ) -> JobEvent | None:
        current = self._snapshots.get(candidate.job_id)
        if current is not None and current.state.value in _TERMINAL_STATES:
            return None
        if current is not None and replace(current, sequence=1) == replace(candidate, sequence=1):
            return None
        sequence = 1 if current is None else current.sequence + 1
        candidate = replace(candidate, sequence=sequence)
        kind = forced_kind or _event_kind(current, candidate)
        event = JobEvent(
            schema_version=JOB_SCHEMA_VERSION,
            sequence=sequence,
            kind=kind,
            created_at=_timestamp(),
            snapshot=candidate,
        )
        self._snapshots[candidate.job_id] = candidate
        self._events[candidate.job_id].append(event)
        return event

    def get(self, job_id: str) -> PublicJobSnapshot:
        _require_job_id(job_id)
        with self._lock:
            snapshot = self._snapshots.get(job_id)
        if snapshot is None:
            raise AncestryError(
                "JOB_NOT_FOUND",
                f"Background job not found: {job_id}",
                exit_code=2,
            )
        return snapshot

    def list(self, *, limit: int = 100) -> tuple[PublicJobSnapshot, ...]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_JOB_LIST_RESULTS
        ):
            raise AncestryError(
                "JOB_LIST_LIMIT_INVALID",
                "The requested job-list limit is outside the supported range.",
                exit_code=2,
            )
        with self._lock:
            snapshots = sorted(
                self._snapshots.values(),
                key=lambda item: int(item.job_id[1:]),
                reverse=True,
            )
        return tuple(snapshots[:limit])

    def replay(self, job_id: str, *, after: int) -> JobReplay:
        _require_job_id(job_id)
        if isinstance(after, bool) or not isinstance(after, int) or not 0 <= after <= 9_999_999_999:
            raise AncestryError(
                "JOB_EVENT_CURSOR_INVALID",
                "The acknowledged job-event sequence is invalid.",
                exit_code=2,
            )
        with self._lock:
            snapshot = self._snapshots.get(job_id)
            retained = tuple(self._events.get(job_id, ()))
        if snapshot is None or not retained:
            return self._missing(job_id)
        oldest = retained[0].sequence
        latest = snapshot.sequence
        if after < oldest - 1:
            raise AncestryError(
                "JOB_EVENT_REPLAY_EXPIRED",
                "The acknowledged job-event sequence is no longer retained.",
                "Fetch the current job snapshot, then reconnect from its sequence.",
                exit_code=2,
                details={
                    "job_id": job_id,
                    "oldest_available_sequence": oldest,
                    "latest_sequence": latest,
                },
            )
        if after > latest:
            raise AncestryError(
                "JOB_EVENT_CURSOR_INVALID",
                "The acknowledged job-event sequence is newer than the job snapshot.",
                exit_code=2,
                details={"job_id": job_id, "latest_sequence": latest},
            )
        return JobReplay(
            schema_version=JOB_SCHEMA_VERSION,
            job_id=job_id,
            acknowledged_sequence=after,
            oldest_available_sequence=oldest,
            latest_sequence=latest,
            events=tuple(event for event in retained if event.sequence > after),
        )

    def _missing(self, job_id: str) -> JobReplay:
        self.get(job_id)
        raise AssertionError("unreachable")

    def reconcile_active(self) -> tuple[JobEvent, ...]:
        reconciled: list[JobEvent] = []
        with self._lock:
            active = tuple(
                snapshot
                for snapshot in self._snapshots.values()
                if snapshot.state.value in _ACTIVE_STATES
            )
            for snapshot in active:
                interrupted = replace(
                    snapshot,
                    sequence=snapshot.sequence + 1,
                    state=JobLifecycleState.FAILED,
                    finished_at=_timestamp(),
                    artifact=None,
                    outcome_summary=None,
                    next_action=None,
                    error_code="JOB_INTERRUPTED",
                    error_message="The sidecar stopped before the background job completed.",
                    error_remediation="Review the operation state before retrying manually.",
                    cancellation_deferred_by=None,
                )
                event = self._record(interrupted, forced_kind=JobEventKind.TERMINAL)
                if event is not None:
                    reconciled.append(event)
        return tuple(reconciled)

    def next_job_number(self) -> int:
        with self._lock:
            return max((int(job_id[1:]) for job_id in self._snapshots), default=0) + 1


def _event_kind(
    current: PublicJobSnapshot | None,
    candidate: PublicJobSnapshot,
) -> JobEventKind:
    if candidate.state.value in _TERMINAL_STATES:
        return JobEventKind.TERMINAL
    if current is not None and (
        candidate.state in {JobLifecycleState.CANCELLING, JobLifecycleState.PENDING_SAFE_POINT}
        or candidate.cancellation_requested_at != current.cancellation_requested_at
        or candidate.cancellation_deferred_by != current.cancellation_deferred_by
    ):
        return JobEventKind.CANCELLATION
    if current is not None and candidate.progress != current.progress:
        return JobEventKind.PROGRESS
    return JobEventKind.SNAPSHOT


class JobSubscription:
    """One bounded live subscription paired with its initial replay."""

    def __init__(
        self,
        replay: JobReplay,
        event_queue: queue.Queue[JobEvent],
        overflowed: threading.Event,
        closed: threading.Event,
        close_callback: Callable[[], None],
    ) -> None:
        self.replay = replay
        self._queue = event_queue
        self._overflowed = overflowed
        self._closed = closed
        self._close_callback = close_callback

    def next(self, *, timeout: float | None = None) -> JobEvent:
        if self._overflowed.is_set():
            raise AncestryError(
                "JOB_EVENT_REPLAY_EXPIRED",
                "The job-event subscriber fell behind its bounded queue.",
                "Fetch the current job snapshot, then reconnect from its sequence.",
                exit_code=2,
            )
        if self._closed.is_set():
            raise AncestryError("JOB_SUBSCRIPTION_CLOSED", "The job-event subscription is closed.")
        try:
            event = self._queue.get(timeout=timeout)
        except queue.Empty as exc:
            raise AncestryError(
                "JOB_EVENT_WAIT_TIMEOUT",
                "No job event arrived within the bounded wait.",
            ) from exc
        if self._overflowed.is_set():
            raise AncestryError(
                "JOB_EVENT_REPLAY_EXPIRED",
                "The job-event subscriber fell behind its bounded queue.",
                "Fetch the current job snapshot, then reconnect from its sequence.",
                exit_code=2,
            )
        return event

    def close(self) -> None:
        self._close_callback()


@dataclass(slots=True)
class _Subscriber:
    job_id: str
    event_queue: queue.Queue[JobEvent]
    overflowed: threading.Event
    closed: threading.Event


class JobLifecycleService:
    """Coordinate persistence, replay, cancellation, and safe shutdown."""

    def __init__(
        self,
        manager: JobManager,
        repository: JobEventRepository,
        *,
        subscriber_limit: int = MAX_JOB_SUBSCRIBERS,
        subscriber_queue_size: int = MAX_SUBSCRIBER_QUEUE,
    ) -> None:
        if (
            isinstance(subscriber_limit, bool)
            or not isinstance(subscriber_limit, int)
            or not 1 <= subscriber_limit <= MAX_JOB_SUBSCRIBERS
        ):
            raise ValueError("Job subscribers must be between 1 and 32.")
        if (
            isinstance(subscriber_queue_size, bool)
            or not isinstance(subscriber_queue_size, int)
            or not 1 <= subscriber_queue_size <= MAX_SUBSCRIBER_QUEUE
        ):
            raise ValueError("Subscriber queues must retain between 1 and 64 events.")
        self.manager = manager
        self.repository = repository
        self.subscriber_limit = subscriber_limit
        self.subscriber_queue_size = subscriber_queue_size
        self._lock = threading.RLock()
        self._subscribers: dict[int, _Subscriber] = {}
        self._next_subscriber_id = 1
        self._closed = False
        self._unsubscribe_manager = manager.subscribe(self._on_snapshot)

    def startup(self) -> tuple[JobEvent, ...]:
        """Fail any persisted active work instead of resuming it after restart."""

        with self._lock:
            return self.repository.reconcile_active()

    def _on_snapshot(self, snapshot: JobSnapshot) -> None:
        with self._lock:
            if self._closed:
                return
            event = self.repository.record_core(snapshot)
            if event is None:
                return
            stale: list[int] = []
            for subscriber_id, subscriber in self._subscribers.items():
                if subscriber.job_id != snapshot.job_id:
                    continue
                try:
                    subscriber.event_queue.put_nowait(event)
                except queue.Full:
                    subscriber.overflowed.set()
                    stale.append(subscriber_id)
            for subscriber_id in stale:
                self._subscribers.pop(subscriber_id, None)

    def get(self, job_id: str) -> PublicJobSnapshot:
        return self.repository.get(job_id)

    def list(self, *, limit: int = 100) -> tuple[PublicJobSnapshot, ...]:
        return self.repository.list(limit=limit)

    def cancel(self, job_id: str) -> PublicJobSnapshot:
        """Idempotently request cancellation for current work."""

        current = self.repository.get(job_id)
        if current.state.value in _TERMINAL_STATES:
            return current
        try:
            self.manager.cancel(job_id)
        except AncestryError as exc:
            if exc.code != "JOB_NOT_FOUND":
                raise
        return self.repository.get(job_id)

    def subscribe(self, job_id: str, *, after: int) -> JobSubscription:
        with self._lock:
            if self._closed:
                raise AncestryError(
                    "JOB_SERVICE_CLOSED",
                    "The background job lifecycle service is closed.",
                )
            if len(self._subscribers) >= self.subscriber_limit:
                raise AncestryError(
                    "JOB_SUBSCRIBER_LIMIT",
                    "The background job subscriber limit has been reached.",
                    "Close an existing job stream before reconnecting.",
                )
            replay = self.repository.replay(job_id, after=after)
            subscriber_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            subscriber = _Subscriber(
                job_id=job_id,
                event_queue=queue.Queue(maxsize=self.subscriber_queue_size),
                overflowed=threading.Event(),
                closed=threading.Event(),
            )
            self._subscribers[subscriber_id] = subscriber

        def close_subscription() -> None:
            with self._lock:
                removed = self._subscribers.pop(subscriber_id, None)
                (removed or subscriber).closed.set()

        return JobSubscription(
            replay,
            subscriber.event_queue,
            subscriber.overflowed,
            subscriber.closed,
            close_subscription,
        )

    def prepare_shutdown(
        self,
        *,
        action: str,
        timeout_seconds: float,
    ) -> ShutdownAssessment:
        """Wait or cancel within a bound and veto exit while jobs remain active."""

        if not isinstance(action, str) or action not in {"wait", "cancel"}:
            raise AncestryError(
                "JOB_SHUTDOWN_ACTION_INVALID",
                "The requested job shutdown action is unsupported.",
                exit_code=2,
            )
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 <= timeout_seconds <= MAX_SHUTDOWN_SECONDS
        ):
            raise AncestryError(
                "JOB_SHUTDOWN_TIMEOUT_INVALID",
                "The requested job shutdown timeout is outside the supported range.",
                exit_code=2,
            )
        if action == "cancel":
            self.manager.cancel_all()
        deadline = time.monotonic() + float(timeout_seconds)
        while self.manager.active() and time.monotonic() < deadline:
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        active_core = self.manager.active()
        if active_core:
            active = tuple(self.repository.get(snapshot.job_id) for snapshot in active_core)
            raise AncestryError(
                "JOB_SHUTDOWN_TIMEOUT",
                "Background jobs did not reach a safe terminal state before shutdown.",
                "Wait for the protected operation to reach a safe point, then try again.",
                details={
                    "active_job_ids": [snapshot.job_id for snapshot in active],
                    "active_job_count": len(active),
                },
            )
        return ShutdownAssessment(
            schema_version=JOB_SCHEMA_VERSION,
            safe_to_quit=True,
            active_jobs=(),
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            subscribers = tuple(self._subscribers.values())
            self._subscribers.clear()
        self._unsubscribe_manager()
        for subscriber in subscribers:
            subscriber.closed.set()
        self.manager.shutdown(wait=False, cancel=True)


__all__ = [
    "JOB_SCHEMA_VERSION",
    "JobEvent",
    "JobEventKind",
    "JobEventRepository",
    "JobLifecycleService",
    "JobLifecycleState",
    "JobReplay",
    "JobSubscription",
    "MemoryJobEventRepository",
    "PublicJobProgress",
    "PublicJobSnapshot",
    "ShutdownAssessment",
]
