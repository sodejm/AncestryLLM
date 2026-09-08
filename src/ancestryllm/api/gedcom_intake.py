"""Private native staging adapter for read-only GEDCOM inspection.

The directory arrives only in the main-process launch frame. Request bodies carry
random staging capabilities and fingerprints, never paths. Retained results are
session-local; grants and staged bytes are released as soon as each job stops.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING

from ancestryllm.application.gedcom_jobs import GedcomJobFacade
from ancestryllm.application.operations import GedcomInspectRequest, GedcomInspectResult
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.jobs import JobState

if TYPE_CHECKING:
    from pathlib import Path

    from ancestryllm.application._artifacts import _ArtifactRegistry
    from ancestryllm.application.dto import ArtifactGrantRef
    from ancestryllm.application.jobs import JobLifecycleService, PublicJobSnapshot
    from ancestryllm.application.operations import RootCandidatePage
    from ancestryllm.application.ports import GedcomOperationsPort
    from ancestryllm.core.jobs import JobSnapshot

_CAPABILITY = re.compile(r"[0-9a-f]{64}\Z")
_TERMINAL = {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}
MAX_RETAINED_SOURCES = 8


def _unavailable() -> AncestryError:
    return AncestryError(
        "GEDCOM_JOB_RESULT_UNAVAILABLE",
        "The inspected source is unavailable in this session.",
        "Select the source again using the native file picker.",
    )


@dataclass(slots=True)
class _IntakeSource:
    path: Path
    grant: ArtifactGrantRef
    discarded: bool = False


class GedcomIntake:
    """Own a bounded set of staged read-only inputs and private job results."""

    def __init__(
        self,
        *,
        directory: Path,
        artifacts: _ArtifactRegistry,
        service: GedcomOperationsPort,
        jobs: JobLifecycleService,
    ) -> None:
        self._directory = directory.resolve(strict=True)
        if not self._directory.is_dir() or self._directory != directory.absolute():
            raise ValueError("Intake staging requires a canonical private directory.")
        self._artifacts = artifacts
        self._jobs = jobs
        self._facade = GedcomJobFacade(service=service, jobs=jobs)
        self._sources: dict[str, _IntakeSource] = {}
        self._lock = RLock()
        self._closed = False
        self._unsubscribe = jobs.manager.subscribe(self._on_job)

    def submit(self, stage_id: str, size_bytes: int, sha256: str) -> PublicJobSnapshot:
        """Consume one main-issued staging capability, never a renderer path."""
        if (
            not isinstance(stage_id, str)
            or not _CAPABILITY.fullmatch(stage_id)
            or not isinstance(sha256, str)
            or not _CAPABILITY.fullmatch(sha256)
            or type(size_bytes) is not int
            or not 0 <= size_bytes <= 512 * 1024 * 1024
        ):
            raise AncestryError("GEDCOM_INTAKE_INVALID", "Invalid staged GEDCOM capability.")
        with self._lock:
            if self._closed:
                raise _unavailable()
            path = self._directory / f"{stage_id}.ged"
            if len(self._sources) >= MAX_RETAINED_SOURCES:
                raise AncestryError(
                    "GEDCOM_INTAKE_CAPACITY",
                    "At most eight inspected sources may be retained.",
                    "Remove an existing source before selecting another.",
                )
            if any(source.path == path for source in self._sources.values()):
                raise AncestryError("GEDCOM_INTAKE_INVALID", "The capability was already consumed.")
            grant = self._artifacts.grant_input(
                path,
                operation="gedcom.inspect",
                media_type="text/vnd.gedcom",
                artifact_type="gedcom",
            )
            try:
                submitted = self._facade.submit_inspect(
                    GedcomInspectRequest(
                        source=grant, expected_sha256=sha256, expected_size_bytes=size_bytes
                    )
                )
            except BaseException:
                self._artifacts.revoke(grant)
                path.unlink(missing_ok=True)
                raise
            self._sources[submitted.job_id] = _IntakeSource(path, grant)
            # A tiny job may complete before registration; reconcile that race.
            self._on_job(self._jobs.manager.get(submitted.job_id))
            return submitted

    def _on_job(self, snapshot: JobSnapshot) -> None:
        if snapshot.state not in _TERMINAL:
            return
        with self._lock:
            source = self._sources.get(snapshot.job_id)
            if source is None:
                return
            self._artifacts.revoke(source.grant)
            source.path.unlink(missing_ok=True)
            if source.discarded or self._closed:
                self._jobs.manager.discard_result(snapshot.job_id)
                self._sources.pop(snapshot.job_id, None)
                if self._closed and not self._sources:
                    self._unsubscribe()

    def _require(self, job_id: str) -> None:
        source = self._sources.get(job_id)
        if self._closed or source is None or source.discarded:
            raise _unavailable()

    def result(self, job_id: str) -> GedcomInspectResult:
        """Read a retained private inspection only within its owning session."""
        with self._lock:
            self._require(job_id)
            result = self._facade.result(job_id)
            if not isinstance(result, GedcomInspectResult):
                raise _unavailable()
            return result

    def roots(
        self, job_id: str, *, query: str = "", limit: int = 25, cursor: str | None = None
    ) -> RootCandidatePage:
        """Return one bounded, source-bound page without retaining query text."""
        with self._lock:
            self._require(job_id)
            return self._facade.root_candidates(job_id, query=query, limit=limit, cursor=cursor)

    def discard(self, job_id: str) -> None:
        """Forget private results and cancel unfinished inspection; idempotent."""
        with self._lock:
            source = self._sources.get(job_id)
            if source is None:
                return
            source.discarded = True
            self._jobs.manager.discard_result(job_id)
            snapshot = self._jobs.manager.cancel(job_id)
            self._on_job(snapshot)

    def close(self) -> None:
        """Revoke session access immediately, cleaning running jobs on completion."""
        with self._lock:
            self._closed = True
            for job_id in tuple(self._sources):
                self.discard(job_id)
            if not self._sources:
                self._unsubscribe()
