"""Public incremental update/rebase entry point."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import NoReturn

from ancestryllm.application.dto import (
    DecisionKind,
    DecisionOption,
    DecisionRequest,
)
from ancestryllm.application.events import ProgressEvent
from ancestryllm.application.ports import (
    CancellationPort,
    DecisionPort,
    DiscardProgress,
    NeverCancelled,
    ProgressPort,
)
from ancestryllm.core.ingress import FileIngressPolicy
from ancestryllm.domain.errors import DomainFailure, DomainFailureCode
from ancestryllm.gedcom import sync_gedcom
from ancestryllm.gedcom.contracts import IdentityResolver
from ancestryllm.gedcom.sync_cli import execute
from ancestryllm.gedcom.sync_contracts import (
    SOURCE_ID_RE,
    SUPPORTED_VENDORS,
    CancellationCheck,
    ResolverFactory,
    SyncCommand,
    SyncExecutionResult,
    SyncRebaseCommand,
    SyncSnapshotInput,
    SyncUpdateCommand,
)
from ancestryllm.gedcom.sync_kernel import (
    ApplicationStage,
    CommitStage,
    ComparisonStage,
    PlanningStage,
    RecoveryStage,
    SnapshotStage,
    SyncCancelled,
    SyncDecisionRequest,
    SyncDecisionSelection,
    SyncEvent,
    SyncEventPhase,
    SyncKernel,
    SyncKernelResult,
    SyncRequest,
    SyncStageError,
)
from ancestryllm.gedcom.sync_operations import execute_command


def _raise_port_failure(exc: Exception, code: str) -> NoReturn:
    if isinstance(exc, DomainFailure) and exc.code is DomainFailureCode.CANCELLED:
        raise SyncCancelled() from exc
    raise SyncStageError(code) from exc


class _ApplicationCancellationStage:
    """Translate application cancellation into the kernel's coded signal."""

    __slots__ = ("_port",)

    def __init__(self, port: CancellationPort) -> None:
        self._port = port

    def check_cancelled(self) -> None:
        try:
            self._port.check_cancelled()
        except Exception as exc:  # noqa: BLE001 - application port boundary
            _raise_port_failure(exc, "SYNC_CANCELLATION_PORT_FAILED")


