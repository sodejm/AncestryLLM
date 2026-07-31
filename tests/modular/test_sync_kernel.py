"""Executable contracts for the staged incremental-sync kernel."""

from __future__ import annotations

import json
from dataclasses import fields, replace

import pytest

import ancestryllm.gedcom.sync_kernel as sync_kernel_module
from ancestryllm.application.dto import (
    DecisionKind,
    DecisionOption,
    DecisionResponse,
    ProgressUpdate,
)
from ancestryllm.application.dto import (
    DecisionRequest as ApplicationDecisionRequest,
)
from ancestryllm.domain.errors import DomainFailure, DomainFailureCode
from ancestryllm.gedcom.sync import SyncApplicationCoordinator
from ancestryllm.gedcom.sync_kernel import (
    SyncCancelled,
    SyncChangeKind,
    SyncDecisionRequest,
    SyncDecisionSelection,
    SyncDelta,
    SyncDocument,
    SyncEvent,
    SyncKernel,
    SyncLossEntry,
    SyncLossReport,
    SyncOperation,
    SyncOptions,
    SyncOutcome,
    SyncPlan,
    SyncPlanningOutput,
    SyncPublication,
    SyncRecoveryContext,
    SyncRecoveryMetadata,
    SyncRequest,
    SyncSnapshot,
    SyncSnapshotState,
    SyncStage,
    SyncStagedApplication,
    SyncStageError,
)

MASTER_DIGEST = "a" * 64
BEFORE_DIGEST = "b" * 64
AFTER_DIGEST = "c" * 64
MANIFEST_DIGEST = "d" * 64
SNAPSHOT_DIGEST = "e" * 64


def _document(
    reference: str = "document:master",
    digest: str = MASTER_DIGEST,
) -> SyncDocument:
    return SyncDocument(reference, digest, 3, ("source:fixture",))


def _snapshot() -> SyncSnapshot:
    return SyncSnapshot(
        "source:rootsmagic",
        "ROOTSMAGIC",
        "2026-07-30T12:00:00Z",
        _document("document:snapshot", SNAPSHOT_DIGEST),
    )


def _delta(
    subject_ref: str = "record:I1",
    kind: SyncChangeKind = SyncChangeKind.UPDATE,
) -> SyncDelta:
    return SyncDelta(
        subject_ref,
        kind,
        BEFORE_DIGEST,
        AFTER_DIGEST,
        ("source:rootsmagic",),
    )


def _decision() -> SyncDecisionRequest:
    return SyncDecisionRequest(
        "decision:manual-deletion",
        "SYNC_MANUAL_DELETION",
        ("record:I1",),
        ("ACCEPT", "KEEP"),
        ("ACCEPT",),
    )


def _request(
    *,
    dry_run: bool = False,
    replayed: tuple[SyncDecisionSelection, ...] = (),
) -> SyncRequest:
    return SyncRequest(
        operation_id="operation:fixture",
        master=_document(),
        manifest=_document("document:manifest", MANIFEST_DIGEST),
        snapshots=(_snapshot(),),
        options=SyncOptions(SyncOperation.REBASE, dry_run=dry_run),
        replayed_decisions=replayed,
    )


