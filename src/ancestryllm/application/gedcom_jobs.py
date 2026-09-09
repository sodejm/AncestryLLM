"""Asynchronous, transport-neutral facade for GEDCOM application operations."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from typing import TYPE_CHECKING

from ancestryllm.application.errors import map_domain_failure
from ancestryllm.application.jobs import JobLifecycleState
from ancestryllm.application.operations import (
    GedcomInspectRequest,
    GedcomInspectResult,
    MergeRequest,
    MergeResult,
    QualityRequest,
    QualityResult,
    RootCandidatePage,
    SubtreeRequest,
    SubtreeResult,
    SyncRequest,
    SyncResult,
)
from ancestryllm.core.cancellation import CancellationError
from ancestryllm.core.errors import AncestryError
from ancestryllm.core.jobs import JobState
from ancestryllm.domain.errors import DomainFailure, DomainFailureCode

if TYPE_CHECKING:
    from collections.abc import Callable

    from ancestryllm.application.dto import ArtifactGrantRef, ServiceResult
    from ancestryllm.application.jobs import JobLifecycleService, PublicJobSnapshot
    from ancestryllm.application.operations import RootCandidate
    from ancestryllm.application.ports import GedcomOperationsPort
    from ancestryllm.core.jobs import JobReporter

_GedcomResult = GedcomInspectResult | MergeResult | SubtreeResult | QualityResult | SyncResult
_CURSOR_SECRET = secrets.token_bytes(32)
_CURSOR_PATTERN = re.compile(r"c1_([0-9a-f]{8})_([0-9a-f]{64})\Z")


class GedcomJobFacade:
    """Submit GEDCOM service contracts without exposing paths or callbacks."""

    def __init__(
        self,
        *,
        service: GedcomOperationsPort,
        jobs: JobLifecycleService,
    ) -> None:
        self._service = service
        self._jobs = jobs

    def _submit(
        self,
        operation: str,
        grants: tuple[ArtifactGrantRef, ...],
        execute: Callable[[JobReporter], ServiceResult],
    ) -> PublicJobSnapshot:
        def work(reporter: JobReporter) -> ServiceResult:
            reporter.update(f"{operation}.running")
            try:
                result = execute(reporter)
            except DomainFailure as failure:
                if failure.code is DomainFailureCode.CANCELLED:
                    raise CancellationError("The operation was cancelled.") from failure
                raise map_domain_failure(failure) from failure
            reporter.set_outcome(
                "The GEDCOM operation completed.",
                next_action="Inspect the retained structured result.",
            )
            return result

        submitted = self._jobs.manager.submit_with_progress(
            operation,
            work,
            resource_keys=tuple(grant.grant_id for grant in grants),
        )
        return self._jobs.get(submitted.job_id)

    def submit_inspect(self, request: GedcomInspectRequest) -> PublicJobSnapshot:
        """Submit a deterministic source inspection."""

        return self._submit(
            "gedcom.inspect",
            (request.source,),
            lambda reporter: self._service.execute_inspect(
                request,
                cancellation=reporter,
            ),
        )

    def submit_merge(self, request: MergeRequest) -> PublicJobSnapshot:
        """Submit a merge through capability-scoped GEDCOM service contracts."""

        grants = [*request.inputs, request.output]
        if request.quality_report is not None:
            grants.append(request.quality_report)
        return self._submit(
            "gedcom.merge",
            tuple(grants),
            lambda reporter: self._service.execute_merge(
                request,
                cancellation=reporter,
            ),
        )

    def submit_subtree(self, request: SubtreeRequest) -> PublicJobSnapshot:
        """Submit a rooted subtree extraction."""

        return self._submit(
            "gedcom.subtree",
            (request.source, request.output),
            lambda reporter: self._service.execute_subtree(
                request,
                cancellation=reporter,
            ),
        )

    def submit_quality(self, request: QualityRequest) -> PublicJobSnapshot:
        """Submit a quality analysis and report publication."""

        return self._submit(
            "gedcom.quality",
            (request.source, request.output),
            lambda reporter: self._service.execute_quality(
                request,
                cancellation=reporter,
            ),
        )

    def submit_sync(self, request: SyncRequest) -> PublicJobSnapshot:
        """Submit a typed sync command with only declared artifact grants."""

        grants = [request.master, request.release_root]
        if request.manifest is not None:
            grants.append(request.manifest)
        grants.extend(snapshot.artifact for snapshot in request.snapshots)
        return self._submit(
            "gedcom.sync",
            tuple(grants),
            lambda reporter: self._service.execute_sync(
                request,
                cancellation=reporter,
            ),
        )

    def result(self, job_id: str) -> _GedcomResult:
        """Return one completed typed result retained outside public job state."""

        public_snapshot = self._jobs.get(job_id)
        if public_snapshot.state is not JobLifecycleState.COMPLETED:
            raise self._result_unavailable()
        try:
            snapshot = self._jobs.manager.get(job_id)
        except AncestryError as error:
            if error.code == "JOB_NOT_FOUND":
                raise self._result_unavailable() from error
            raise
        if snapshot.state is not JobState.COMPLETED:
            raise self._result_unavailable()
        if not isinstance(
            snapshot.result,
            (
                GedcomInspectResult,
                MergeResult,
                SubtreeResult,
                QualityResult,
                SyncResult,
            ),
        ):
            raise AncestryError(
                "GEDCOM_JOB_RESULT_INVALID",
                "The completed GEDCOM job retained an unsupported result.",
                "Review the coded job outcome before retrying.",
            )
        return snapshot.result

    @staticmethod
    def _cursor(job_id: str, result: GedcomInspectResult, query: str, offset: int) -> str:
        binding = json.dumps(
            (
                job_id,
                result.summary.source.artifact_id,
                result.summary.source.sha256,
                query,
                offset,
            ),
            ensure_ascii=True,
        ).encode()
        signature = hmac.new(_CURSOR_SECRET, binding, hashlib.sha256).hexdigest()
        return f"c1_{offset:08x}_{signature}"

    def root_candidates(
        self,
        job_id: str,
        *,
        query: str = "",
        limit: int = 25,
        cursor: str | None = None,
    ) -> RootCandidatePage:
        """Search retained inspection metadata using a job/query-bound opaque cursor."""
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or not isinstance(query, str)
            or len(query) > 128
        ):
            raise AncestryError(
                "GEDCOM_ROOT_QUERY_INVALID",
                "The root candidate query exceeds its limits.",
                "Use at most 128 search characters and a page size between 1 and 100.",
            )
        result = self.result(job_id)
        if not isinstance(result, GedcomInspectResult):
            raise self._result_unavailable()
        normalized_query = query.strip().casefold()
        is_person_ref = normalized_query.startswith("person:")
        offset = 0
        if cursor is not None:
            match = _CURSOR_PATTERN.fullmatch(cursor) if isinstance(cursor, str) else None
            if match is not None:
                offset = int(match[1], 16)
            if (
                match is None
                or offset >= len(result.root_candidates)
                or not hmac.compare_digest(
                    cursor, self._cursor(job_id, result, normalized_query, offset)
                )
            ):
                raise AncestryError(
                    "GEDCOM_ROOT_CURSOR_INVALID",
                    "The root candidate cursor is invalid or stale.",
                    "Restart the search for this inspected source.",
                )
        if not normalized_query:
            total_count = len(result.root_candidates)
            candidates = result.root_candidates[offset : offset + limit]
            next_offset = offset + len(candidates)
            return RootCandidatePage(
                candidates=candidates,
                total_count=total_count,
                next_cursor=(
                    self._cursor(job_id, result, normalized_query, next_offset)
                    if next_offset < total_count
                    else None
                ),
            )
        candidates: list[RootCandidate] = []
        total_count = 0
        next_offset = None
        for index, candidate in enumerate(result.root_candidates):
            if is_person_ref:
                # Finding anchors never fall back to a fuzzy match in imported text.
                if normalized_query != candidate.person_ref:
                    continue
            else:
                searchable = " ".join(
                    (
                        candidate.display_name,
                        candidate.source_identifier,
                        candidate.birth_date,
                        candidate.death_date,
                    )
                ).casefold()
                if normalized_query not in searchable:
                    continue
            total_count += 1
            if index < offset:
                continue
            if len(candidates) < limit:
                candidates.append(candidate)
            elif next_offset is None:
                next_offset = index
        return RootCandidatePage(
            candidates=tuple(candidates),
            total_count=total_count,
            next_cursor=(
                self._cursor(job_id, result, normalized_query, next_offset)
                if next_offset is not None
                else None
            ),
        )

    @staticmethod
    def _result_unavailable() -> AncestryError:
        return AncestryError(
            "GEDCOM_JOB_RESULT_UNAVAILABLE",
            "The GEDCOM job does not have a completed result.",
            "Wait for the job to finish successfully before requesting its result.",
            exit_code=2,
        )


__all__ = ["GedcomJobFacade"]