class _ApplicationDecisionStage:
    """Translate opaque sync decisions to the shared application port."""

    __slots__ = ("_port",)

    def __init__(self, port: DecisionPort) -> None:
        self._port = port

    @staticmethod
    def _kind(request: SyncDecisionRequest) -> DecisionKind:
        if "CONFLICT" in request.decision_code:
            return DecisionKind.RESOLVE_CONFLICT
        if len(request.option_ids) > 2:
            return DecisionKind.SELECT
        return DecisionKind.CONFIRM

    def decide(self, request: SyncDecisionRequest) -> SyncDecisionSelection:
        try:
            response = self._port.decide(
                DecisionRequest(
                    decision_id=request.decision_id,
                    operation="GEDCOM_SYNC",
                    decision_code=request.decision_code,
                    kind=self._kind(request),
                    options=tuple(
                        DecisionOption(
                            option_id=option_id,
                            label_code=option_id,
                            destructive=option_id in request.destructive_option_ids,
                        )
                        for option_id in request.option_ids
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001 - application port boundary
            _raise_port_failure(exc, "SYNC_DECISION_PORT_FAILED")
        if response.decision_id != request.decision_id:
            raise SyncStageError("SYNC_DECISION_ID_MISMATCH")
        if response.cancelled:
            raise SyncCancelled()
        if response.option_id is None:
            raise SyncStageError("SYNC_DECISION_RESPONSE_INVALID")
        if response.option_id not in request.option_ids:
            raise SyncStageError("SYNC_DECISION_OPTION_INVALID")
        return SyncDecisionSelection(response.decision_id, response.option_id)


class _ApplicationEventStage:
    """Translate private-data-free kernel events into progress DTOs."""

    __slots__ = ("_port",)

    def __init__(self, port: ProgressPort) -> None:
        self._port = port

    def emit(self, event: SyncEvent) -> None:
        item_count = event.item_count if event.phase is SyncEventPhase.COMPLETED else None
        try:
            self._port.emit(
                ProgressEvent(
                    operation="GEDCOM_SYNC",
                    stage=event.code,
                    sequence=event.sequence,
                    completed=item_count,
                    total=item_count,
                )
            )
        except Exception as exc:  # noqa: BLE001 - application port boundary
            _raise_port_failure(exc, "SYNC_PROGRESS_PORT_FAILED")


class SyncApplicationCoordinator:
    """Run a staged sync through the shared application-service ports.

    Stage adapters own input capture, unpublished application state, atomic
    publication, and recovery. The coordinator only translates the shared
    decision, cancellation, and structural-progress contracts.
    """

    __slots__ = ("_kernel",)

    def __init__(
        self,
        *,
        snapshot: SnapshotStage,
        comparison: ComparisonStage,
        planning: PlanningStage,
        decisions: DecisionPort,
        application: ApplicationStage,
        commit: CommitStage,
        recovery: RecoveryStage,
        cancellation: CancellationPort | None = None,
        progress: ProgressPort | None = None,
    ) -> None:
        self._kernel = SyncKernel(
            snapshot=snapshot,
            comparison=comparison,
            planning=planning,
            decisions=_ApplicationDecisionStage(decisions),
            application=application,
            commit=commit,
            recovery=recovery,
            cancellation=_ApplicationCancellationStage(
                cancellation if cancellation is not None else NeverCancelled()
            ),
            events=_ApplicationEventStage(progress if progress is not None else DiscardProgress()),
        )

    def execute(self, request: SyncRequest) -> SyncKernelResult:
        """Execute one typed operation without terminal or host-path state."""

        return self._kernel.execute(request)


def execute_sync(
    argv: Sequence[str],
    ingress: FileIngressPolicy | None = None,
    *,
    resolver_factory: ResolverFactory | None = None,
    cancellation_check: CancellationCheck | None = None,
    raise_errors: bool = False,
) -> SyncExecutionResult:
    """Return one structured sync result without writing to terminal streams."""

    return execute(
        list(argv),
        sync_gedcom,
        ingress,
        resolver_factory=resolver_factory,
        cancellation_check=cancellation_check,
        raise_errors=raise_errors,
    )


def execute_sync_command(
    command: SyncCommand,
    ingress: FileIngressPolicy | None = None,
    *,
    identity_resolver: IdentityResolver | None = None,
    cancellation_check: CancellationCheck | None = None,
    raise_errors: bool = False,
) -> SyncExecutionResult:
    """Execute one typed service command without synthesizing terminal arguments."""

    return execute_command(
        command,
        sync_gedcom,
        ingress,
        identity_resolver=identity_resolver,
        cancellation_check=cancellation_check,
        raise_errors=raise_errors,
    )


def _render_result(result: SyncExecutionResult) -> None:
    """Preserve the legacy console transcript outside the sync kernel."""

    if result.committed:
        try:
            if result.output:
                print(result.output, end="")
        except BaseException:  # noqa: BLE001 - rendering cannot invalidate an immutable commit
            return
    elif result.output:
        print(result.output, end="")
    if result.error:
        print(result.error, end="", file=sys.stderr)


def run_sync(
    argv: Sequence[str],
    ingress: FileIngressPolicy | None = None,
    *,
    resolver_factory: ResolverFactory | None = None,
    cancellation_check: CancellationCheck | None = None,
    raise_errors: bool = False,
) -> int:
    """Run sync and render its compatibility transcript for console callers."""

    result = execute_sync(
        argv,
        ingress,
        resolver_factory=resolver_factory,
        cancellation_check=cancellation_check,
        raise_errors=raise_errors,
    )
    _render_result(result)
    return result.exit_code


__all__ = [
    "SOURCE_ID_RE",
    "SUPPORTED_VENDORS",
    "SyncApplicationCoordinator",
    "SyncCommand",
    "SyncExecutionResult",
    "SyncRebaseCommand",
    "SyncSnapshotInput",
    "SyncUpdateCommand",
    "execute_sync",
    "execute_sync_command",
    "run_sync",
]