class _Harness:
    def __init__(
        self,
        *,
        deltas: tuple[SyncDelta, ...] | None = None,
        decisions: tuple[SyncDecisionRequest, ...] | None = None,
        fail_stage: SyncStage | None = None,
        recovery_fails: bool = False,
        prior_preserved: bool = True,
        staged_removed: bool | None = None,
    ) -> None:
        self.deltas = (_delta(),) if deltas is None else deltas
        self.decision_requests = (_decision(),) if decisions is None else decisions
        self.fail_stage = fail_stage
        self.recovery_fails = recovery_fails
        self.prior_preserved = prior_preserved
        self.staged_removed = staged_removed
        self.published = False
        self.calls: list[str] = []
        self.events: list[SyncEvent] = []
        self.recovery_contexts: list[SyncRecoveryContext] = []

    def _fail_if_requested(self, stage: SyncStage) -> None:
        if self.fail_stage is stage:
            raise SyncStageError(f"FAIL_{stage.name}")

    def capture(self, request: SyncRequest) -> SyncSnapshotState:
        self.calls.append("snapshot")
        self._fail_if_requested(SyncStage.SNAPSHOT)
        return SyncSnapshotState.create(
            state_ref="state:fixture",
            master=request.master,
            manifest=request.manifest,
            snapshots=request.snapshots,
        )

    def compare(
        self,
        snapshot: SyncSnapshotState,
        options: SyncOptions,
    ) -> tuple[SyncDelta, ...]:
        del snapshot, options
        self.calls.append("comparison")
        self._fail_if_requested(SyncStage.COMPARISON)
        return self.deltas

    def plan(
        self,
        snapshot: SyncSnapshotState,
        deltas: tuple[SyncDelta, ...],
        options: SyncOptions,
    ) -> SyncPlanningOutput:
        del snapshot, options
        self.calls.append("planning")
        self._fail_if_requested(SyncStage.PLANNING)
        return SyncPlanningOutput(deltas, self.decision_requests)

    def decide(self, request: SyncDecisionRequest) -> SyncDecisionSelection:
        self.calls.append("decision")
        self._fail_if_requested(SyncStage.DECISION)
        return SyncDecisionSelection(request.decision_id, "KEEP")

    def stage(
        self,
        snapshot: SyncSnapshotState,
        plan: SyncPlan,
        decisions: tuple[SyncDecisionSelection, ...],
    ) -> SyncStagedApplication:
        del snapshot, decisions
        self.calls.append("application")
        self._fail_if_requested(SyncStage.APPLICATION)
        return SyncStagedApplication(
            "staging:fixture",
            plan.plan_id,
            ("artifact:gedcom", "artifact:manifest"),
        )

    def prepare(self, staged: SyncStagedApplication) -> SyncPublication:
        self.calls.append("prepare")
        self._fail_if_requested(SyncStage.COMMIT)
        return SyncPublication(
            "revision:fixture",
            staged.plan_id,
            staged.artifact_refs,
        )

    def commit(
        self,
        staged: SyncStagedApplication,
        publication: SyncPublication,
    ) -> None:
        del staged, publication
        self.calls.append("commit")
        self.published = True

    def recover(self, context: SyncRecoveryContext) -> SyncRecoveryMetadata:
        self.calls.append("recovery")
        self.recovery_contexts.append(context)
        if self.recovery_fails:
            raise RuntimeError("fictional recovery failure")
        return SyncRecoveryMetadata(
            "recovery:fixture",
            self.prior_preserved,
            (
                context.staging_ref is not None or context.failed_stage is SyncStage.APPLICATION
                if self.staged_removed is None
                else self.staged_removed
            ),
            "SYNC_RECOVERED",
        )

    def emit(self, event: SyncEvent) -> None:
        self.events.append(event)


class _CancelAt:
    def __init__(self, checkpoint: int) -> None:
        self.checkpoint = checkpoint
        self.calls = 0

    def check_cancelled(self) -> None:
        self.calls += 1
        if self.calls == self.checkpoint:
            raise SyncCancelled()


