"""Encrypted SQL repository for bounded background-job snapshots and events."""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select

from ancestryllm.application.jobs import (
    JOB_SCHEMA_VERSION,
    MAX_JOB_EVENTS_PER_JOB,
    MAX_JOB_LIST_RESULTS,
    JobEvent,
    JobEventKind,
    JobLifecycleState,
    JobReplay,
    PublicJobSnapshot,
)
from ancestryllm.core.errors import AncestryError, StorageError
from ancestryllm.storage.models import JobEventModel, JobModel

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ancestryllm.core.jobs import JobSnapshot
    from ancestryllm.storage.database import Database

_TERMINAL_STATES = {
    JobLifecycleState.COMPLETED.value,
    JobLifecycleState.FAILED.value,
    JobLifecycleState.CANCELLED.value,
}
_ACTIVE_STATES = {
    JobLifecycleState.QUEUED.value,
    JobLifecycleState.RUNNING.value,
    JobLifecycleState.CANCELLING.value,
    JobLifecycleState.PENDING_SAFE_POINT.value,
}


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _validate_job_id(job_id: object) -> int:
    if not isinstance(job_id, str):
        raise AncestryError(
            "JOB_ID_INVALID",
            "The background job identifier is invalid.",
            exit_code=2,
        )
    suffix = job_id[1:] if job_id.startswith("j") else ""
    if not 6 <= len(suffix) <= 12 or not suffix.isascii() or not suffix.isdecimal():
        raise AncestryError(
            "JOB_ID_INVALID",
            "The background job identifier is invalid.",
            exit_code=2,
        )
    return int(suffix)


def _validate_limit(limit: int) -> None:
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


def _validate_cursor(after: int) -> None:
    if isinstance(after, bool) or not isinstance(after, int) or not 0 <= after <= 9_999_999_999:
        raise AncestryError(
            "JOB_EVENT_CURSOR_INVALID",
            "The acknowledged job-event sequence is invalid.",
            exit_code=2,
        )


def _storage_error(exc: BaseException) -> StorageError:
    return StorageError(
        "JOB_RECORD_INVALID",
        "Persisted background-job state is invalid.",
        "Stop using the workspace and restore the latest verified encrypted backup.",
        details={"error_type": type(exc).__name__},
    )


def _decode_snapshot(payload: str) -> PublicJobSnapshot:
    try:
        return PublicJobSnapshot.from_json(payload)
    except (TypeError, ValueError) as exc:
        raise _storage_error(exc) from exc


def _decode_event(payload: str) -> JobEvent:
    try:
        return JobEvent.from_json(payload)
    except (TypeError, ValueError) as exc:
        raise _storage_error(exc) from exc


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