class _ApplicationDecisionPort:
    def __init__(
        self,
        response: DecisionResponse | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.response = response
        self.failure = failure
        self.requests: list[ApplicationDecisionRequest] = []

    def decide(self, request: ApplicationDecisionRequest) -> DecisionResponse:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return self.response or DecisionResponse(request.decision_id, "KEEP")


class _ApplicationProgressPort:
    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.updates: list[ProgressUpdate] = []

    def emit(self, update: ProgressUpdate) -> None:
        self.updates.append(update)
        if update.stage == self.fail_on:
            raise RuntimeError("fictional progress adapter failure")


class _ApplicationCancellationPort:
    def __init__(self, cancel_at: int) -> None:
        self.cancel_at = cancel_at
        self.calls = 0

    def check_cancelled(self) -> None:
        self.calls += 1
        if self.calls == self.cancel_at:
            raise DomainFailure(DomainFailureCode.CANCELLED)


def _kernel(
    harness: _Harness,
    *,
    cancellation: _CancelAt | None = None,
) -> SyncKernel:
    return SyncKernel(
        snapshot=harness,
        comparison=harness,
        planning=harness,
        decisions=harness,
        application=harness,
        commit=harness,
        recovery=harness,
        cancellation=cancellation,
        events=harness,
    )


def _coordinator(
    harness: _Harness,
    *,
    decisions: _ApplicationDecisionPort | None = None,
    cancellation: _ApplicationCancellationPort | None = None,
    progress: _ApplicationProgressPort | None = None,
) -> SyncApplicationCoordinator:
    return SyncApplicationCoordinator(
        snapshot=harness,
        comparison=harness,
        planning=harness,
        decisions=decisions or _ApplicationDecisionPort(),
        application=harness,
        commit=harness,
        recovery=harness,
        cancellation=cancellation,
        progress=progress,
    )


def test_plan_is_content_addressed_and_independent_of_delta_order() -> None:
    state = SyncSnapshotState.create(
        state_ref="state:fixture",
        master=_document(),
        manifest=None,
        snapshots=(_snapshot(),),
    )
    create = _delta("record:I2", SyncChangeKind.CREATE)
    update = _delta("record:I1", SyncChangeKind.UPDATE)
    losses = (
        SyncLossEntry("SYNC_UNSUPPORTED_TAG", 1, ("record:I2",)),
        SyncLossEntry("SYNC_UNATTRIBUTED_VALUE", 2, ("record:I1",)),
    )

    first = SyncPlan.create(
        operation_id="operation:fixture",
        options=SyncOptions(SyncOperation.UPDATE),
        input_fingerprint=state.input_fingerprint,
        output=SyncPlanningOutput((update, create), (_decision(),), losses),
    )
    second = SyncPlan.create(
        operation_id="operation:fixture",
        options=SyncOptions(SyncOperation.UPDATE),
        input_fingerprint=state.input_fingerprint,
        output=SyncPlanningOutput(
            (create, update),
            (_decision(),),
            tuple(reversed(losses)),
        ),
    )

    assert first == second
    assert first.plan_id.startswith("plan:")
    assert tuple(entry.sequence for entry in first.entries) == (0, 1)
    assert first.to_json() == second.to_json()
    assert json.loads(first.to_json())["entries"][0]["kind"] == "create"
    assert first.loss_report == SyncLossReport.create(losses)
    assert [entry.loss_code for entry in first.loss_report.entries] == [
        "SYNC_UNATTRIBUTED_VALUE",
        "SYNC_UNSUPPORTED_TAG",
    ]


def test_loss_report_aggregates_duplicate_logical_categories() -> None:
    report = SyncLossReport.create(
        (
            SyncLossEntry("SYNC_UNSUPPORTED_TAG", 2, ("record:I1",)),
            SyncLossEntry("SYNC_UNSUPPORTED_TAG", 3, ("record:I1",)),
        )
    )

    assert report.entries == (SyncLossEntry("SYNC_UNSUPPORTED_TAG", 5, ("record:I1",)),)
    with pytest.raises(ValueError, match="aggregated"):
        SyncLossReport(
            (
                SyncLossEntry("SYNC_UNSUPPORTED_TAG", 2, ("record:I1",)),
                SyncLossEntry("SYNC_UNSUPPORTED_TAG", 3, ("record:I1",)),
            )
        )


def test_reconstructed_plan_rejects_an_action_id_that_does_not_match_content() -> None:
    state = SyncSnapshotState.create(
        state_ref="state:fixture",
        master=_document(),
        manifest=None,
        snapshots=(_snapshot(),),
    )
    plan = SyncPlan.create(
        operation_id="operation:fixture",
        options=SyncOptions(SyncOperation.UPDATE),
        input_fingerprint=state.input_fingerprint,
        output=SyncPlanningOutput((_delta(),)),
    )
    tampered = replace(plan.entries[0], action_id="action:different")

    with pytest.raises(ValueError, match="action_id"):
        replace(plan, entries=(tampered,))


def test_plan_identity_includes_every_plan_affecting_option() -> None:
    state = SyncSnapshotState.create(
        state_ref="state:fixture",
        master=_document(),
        manifest=_document("document:manifest", MANIFEST_DIGEST),
        snapshots=(_snapshot(),),
    )
    output = SyncPlanningOutput((_delta(),))
    options = (
        SyncOptions(SyncOperation.UPDATE),
        SyncOptions(SyncOperation.UPDATE, gedcom_version="5.5.1"),
        SyncOptions(SyncOperation.UPDATE, initialize_manifest=True),
        SyncOptions(SyncOperation.REBASE),
        SyncOptions(SyncOperation.REBASE, accept_manual_deletions=True),
    )

    plans = tuple(
        SyncPlan.create(
            operation_id="operation:fixture",
            options=item,
            input_fingerprint=state.input_fingerprint,
            output=output,
        )
        for item in options
    )

    assert len({plan.plan_id for plan in plans}) == len(options)
    assert tuple(plan.options for plan in plans) == options
    encoded = json.loads(plans[-1].to_json())
    assert encoded["options"]["accept_manual_deletions"] is True
    assert encoded["options"]["gedcom_version"] == "5.5.5"


def test_snapshot_state_rejects_a_fingerprint_not_derived_from_its_inputs() -> None:
    with pytest.raises(ValueError, match="does not match"):
        SyncSnapshotState(
            "state:fixture",
            _document(),
            _document("document:manifest", MANIFEST_DIGEST),
            (_snapshot(),),
            "f" * 64,
        )


def test_tombstone_conflict_and_rebase_actions_remain_explicit() -> None:
    state = SyncSnapshotState.create(
        state_ref="state:fixture",
        master=_document(),
        manifest=_document("document:manifest", MANIFEST_DIGEST),
        snapshots=(_snapshot(),),
    )
    deltas = (
        _delta("record:I3", SyncChangeKind.REBASE),
        _delta("record:I2", SyncChangeKind.CONFLICT),
        _delta("record:I1", SyncChangeKind.TOMBSTONE),
    )
    conflict = SyncDecisionRequest(
        "decision:conflict",
        "SYNC_CONFLICT",
        ("record:I2",),
        ("KEEP_MASTER", "USE_SNAPSHOT"),
    )

    plan = SyncPlan.create(
        operation_id="operation:fixture",
        options=SyncOptions(SyncOperation.REBASE),
        input_fingerprint=state.input_fingerprint,
        output=SyncPlanningOutput(deltas, (conflict,)),
    )

    assert tuple(entry.kind for entry in plan.entries) == (
        SyncChangeKind.CONFLICT,
        SyncChangeKind.REBASE,
        SyncChangeKind.TOMBSTONE,
    )
    assert plan.decisions == (conflict,)


def test_replayed_decision_is_used_without_calling_decision_adapter() -> None:
    replayed = SyncDecisionSelection("decision:manual-deletion", "ACCEPT")
    harness = _Harness()

    result = _kernel(harness).execute(_request(replayed=(replayed,)))

    assert result.outcome is SyncOutcome.COMMITTED
    assert result.decisions == (replayed,)
    assert "decision" not in harness.calls
    assert harness.calls == [
        "snapshot",
        "comparison",
        "planning",
        "application",
        "prepare",
        "commit",
    ]


def test_no_change_is_repeatable_and_never_stages_or_publishes() -> None:
    harness = _Harness(deltas=(), decisions=())
    kernel = _kernel(harness)

    first = kernel.execute(_request())
    second = kernel.execute(_request())

    assert first.outcome is second.outcome is SyncOutcome.NO_CHANGE
    assert first.plan == second.plan
    assert first.plan is not None
    assert first.plan.entries == ()
    assert set(harness.calls) == {"snapshot", "comparison", "planning"}


def test_no_change_result_rejects_a_nonempty_plan() -> None:
    dry_run = _kernel(_Harness()).execute(_request(dry_run=True))

    with pytest.raises(ValueError, match="No-change"):
        replace(dry_run, outcome=SyncOutcome.NO_CHANGE)


def test_planning_may_return_the_same_delta_multiset_in_a_different_order() -> None:
    class ReorderedPlanning(_Harness):
        def plan(
            self,
            snapshot: SyncSnapshotState,
            deltas: tuple[SyncDelta, ...],
            options: SyncOptions,
        ) -> SyncPlanningOutput:
            del snapshot, options
            self.calls.append("planning")
            return SyncPlanningOutput(tuple(reversed(deltas)), self.decision_requests)

    harness = ReorderedPlanning(
        deltas=(
            _delta("record:I1", SyncChangeKind.UPDATE),
            _delta("record:I2", SyncChangeKind.CREATE),
        )
    )

    result = _kernel(harness).execute(_request())

    assert result.outcome is SyncOutcome.COMMITTED
    assert result.plan is not None
    assert tuple(entry.kind for entry in result.plan.entries) == (
        SyncChangeKind.CREATE,
        SyncChangeKind.UPDATE,
    )


def test_dry_run_returns_plan_without_deciding_staging_or_publishing() -> None:
    harness = _Harness()

    result = _kernel(harness).execute(_request(dry_run=True))

    assert result.outcome is SyncOutcome.DRY_RUN
    assert result.plan is not None
    assert result.plan.decisions == (_decision(),)
    assert harness.calls == ["snapshot", "comparison", "planning"]


def test_success_uses_declared_stage_order_and_atomic_publication_boundary() -> None:
    harness = _Harness()

    result = _kernel(harness).execute(_request())

    assert result.committed
    assert result.publication is not None
    assert result.publication.revision_ref == "revision:fixture"
    assert harness.calls == [
        "snapshot",
        "comparison",
        "planning",
        "decision",
        "application",
        "prepare",
        "commit",
    ]
    assert [
        (event.stage, event.phase.value)
        for event in result.events
        if event.stage is not SyncStage.RECOVERY
    ] == [
        (SyncStage.SNAPSHOT, "started"),
        (SyncStage.SNAPSHOT, "completed"),
        (SyncStage.COMPARISON, "started"),
        (SyncStage.COMPARISON, "completed"),
        (SyncStage.PLANNING, "started"),
        (SyncStage.PLANNING, "completed"),
        (SyncStage.DECISION, "started"),
        (SyncStage.DECISION, "completed"),
        (SyncStage.APPLICATION, "started"),
        (SyncStage.APPLICATION, "completed"),
        (SyncStage.COMMIT, "started"),
        (SyncStage.COMMIT, "completed"),
    ]


def test_repeated_success_with_identical_inputs_is_idempotent() -> None:
    harness = _Harness()
    kernel = _kernel(harness)

    first = kernel.execute(_request())
    second = kernel.execute(_request())

    assert first == second
    assert first.plan is not None
    assert first.publication is not None
    assert first.plan.plan_id == second.plan.plan_id
    assert first.publication == second.publication


@pytest.mark.parametrize("checkpoint", range(1, 13))
def test_cancellation_at_every_interruptible_boundary_preserves_prior_revision(
    checkpoint: int,
) -> None:
    harness = _Harness()

    result = _kernel(harness, cancellation=_CancelAt(checkpoint)).execute(_request())

    assert result.outcome is SyncOutcome.CANCELLED
    assert result.error_code == "SYNC_CANCELLED"
    assert result.publication is None
    assert "commit" not in harness.calls
    assert harness.calls[-1] == "recovery"
    assert result.recovery is not None
    assert result.recovery.prior_revision_preserved


@pytest.mark.parametrize(
    "stage",
    (
        SyncStage.SNAPSHOT,
        SyncStage.COMPARISON,
        SyncStage.PLANNING,
        SyncStage.DECISION,
        SyncStage.APPLICATION,
        SyncStage.COMMIT,
    ),
)
def test_stage_failure_is_coded_and_recovered_without_publication(
    stage: SyncStage,
) -> None:
    harness = _Harness(fail_stage=stage)

    result = _kernel(harness).execute(_request())

    assert result.outcome is SyncOutcome.FAILED
    assert result.failed_stage is stage
    assert result.error_code == f"FAIL_{stage.name}"
    assert result.publication is None
    assert result.recovery is not None
    assert result.recovery.prior_revision_preserved
    assert harness.recovery_contexts == [
        SyncRecoveryContext(
            "operation:fixture",
            stage,
            f"FAIL_{stage.name}",
            (None if stage is SyncStage.SNAPSHOT else "state:fixture"),
            (
                None
                if stage in {SyncStage.SNAPSHOT, SyncStage.COMPARISON, SyncStage.PLANNING}
                else result.plan.plan_id
                if result.plan is not None
                else None
            ),
            ("staging:fixture" if stage is SyncStage.COMMIT else None),
        )
    ]


def test_recovery_failure_and_incomplete_recovery_are_explicit() -> None:
    failed = _kernel(_Harness(fail_stage=SyncStage.APPLICATION, recovery_fails=True)).execute(
        _request()
    )
    incomplete = _kernel(_Harness(fail_stage=SyncStage.APPLICATION, prior_preserved=False)).execute(
        _request()
    )

    assert failed.error_code == "SYNC_RECOVERY_FAILED"
    assert failed.recovery is None
    assert incomplete.error_code == "SYNC_RECOVERY_INCOMPLETE"
    assert incomplete.recovery is not None
    assert not incomplete.recovery.prior_revision_preserved


def test_failed_application_attempt_requires_explicit_staging_cleanup() -> None:
    result = _kernel(
        _Harness(
            fail_stage=SyncStage.APPLICATION,
            staged_removed=False,
        )
    ).execute(_request())

    assert result.error_code == "SYNC_RECOVERY_INCOMPLETE"
    assert result.recovery is not None
    assert not result.recovery.staged_state_removed


def test_staged_state_cleanup_is_required_for_complete_recovery() -> None:
    result = _kernel(
        _Harness(
            fail_stage=SyncStage.COMMIT,
            prior_preserved=True,
            staged_removed=False,
        )
    ).execute(_request())

    assert result.error_code == "SYNC_RECOVERY_INCOMPLETE"
    assert result.recovery is not None
    assert result.recovery.prior_revision_preserved
    assert not result.recovery.staged_state_removed


def test_unknown_or_invalid_replayed_decisions_fail_before_application() -> None:
    unknown = SyncDecisionSelection("decision:unknown", "ACCEPT")
    invalid = SyncDecisionSelection("decision:manual-deletion", "REJECT")

    for selection, expected in (
        (unknown, "SYNC_DECISION_UNKNOWN"),
        (invalid, "SYNC_DECISION_OPTION_INVALID"),
    ):
        harness = _Harness()
        result = _kernel(harness).execute(_request(replayed=(selection,)))

        assert result.error_code == expected
        assert result.failed_stage is SyncStage.DECISION
        assert "application" not in harness.calls
        assert "commit" not in harness.calls


def test_completed_decisions_are_retained_when_a_later_decision_is_cancelled() -> None:
    first = SyncDecisionRequest(
        "decision:first",
        "SYNC_SELECT",
        ("record:I1",),
        ("KEEP",),
    )
    second = SyncDecisionRequest(
        "decision:second",
        "SYNC_SELECT",
        ("record:I2",),
        ("KEEP",),
    )

    class CancelSecondDecision(_Harness):
        def __init__(self) -> None:
            super().__init__(decisions=(first, second))
            self.decision_count = 0

        def decide(self, request: SyncDecisionRequest) -> SyncDecisionSelection:
            self.calls.append("decision")
            self.decision_count += 1
            if self.decision_count == 2:
                raise SyncCancelled()
            return SyncDecisionSelection(request.decision_id, "KEEP")

    result = _kernel(CancelSecondDecision()).execute(_request())

    assert result.outcome is SyncOutcome.CANCELLED
    assert result.decisions == (SyncDecisionSelection("decision:first", "KEEP"),)


def test_recovery_metadata_and_events_cannot_contain_paths_or_payload_fields() -> None:
    unsafe_names = {
        "argv",
        "content",
        "document",
        "gedcom",
        "message",
        "path",
        "payload",
        "prompt",
        "response",
    }

    for contract in (SyncRecoveryContext, SyncRecoveryMetadata, SyncEvent):
        assert {field.name for field in fields(contract)}.isdisjoint(unsafe_names)

    result = _kernel(_Harness(fail_stage=SyncStage.APPLICATION)).execute(_request())
    encoded = result.to_json()
    assert "/private/" not in encoded
    assert "John Doe" not in encoded
    assert len(result.events) <= 256


def test_publication_must_match_staged_plan_and_artifact_references() -> None:
    class MismatchedCommit(_Harness):
        def prepare(self, staged: SyncStagedApplication) -> SyncPublication:
            self.calls.append("prepare")
            return SyncPublication(
                "revision:fixture",
                staged.plan_id,
                ("artifact:different",),
            )

    harness = MismatchedCommit()
    result = _kernel(harness).execute(_request())

    assert result.error_code == "SYNC_PUBLICATION_ARTIFACT_MISMATCH"
    assert result.failed_stage is SyncStage.COMMIT
    assert result.publication is None
    assert result.recovery is not None
    assert result.recovery.prior_revision_preserved
    assert not harness.published


@pytest.mark.parametrize(
    "decision_id",
    (
        "decision+invalid",
        "decision@invalid",
        "decision..invalid",
        "d" * 129,
    ),
)
def test_kernel_decision_ids_match_application_dto_constraints(
    decision_id: str,
) -> None:
    with pytest.raises(ValueError):
        ApplicationDecisionRequest(
            decision_id,
            "GEDCOM_SYNC",
            "SYNC_SELECT",
            DecisionKind.CONFIRM,
            (DecisionOption("KEEP", "KEEP"),),
        )
    with pytest.raises(ValueError):
        SyncDecisionRequest(
            decision_id,
            "SYNC_SELECT",
            (),
            ("KEEP",),
        )
    with pytest.raises(ValueError):
        SyncDecisionSelection(decision_id, "KEEP")


def test_decision_options_are_bounded_to_the_application_port_limit() -> None:
    option_ids = tuple(f"OPTION_{index:02d}" for index in range(33))

    with pytest.raises(ValueError, match="option limit"):
        SyncDecisionRequest(
            "decision:too-many-options",
            "SYNC_SELECT",
            (),
            option_ids,
        )


def test_sync_request_enforces_aggregate_serialized_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sync_kernel_module, "MAX_SYNC_JSON_BYTES", 1)

    with pytest.raises(ValueError, match="Serialized sync contract"):
        _request()


def test_application_coordinator_maps_decisions_and_structural_progress() -> None:
    harness = _Harness()
    decisions = _ApplicationDecisionPort()
    progress = _ApplicationProgressPort()

    result = _coordinator(
        harness,
        decisions=decisions,
        progress=progress,
    ).execute(_request())

    assert result.outcome is SyncOutcome.COMMITTED
    assert len(decisions.requests) == 1
    request = decisions.requests[0]
    assert request.kind is DecisionKind.CONFIRM
    assert tuple((option.option_id, option.destructive) for option in request.options) == (
        ("ACCEPT", True),
        ("KEEP", False),
    )
    assert [update.stage for update in progress.updates] == [event.code for event in result.events]
    assert all(update.operation == "GEDCOM_SYNC" for update in progress.updates)
    assert all(update.artifact_id is None for update in progress.updates)


def test_application_coordinator_uses_explicit_destructive_option_metadata() -> None:
    decision = SyncDecisionRequest(
        "decision:tombstone",
        "SYNC_TOMBSTONE",
        ("record:I1",),
        ("APPLY", "DELETE_RECORD"),
        ("APPLY",),
    )
    harness = _Harness(decisions=(decision,))
    decisions = _ApplicationDecisionPort(DecisionResponse("decision:tombstone", "DELETE_RECORD"))

    result = _coordinator(harness, decisions=decisions).execute(_request())

    assert result.outcome is SyncOutcome.COMMITTED
    assert tuple(
        (option.option_id, option.destructive) for option in decisions.requests[0].options
    ) == (("APPLY", True), ("DELETE_RECORD", False))