class SqlJobEventRepository:
    """Persist sanitized lifecycle state in the encrypted workspace database."""

    def __init__(
        self,
        database: Database,
        *,
        max_events_per_job: int = MAX_JOB_EVENTS_PER_JOB,
    ) -> None:
        if (
            isinstance(max_events_per_job, bool)
            or not isinstance(max_events_per_job, int)
            or not 1 <= max_events_per_job <= MAX_JOB_EVENTS_PER_JOB
        ):
            raise ValueError("Retained job events must be between 1 and 256 per job.")
        self.database = database
        self.max_events_per_job = max_events_per_job
        self._lock = threading.RLock()

    def record_core(self, snapshot: JobSnapshot) -> JobEvent | None:
        """Persist a normalized core job snapshot as a durable lifecycle event."""
        job_number = _validate_job_id(snapshot.job_id)
        with self._lock, self.database.session() as session:
            current_row = session.get(JobModel, snapshot.job_id)
            current = _decode_snapshot(current_row.snapshot_json) if current_row else None
            sequence = 1 if current is None else current.sequence + 1
            candidate = PublicJobSnapshot.from_core(snapshot, sequence=sequence)
            event = self._record(
                session,
                candidate,
                job_number=job_number,
                current_row=current_row,
                current=current,
            )
            session.commit()
            return event

    def _record(
        self,
        session: Session,
        candidate: PublicJobSnapshot,
        *,
        job_number: int,
        current_row: JobModel | None,
        current: PublicJobSnapshot | None,
        forced_kind: JobEventKind | None = None,
    ) -> JobEvent | None:
        if current is not None and current.state.value in _TERMINAL_STATES:
            return None
        if current is not None and replace(current, sequence=1) == replace(candidate, sequence=1):
            return None

        sequence = 1 if current is None else current.sequence + 1
        candidate = replace(candidate, sequence=sequence)
        event = JobEvent(
            schema_version=JOB_SCHEMA_VERSION,
            sequence=sequence,
            kind=forced_kind or _event_kind(current, candidate),
            created_at=_timestamp(),
            snapshot=candidate,
        )
        if current_row is None:
            current_row = JobModel(
                job_id=candidate.job_id,
                job_number=job_number,
                state=candidate.state.value,
                sequence=sequence,
                submitted_at=candidate.submitted_at,
                snapshot_json=candidate.to_json(),
            )
            session.add(current_row)
            # The models intentionally do not expose an ORM relationship: persisted
            # events are an internal replay log, not an object graph. Flush the parent
            # explicitly so SQLAlchemy cannot reorder these independent INSERTs ahead
            # of the foreign-key constraint.
            session.flush((current_row,))
        else:
            current_row.state = candidate.state.value
            current_row.sequence = sequence
            current_row.snapshot_json = candidate.to_json()
        session.add(
            JobEventModel(
                job_id=candidate.job_id,
                sequence=sequence,
                kind=event.kind.value,
                event_json=event.to_json(),
            )
        )
        session.flush()
        retained = tuple(
            session.scalars(
                select(JobEventModel.sequence)
                .where(JobEventModel.job_id == candidate.job_id)
                .order_by(JobEventModel.sequence.desc())
                .offset(self.max_events_per_job)
            )
        )
        if retained:
            session.execute(
                delete(JobEventModel).where(
                    JobEventModel.job_id == candidate.job_id,
                    JobEventModel.sequence.in_(retained),
                )
            )
        return event

    def get(self, job_id: str) -> PublicJobSnapshot:
        """Return one persisted public job snapshot or raise ``JOB_NOT_FOUND``."""
        _validate_job_id(job_id)
        with self._lock, self.database.session() as session:
            row = session.get(JobModel, job_id)
            if row is None:
                raise AncestryError(
                    "JOB_NOT_FOUND",
                    f"Background job not found: {job_id}",
                    exit_code=2,
                )
            return _decode_snapshot(row.snapshot_json)

    def list(self, *, limit: int = 100) -> tuple[PublicJobSnapshot, ...]:
        """Return newest persisted public job snapshots up to the requested limit."""
        _validate_limit(limit)
        with self._lock, self.database.session() as session:
            rows = tuple(
                session.scalars(select(JobModel).order_by(JobModel.job_number.desc()).limit(limit))
            )
            return tuple(_decode_snapshot(row.snapshot_json) for row in rows)

    def replay(self, job_id: str, *, after: int) -> JobReplay:
        """Replay persisted events through the SQL job event repository."""
        _validate_job_id(job_id)
        _validate_cursor(after)
        with self._lock, self.database.session() as session:
            snapshot_row = session.get(JobModel, job_id)
            if snapshot_row is None:
                raise AncestryError(
                    "JOB_NOT_FOUND",
                    f"Background job not found: {job_id}",
                    exit_code=2,
                )
            snapshot = _decode_snapshot(snapshot_row.snapshot_json)
            rows = tuple(
                session.scalars(
                    select(JobEventModel)
                    .where(JobEventModel.job_id == job_id)
                    .order_by(JobEventModel.sequence)
                )
            )
            if not rows:
                raise _storage_error(ValueError("job has no retained events"))
            oldest = rows[0].sequence
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
            events = tuple(_decode_event(row.event_json) for row in rows if row.sequence > after)
            return JobReplay(
                schema_version=JOB_SCHEMA_VERSION,
                job_id=job_id,
                acknowledged_sequence=after,
                oldest_available_sequence=oldest,
                latest_sequence=latest,
                events=events,
            )

    def reconcile_active(self) -> tuple[JobEvent, ...]:
        """Reconcile active jobs after SQL job event repository recovery."""
        reconciled: list[JobEvent] = []
        with self._lock, self.database.session() as session:
            rows = tuple(
                session.scalars(
                    select(JobModel)
                    .where(JobModel.state.in_(_ACTIVE_STATES))
                    .order_by(JobModel.job_number)
                )
            )
            for row in rows:
                current = _decode_snapshot(row.snapshot_json)
                interrupted = replace(
                    current,
                    sequence=current.sequence + 1,
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
                event = self._record(
                    session,
                    interrupted,
                    job_number=row.job_number,
                    current_row=row,
                    current=current,
                    forced_kind=JobEventKind.TERMINAL,
                )
                if event is not None:
                    reconciled.append(event)
            session.commit()
        return tuple(reconciled)

    def next_job_number(self) -> int:
        """Reserve the next monotonically increasing job number through the SQL job event repository."""
        with self._lock, self.database.session() as session:
            latest = session.scalar(select(func.max(JobModel.job_number)))
            return int(latest or 0) + 1


__all__ = ["SqlJobEventRepository"]