def test_application_coordinator_maps_conflict_decisions() -> None:
    conflict = SyncDecisionRequest(
        "decision:conflict",
        "SYNC_CONFLICT",
        ("record:I1",),
        ("KEEP_MASTER", "USE_SNAPSHOT"),
    )
    harness = _Harness(decisions=(conflict,))
    decisions = _ApplicationDecisionPort(DecisionResponse("decision:conflict", "KEEP_MASTER"))

    result = _coordinator(harness, decisions=decisions).execute(_request())

    assert result.outcome is SyncOutcome.COMMITTED
    assert decisions.requests[0].kind is DecisionKind.RESOLVE_CONFLICT
    assert result.decisions == (SyncDecisionSelection("decision:conflict", "KEEP_MASTER"),)


def test_application_decision_cancellation_recovers_without_publication() -> None:
    harness = _Harness()
    decisions = _ApplicationDecisionPort(
        DecisionResponse("decision:manual-deletion", None, cancelled=True)
    )

    result = _coordinator(harness, decisions=decisions).execute(_request())

    assert result.outcome is SyncOutcome.CANCELLED
    assert result.error_code == "SYNC_CANCELLED"
    assert result.failed_stage is SyncStage.DECISION
    assert result.publication is None
    assert "application" not in harness.calls
    assert harness.calls[-1] == "recovery"


def test_application_cancellation_port_uses_the_coded_cancelled_outcome() -> None:
    harness = _Harness()

    result = _coordinator(
        harness,
        cancellation=_ApplicationCancellationPort(cancel_at=1),
    ).execute(_request())

    assert result.outcome is SyncOutcome.CANCELLED
    assert result.error_code == "SYNC_CANCELLED"
    assert result.failed_stage is SyncStage.SNAPSHOT
    assert result.publication is None
    assert harness.calls == ["recovery"]


@pytest.mark.parametrize(
    ("response", "expected_code"),
    (
        (
            DecisionResponse("decision:manual-deletion", None),
            "SYNC_DECISION_RESPONSE_INVALID",
        ),
        (
            DecisionResponse("decision:different", "KEEP"),
            "SYNC_DECISION_ID_MISMATCH",
        ),
        (
            DecisionResponse("decision:manual-deletion", "KEEP:LOCAL"),
            "SYNC_DECISION_OPTION_INVALID",
        ),
    ),
)
def test_invalid_application_decision_response_fails_before_application(
    response: DecisionResponse,
    expected_code: str,
) -> None:
    harness = _Harness()

    result = _coordinator(
        harness,
        decisions=_ApplicationDecisionPort(response),
    ).execute(_request())

    assert result.outcome is SyncOutcome.FAILED
    assert result.error_code == expected_code
    assert result.failed_stage is SyncStage.DECISION
    assert result.publication is None
    assert "application" not in harness.calls


def test_progress_failure_before_publication_is_coded_and_recovered() -> None:
    harness = _Harness()
    progress = _ApplicationProgressPort(fail_on="SYNC_APPLICATION_STARTED")

    result = _coordinator(harness, progress=progress).execute(_request())

    assert result.outcome is SyncOutcome.FAILED
    assert result.error_code == "SYNC_PROGRESS_PORT_FAILED"
    assert result.failed_stage is SyncStage.APPLICATION
    assert result.publication is None
    assert "application" not in harness.calls
    assert "commit" not in harness.calls
    assert harness.calls[-1] == "recovery"


def test_post_commit_progress_failure_cannot_invalidate_publication() -> None:
    harness = _Harness()
    progress = _ApplicationProgressPort(fail_on="SYNC_COMMIT_COMPLETED")

    result = _coordinator(harness, progress=progress).execute(_request())

    assert result.outcome is SyncOutcome.COMMITTED
    assert result.publication is not None
    assert result.error_code is None
    assert harness.calls[-1] == "commit"
    assert "recovery" not in harness.calls


@pytest.mark.parametrize(
    "unsafe_ref",
    (
        "../private/master.ged",
        "record:John Doe",
        "record:\nI1",
    ),
)
def test_contract_references_reject_locations_and_payload_text(
    unsafe_ref: str,
) -> None:
    with pytest.raises(ValueError, match="opaque reference"):
        _document(unsafe_ref)


@pytest.mark.parametrize(
    "exported_at",
    (
        "yesterday",
        "2026-13-01T00:00:00Z",
        "2026-02-30T00:00:00Z",
        "2026-01-01T24:00:00Z",
        "2026-01-01T00:60:00Z",
        "2026-01-01T00:00:00+24:00",
        "2026-01-01T00:00:00+00:60",
    ),
)
def test_snapshot_export_time_requires_valid_rfc3339_components(
    exported_at: str,
) -> None:
    with pytest.raises(ValueError, match="RFC 3339"):
        SyncSnapshot(
            "source:rootsmagic",
            "ROOTSMAGIC",
            exported_at,
            _document("document:snapshot", SNAPSHOT_DIGEST),
        )


@pytest.mark.parametrize("gedcom_version", ("5.5.1", "5.5.5"))
def test_sync_options_accept_only_advertised_gedcom_versions(
    gedcom_version: str,
) -> None:
    options = SyncOptions(SyncOperation.UPDATE, gedcom_version=gedcom_version)

    assert options.gedcom_version == gedcom_version


@pytest.mark.parametrize("gedcom_version", ("5.5", "7.0"))
def test_sync_options_reject_unadvertised_gedcom_versions(
    gedcom_version: str,
) -> None:
    with pytest.raises(ValueError, match="Unsupported GEDCOM version"):
        SyncOptions(SyncOperation.UPDATE, gedcom_version=gedcom_version)
