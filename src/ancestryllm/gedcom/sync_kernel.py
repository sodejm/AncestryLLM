"""Pure staged contracts and coordinator for incremental GEDCOM synchronization.

The operation kernel deliberately knows nothing about host files, terminal
arguments, UI frameworks, provider clients, or publication mechanics.  Outer
adapters capture immutable documents, implement the stages, and translate the
bounded result and event records into application DTOs.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

MAX_ITEMS = 10_000
MAX_COUNT = 1_000_000_000
MAX_EVENTS = 256
MAX_DECISION_OPTIONS = 32
MAX_REF_LENGTH = 192
MAX_CODE_LENGTH = 96
MAX_SYNC_JSON_BYTES = 8 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,95}$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,191}$")
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$"
)


class SyncOperation(StrEnum):
    """Supported deterministic synchronization operations."""

    UPDATE = "update"
    REBASE = "rebase"


class SyncStage(StrEnum):
    """Ordered interruptible and non-interruptible operation stages."""

    SNAPSHOT = "snapshot"
    COMPARISON = "comparison"
    PLANNING = "planning"
    DECISION = "decision"
    APPLICATION = "application"
    COMMIT = "commit"
    RECOVERY = "recovery"


class SyncOutcome(StrEnum):
    """Terminal kernel outcomes."""

    COMMITTED = "committed"
    NO_CHANGE = "no-change"
    DRY_RUN = "dry-run"
    CANCELLED = "cancelled"
    FAILED = "failed"


class SyncChangeKind(StrEnum):
    """Stable change categories used by plans and accounting."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    TOMBSTONE = "tombstone"
    CONFLICT = "conflict"
    REBASE = "rebase"


class SyncEventPhase(StrEnum):
    """Structural event phases with no private payload content."""

    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


def _validate_ref(label: str, value: str) -> None:
    if len(value) > MAX_REF_LENGTH or _REF_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a bounded opaque reference.")


def _validate_decision_id(label: str, value: str) -> None:
    """Match the shared application decision DTO's identifier contract."""

    if not 1 <= len(value) <= 128:
        raise ValueError(f"{label} length is outside its bounded range.")
    if not all(character.isalnum() or character in "._:-" for character in value):
        raise ValueError(f"{label} contains unsupported characters.")
    if ".." in value:
        raise ValueError(f"{label} must not contain paths or control characters.")


def _validate_code(label: str, value: str) -> None:
    if len(value) > MAX_CODE_LENGTH or _CODE_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a bounded uppercase code.")


def _validate_sha256(label: str, value: str) -> None:
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest.")


def _validate_count(label: str, value: int) -> None:
    if isinstance(value, bool) or not 0 <= value <= MAX_COUNT:
        raise ValueError(f"{label} is outside its bounded range.")


def _validate_rfc3339(label: str, value: str) -> None:
    """Validate both RFC 3339 shape and calendar/time component ranges."""

    if _RFC3339_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a bounded RFC 3339 timestamp.")
    hour = int(value[11:13])
    minute = int(value[14:16])
    second = int(value[17:19])
    if hour > 23 or minute > 59 or second > 59:
        raise ValueError(f"{label} must be a bounded RFC 3339 timestamp.")
    if not value.endswith("Z"):
        offset_hour = int(value[-5:-3])
        offset_minute = int(value[-2:])
        if offset_hour > 23 or offset_minute > 59:
            raise ValueError(f"{label} must be a bounded RFC 3339 timestamp.")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be a bounded RFC 3339 timestamp.") from exc


def _encode(value: object) -> object:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("Sync contract mapping keys must be strings.")
        return {key: _encode(item) for key, item in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        fields = value.__dataclass_fields__
        return {name: _encode(getattr(value, name)) for name in fields}
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    raise TypeError(f"Unsupported sync contract value: {type(value).__name__}")


def _canonical_json(value: object) -> str:
    return json.dumps(
        _encode(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class SyncValue:
    """Deterministic JSON mixin for immutable operation values."""

    __slots__ = ()

    def to_json(self) -> str:
        """Return canonical JSON with stable key and collection ordering."""

        encoded = _canonical_json(self)
        if len(encoded.encode("utf-8")) > MAX_SYNC_JSON_BYTES:
            raise ValueError("Serialized sync contract exceeds its bounded size.")
        return encoded


@dataclass(frozen=True, slots=True)
class SyncDocument(SyncValue):
    """One immutable document identified only by safe structural metadata."""

    document_ref: str
    sha256: str
    record_count: int
    provenance_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_ref("document_ref", self.document_ref)
        _validate_sha256("sha256", self.sha256)
        _validate_count("record_count", self.record_count)
        if len(self.provenance_refs) > MAX_ITEMS:
            raise ValueError("provenance_refs exceeds its bounded size.")
        if self.provenance_refs != tuple(sorted(set(self.provenance_refs))):
            raise ValueError("provenance_refs must be unique and deterministically ordered.")
        for reference in self.provenance_refs:
            _validate_ref("provenance_ref", reference)


@dataclass(frozen=True, slots=True)
class SyncSnapshot(SyncValue):
    """One immutable source snapshot with an adapter-issued source identity."""

    source_id: str
    vendor_code: str
    exported_at: str
    document: SyncDocument

    def __post_init__(self) -> None:
        _validate_ref("source_id", self.source_id)
        _validate_code("vendor_code", self.vendor_code)
        _validate_rfc3339("exported_at", self.exported_at)


@dataclass(frozen=True, slots=True)
class SyncOptions(SyncValue):
    """Explicit operation choices that affect a deterministic plan."""

    operation: SyncOperation
    initialize_manifest: bool = False
    dry_run: bool = False
    accept_manual_deletions: bool = False
    gedcom_version: str = "5.5.5"

    def __post_init__(self) -> None:
        if self.operation is SyncOperation.UPDATE and self.accept_manual_deletions:
            raise ValueError("Manual deletion acceptance is valid only for rebase.")
        if self.operation is SyncOperation.REBASE and self.initialize_manifest:
            raise ValueError("Manifest initialization is valid only for update.")
        if self.gedcom_version not in {"5.5.1", "5.5.5"}:
            raise ValueError("Unsupported GEDCOM version.")


@dataclass(frozen=True, slots=True)
class SyncDecisionSelection(SyncValue):
    """One explicit, replayable selection for a declared decision."""

    decision_id: str
    option_id: str

    def __post_init__(self) -> None:
        _validate_decision_id("decision_id", self.decision_id)
        _validate_code("option_id", self.option_id)


@dataclass(frozen=True, slots=True)
class SyncRequest(SyncValue):
    """Path-free kernel input assembled from already captured documents."""

    operation_id: str
    master: SyncDocument
    manifest: SyncDocument | None
    snapshots: tuple[SyncSnapshot, ...]
    options: SyncOptions
    replayed_decisions: tuple[SyncDecisionSelection, ...] = ()

    def __post_init__(self) -> None:
        _validate_ref("operation_id", self.operation_id)
        if len(self.snapshots) > MAX_ITEMS or len(self.replayed_decisions) > MAX_ITEMS:
            raise ValueError("Sync request exceeds its bounded size.")
        source_ids = tuple(snapshot.source_id for snapshot in self.snapshots)
        if source_ids != tuple(sorted(source_ids)) or len(source_ids) != len(set(source_ids)):
            raise ValueError("snapshots must have unique source IDs in deterministic order.")
        decision_ids = tuple(selection.decision_id for selection in self.replayed_decisions)
        if decision_ids != tuple(sorted(decision_ids)) or len(decision_ids) != len(
            set(decision_ids)
        ):
            raise ValueError("replayed_decisions must have unique IDs in deterministic order.")


@dataclass(frozen=True, slots=True)
class SyncSnapshotState(SyncValue):
    """Captured immutable input state plus an opaque adapter state reference."""

    state_ref: str
    master: SyncDocument
    manifest: SyncDocument | None
    snapshots: tuple[SyncSnapshot, ...]
    input_fingerprint: str

    def __post_init__(self) -> None:
        _validate_ref("state_ref", self.state_ref)
        _validate_sha256("input_fingerprint", self.input_fingerprint)
        if len(self.snapshots) > MAX_ITEMS:
            raise ValueError("Snapshot state exceeds its bounded size.")
        source_ids = tuple(snapshot.source_id for snapshot in self.snapshots)
        if source_ids != tuple(sorted(source_ids)) or len(source_ids) != len(set(source_ids)):
            raise ValueError("snapshots must have unique source IDs in deterministic order.")
        if self.input_fingerprint != self.fingerprint_for(
            master=self.master,
            manifest=self.manifest,
            snapshots=self.snapshots,
        ):
            raise ValueError("input_fingerprint does not match the captured inputs.")

    @staticmethod
    def fingerprint_for(
        *,
        master: SyncDocument,
        manifest: SyncDocument | None,
        snapshots: tuple[SyncSnapshot, ...],
    ) -> str:
        """Return the deterministic fingerprint for captured inputs."""

        return _digest(
            {
                "master": master,
                "manifest": manifest,
                "snapshots": snapshots,
            }
        )

    @classmethod
    def create(
        cls,
        *,
        state_ref: str,
        master: SyncDocument,
        manifest: SyncDocument | None,
        snapshots: tuple[SyncSnapshot, ...],
    ) -> SyncSnapshotState:
        """Create state with a deterministic content fingerprint."""

        fingerprint = cls.fingerprint_for(
            master=master,
            manifest=manifest,
            snapshots=snapshots,
        )
        return cls(state_ref, master, manifest, snapshots, fingerprint)


@dataclass(frozen=True, slots=True)
class SyncDelta(SyncValue):
    """One comparison result without GEDCOM or user payload content."""

    subject_ref: str
    kind: SyncChangeKind
    before_sha256: str | None
    after_sha256: str | None
    provenance_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_ref("subject_ref", self.subject_ref)
        if self.before_sha256 is not None:
            _validate_sha256("before_sha256", self.before_sha256)
        if self.after_sha256 is not None:
            _validate_sha256("after_sha256", self.after_sha256)
        if self.before_sha256 is None and self.after_sha256 is None:
            raise ValueError("A delta must identify at least one side.")
        if len(self.provenance_refs) > MAX_ITEMS:
            raise ValueError("provenance_refs exceeds its bounded size.")
        if self.provenance_refs != tuple(sorted(set(self.provenance_refs))):
            raise ValueError("provenance_refs must be unique and deterministically ordered.")
        for reference in self.provenance_refs:
            _validate_ref("provenance_ref", reference)


@dataclass(frozen=True, slots=True)
class SyncDecisionRequest(SyncValue):
    """One explicit plan decision with stable coded options."""

    decision_id: str
    decision_code: str
    subject_refs: tuple[str, ...]
    option_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_decision_id("decision_id", self.decision_id)
        _validate_code("decision_code", self.decision_code)
        if not self.option_ids:
            raise ValueError("A decision must declare at least one option.")
        if len(self.subject_refs) > MAX_ITEMS:
            raise ValueError("Decision request exceeds its bounded size.")
        if len(self.option_ids) > MAX_DECISION_OPTIONS:
            raise ValueError("Decision request exceeds the supported option limit.")
        if self.subject_refs != tuple(sorted(set(self.subject_refs))):
            raise ValueError("subject_refs must be unique and deterministically ordered.")
        if self.option_ids != tuple(sorted(set(self.option_ids))):
            raise ValueError("option_ids must be unique and deterministically ordered.")
        for reference in self.subject_refs:
            _validate_ref("subject_ref", reference)
        for option_id in self.option_ids:
            _validate_code("option_id", option_id)


@dataclass(frozen=True, slots=True)
class SyncPlanEntry(SyncValue):
    """One deterministically ordered and content-addressed plan action."""

    action_id: str
    sequence: int
    subject_ref: str
    kind: SyncChangeKind
    before_sha256: str | None
    after_sha256: str | None
    provenance_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_ref("action_id", self.action_id)
        _validate_count("sequence", self.sequence)
        _validate_ref("subject_ref", self.subject_ref)
        if self.before_sha256 is not None:
            _validate_sha256("before_sha256", self.before_sha256)
        if self.after_sha256 is not None:
            _validate_sha256("after_sha256", self.after_sha256)
        if self.before_sha256 is None and self.after_sha256 is None:
            raise ValueError("A plan entry must identify at least one side.")
        if self.provenance_refs != tuple(sorted(set(self.provenance_refs))):
            raise ValueError("provenance_refs must be unique and deterministically ordered.")
        if len(self.provenance_refs) > MAX_ITEMS:
            raise ValueError("provenance_refs exceeds its bounded size.")
        for reference in self.provenance_refs:
            _validate_ref("provenance_ref", reference)


@dataclass(frozen=True, slots=True)
class SyncLossEntry(SyncValue):
    """One coded, payload-free loss category identified during planning."""

    loss_code: str
    item_count: int
    subject_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_code("loss_code", self.loss_code)
        _validate_count("item_count", self.item_count)
        if self.item_count == 0:
            raise ValueError("A loss entry must report at least one item.")
        if len(self.subject_refs) > MAX_ITEMS:
            raise ValueError("subject_refs exceeds its bounded size.")
        if self.subject_refs != tuple(sorted(set(self.subject_refs))):
            raise ValueError("subject_refs must be unique and deterministically ordered.")
        for reference in self.subject_refs:
            _validate_ref("subject_ref", reference)


@dataclass(frozen=True, slots=True)
class SyncLossReport(SyncValue):
    """Deterministically ordered structural loss report for a sync plan."""

    entries: tuple[SyncLossEntry, ...] = ()

    def __post_init__(self) -> None:
        if len(self.entries) > MAX_ITEMS:
            raise ValueError("Loss report exceeds its bounded size.")
        expected = tuple(
            sorted(
                set(self.entries),
                key=lambda item: (
                    item.loss_code,
                    item.subject_refs,
                    item.item_count,
                ),
            )
        )
        if self.entries != expected:
            raise ValueError("Loss entries must be unique and deterministically ordered.")

    @classmethod
    def create(cls, entries: tuple[SyncLossEntry, ...]) -> SyncLossReport:
        """Normalize stage output into a deterministic report."""

        return cls(
            tuple(
                sorted(
                    set(entries),
                    key=lambda item: (
                        item.loss_code,
                        item.subject_refs,
                        item.item_count,
                    ),
                )
            )
        )


@dataclass(frozen=True, slots=True)
class SyncPlanningOutput(SyncValue):
    """Raw stage output normalized by the coordinator into a stable plan."""

    deltas: tuple[SyncDelta, ...]
    decisions: tuple[SyncDecisionRequest, ...] = ()
    losses: tuple[SyncLossEntry, ...] = ()

    def __post_init__(self) -> None:
        if (
            len(self.deltas) > MAX_ITEMS
            or len(self.decisions) > MAX_ITEMS
            or len(self.losses) > MAX_ITEMS
        ):
            raise ValueError("Planning output exceeds its bounded size.")


@dataclass(frozen=True, slots=True)
class SyncPlan(SyncValue):
    """Content-addressed deterministic plan safe to persist and replay."""

    plan_id: str
    operation_id: str
    options: SyncOptions
    input_fingerprint: str
    entries: tuple[SyncPlanEntry, ...]
    decisions: tuple[SyncDecisionRequest, ...]
    loss_report: SyncLossReport

    def __post_init__(self) -> None:
        _validate_ref("plan_id", self.plan_id)
        _validate_ref("operation_id", self.operation_id)
        _validate_sha256("input_fingerprint", self.input_fingerprint)
        if len(self.entries) > MAX_ITEMS or len(self.decisions) > MAX_ITEMS:
            raise ValueError("Sync plan exceeds its bounded size.")
        if tuple(entry.sequence for entry in self.entries) != tuple(range(len(self.entries))):
            raise ValueError("Plan entries must have contiguous deterministic sequence values.")
        if self.decisions != tuple(sorted(self.decisions, key=lambda item: item.decision_id)):
            raise ValueError("Plan decisions must be deterministically ordered.")
        decision_ids = tuple(item.decision_id for item in self.decisions)
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError("Plan decision IDs must be unique.")
        if self.plan_id != self.expected_plan_id():
            raise ValueError("plan_id does not match the deterministic plan content.")

    def expected_plan_id(self) -> str:
        """Return the content-derived opaque plan identity."""

        return "plan:" + _digest(
            {
                "operation_id": self.operation_id,
                "options": self.options,
                "input_fingerprint": self.input_fingerprint,
                "entries": self.entries,
                "decisions": self.decisions,
                "loss_report": self.loss_report,
            }
        )

    @classmethod
    def create(
        cls,
        *,
        operation_id: str,
        options: SyncOptions,
        input_fingerprint: str,
        output: SyncPlanningOutput,
    ) -> SyncPlan:
        """Normalize comparison output into a content-addressed plan."""

        ordered_deltas = tuple(
            sorted(
                output.deltas,
                key=lambda item: (
                    item.kind.value,
                    item.subject_ref,
                    item.before_sha256 or "",
                    item.after_sha256 or "",
                    item.provenance_refs,
                ),
            )
        )
        entries = tuple(
            SyncPlanEntry(
                action_id="action:"
                + _digest(
                    {
                        "operation_id": operation_id,
                        "sequence": sequence,
                        "delta": delta,
                    }
                ),
                sequence=sequence,
                subject_ref=delta.subject_ref,
                kind=delta.kind,
                before_sha256=delta.before_sha256,
                after_sha256=delta.after_sha256,
                provenance_refs=delta.provenance_refs,
            )
            for sequence, delta in enumerate(ordered_deltas)
        )
        decisions = tuple(sorted(output.decisions, key=lambda item: item.decision_id))
        loss_report = SyncLossReport.create(output.losses)
        provisional = cls.__new__(cls)
        object.__setattr__(provisional, "plan_id", "plan:pending")
        object.__setattr__(provisional, "operation_id", operation_id)
        object.__setattr__(provisional, "options", options)
        object.__setattr__(provisional, "input_fingerprint", input_fingerprint)
        object.__setattr__(provisional, "entries", entries)
        object.__setattr__(provisional, "decisions", decisions)
        object.__setattr__(provisional, "loss_report", loss_report)
        plan_id = provisional.expected_plan_id()
        return cls(
            plan_id,
            operation_id,
            options,
            input_fingerprint,
            entries,
            decisions,
            loss_report,
        )


@dataclass(frozen=True, slots=True)
class SyncStagedApplication(SyncValue):
    """Opaque staged state that has not crossed the publication boundary."""

    staging_ref: str
    plan_id: str
    artifact_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_ref("staging_ref", self.staging_ref)
        _validate_ref("plan_id", self.plan_id)
        if len(self.artifact_refs) > MAX_ITEMS:
            raise ValueError("artifact_refs exceeds its bounded size.")
        if self.artifact_refs != tuple(sorted(set(self.artifact_refs))):
            raise ValueError("artifact_refs must be unique and deterministically ordered.")
        for reference in self.artifact_refs:
            _validate_ref("artifact_ref", reference)


@dataclass(frozen=True, slots=True)
class SyncPublication(SyncValue):
    """Committed immutable revision and its opaque artifact references."""

    revision_ref: str
    plan_id: str
    artifact_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_ref("revision_ref", self.revision_ref)
        _validate_ref("plan_id", self.plan_id)
        if len(self.artifact_refs) > MAX_ITEMS:
            raise ValueError("artifact_refs exceeds its bounded size.")
        if self.artifact_refs != tuple(sorted(set(self.artifact_refs))):
            raise ValueError("artifact_refs must be unique and deterministically ordered.")
        for reference in self.artifact_refs:
            _validate_ref("artifact_ref", reference)


@dataclass(frozen=True, slots=True)
class SyncRecoveryContext(SyncValue):
    """Bounded recovery input with no document or user payload content."""

    operation_id: str
    failed_stage: SyncStage
    error_code: str
    state_ref: str | None
    plan_id: str | None
    staging_ref: str | None

    def __post_init__(self) -> None:
        _validate_ref("operation_id", self.operation_id)
        _validate_code("error_code", self.error_code)
        for label, value in (
            ("state_ref", self.state_ref),
            ("plan_id", self.plan_id),
            ("staging_ref", self.staging_ref),
        ):
            if value is not None:
                _validate_ref(label, value)


@dataclass(frozen=True, slots=True)
class SyncRecoveryMetadata(SyncValue):
    """Safe recovery evidence suitable for structured logs and DTOs."""

    recovery_ref: str
    prior_revision_preserved: bool
    staged_state_removed: bool
    recovery_code: str

    def __post_init__(self) -> None:
        _validate_ref("recovery_ref", self.recovery_ref)
        _validate_code("recovery_code", self.recovery_code)


@dataclass(frozen=True, slots=True)
class SyncEvent(SyncValue):
    """Bounded structural progress event with coded, non-payload metadata."""

    operation_id: str
    sequence: int
    stage: SyncStage
    phase: SyncEventPhase
    item_count: int
    code: str

    def __post_init__(self) -> None:
        _validate_ref("operation_id", self.operation_id)
        if not 0 <= self.sequence < MAX_EVENTS:
            raise ValueError("Event sequence is outside its bounded range.")
        _validate_count("item_count", self.item_count)
        _validate_code("code", self.code)


@dataclass(frozen=True, slots=True)
class SyncKernelResult(SyncValue):
    """Complete structured kernel outcome."""

    operation_id: str
    outcome: SyncOutcome
    plan: SyncPlan | None
    decisions: tuple[SyncDecisionSelection, ...]
    publication: SyncPublication | None
    recovery: SyncRecoveryMetadata | None
    error_code: str | None
    failed_stage: SyncStage | None
    events: tuple[SyncEvent, ...]

    def __post_init__(self) -> None:
        _validate_ref("operation_id", self.operation_id)
        if self.plan is not None and self.plan.operation_id != self.operation_id:
            raise ValueError("Result plan does not belong to this operation.")
        if len(self.decisions) > MAX_ITEMS:
            raise ValueError("Result decisions exceed their bounded size.")
        if self.decisions != tuple(sorted(self.decisions, key=lambda item: item.decision_id)):
            raise ValueError("Result decisions must be deterministically ordered.")
        decision_ids = tuple(item.decision_id for item in self.decisions)
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError("Result decision IDs must be unique.")
        if self.plan is not None:
            declared = {item.decision_id: item.option_ids for item in self.plan.decisions}
            for selection in self.decisions:
                if selection.decision_id not in declared:
                    raise ValueError("Result contains an undeclared decision.")
                if selection.option_id not in declared[selection.decision_id]:
                    raise ValueError("Result contains an invalid decision option.")
        if len(self.events) > MAX_EVENTS:
            raise ValueError("Result events exceed their bounded size.")
        if tuple(event.sequence for event in self.events) != tuple(range(len(self.events))):
            raise ValueError("Result events must have contiguous sequence values.")
        if any(event.operation_id != self.operation_id for event in self.events):
            raise ValueError("Result event does not belong to this operation.")
        if self.error_code is not None:
            _validate_code("error_code", self.error_code)

        terminal_failure = self.outcome in {SyncOutcome.CANCELLED, SyncOutcome.FAILED}
        if self.outcome is SyncOutcome.COMMITTED:
            if self.plan is None or self.publication is None:
                raise ValueError("Committed results require a plan and publication.")
            if self.publication.plan_id != self.plan.plan_id:
                raise ValueError("Committed publication does not match the result plan.")
            if (
                self.recovery is not None
                or self.error_code is not None
                or self.failed_stage is not None
            ):
                raise ValueError("Committed results cannot contain failure metadata.")
        elif self.publication is not None:
            raise ValueError("Only committed results may contain a publication.")
        if terminal_failure:
            if self.error_code is None or self.failed_stage is None:
                raise ValueError("Failed results require coded stage metadata.")
        elif (
            self.recovery is not None
            or self.error_code is not None
            or self.failed_stage is not None
        ):
            raise ValueError("Successful non-publication results cannot contain failure metadata.")
        if self.outcome in {SyncOutcome.DRY_RUN, SyncOutcome.NO_CHANGE}:
            if self.plan is None or self.decisions:
                raise ValueError("Non-publication success requires an undecided plan.")

    @property
    def committed(self) -> bool:
        """Whether the publication stage completed atomically."""

        return self.outcome is SyncOutcome.COMMITTED


class SyncStageError(RuntimeError):
    """Coded implementation failure at a declared stage."""

    def __init__(self, code: str, message: str = "") -> None:
        _validate_code("code", code)
        super().__init__(message or code)
        self.code = code


class SyncCancelled(SyncStageError):
    """Explicit cooperative cancellation before publication."""

    def __init__(self) -> None:
        super().__init__("SYNC_CANCELLED")


@runtime_checkable
class SnapshotStage(Protocol):
    """Capture and verify immutable inputs."""

    def capture(self, request: SyncRequest) -> SyncSnapshotState: ...


@runtime_checkable
class ComparisonStage(Protocol):
    """Compare typed immutable snapshot state."""

    def compare(
        self,
        snapshot: SyncSnapshotState,
        options: SyncOptions,
    ) -> tuple[SyncDelta, ...]: ...


@runtime_checkable
class PlanningStage(Protocol):
    """Convert deltas into actions and explicit decisions."""

    def plan(
        self,
        snapshot: SyncSnapshotState,
        deltas: tuple[SyncDelta, ...],
        options: SyncOptions,
    ) -> SyncPlanningOutput: ...


@runtime_checkable
class DecisionStage(Protocol):
    """Resolve a declared decision through an outer application port."""

    def decide(self, request: SyncDecisionRequest) -> SyncDecisionSelection: ...


@runtime_checkable
class ApplicationStage(Protocol):
    """Apply a decided plan into unpublished staged state."""

    def stage(
        self,
        snapshot: SyncSnapshotState,
        plan: SyncPlan,
        decisions: tuple[SyncDecisionSelection, ...],
    ) -> SyncStagedApplication: ...


@runtime_checkable
class CommitStage(Protocol):
    """Validate a publication receipt, then atomically publish staged state.

    ``prepare`` must not publish. ``commit`` receives a kernel-validated receipt
    and must not raise after the publication boundary has been crossed.
    """

    def prepare(self, staged: SyncStagedApplication) -> SyncPublication: ...

    def commit(
        self,
        staged: SyncStagedApplication,
        publication: SyncPublication,
    ) -> None: ...


@runtime_checkable
class RecoveryStage(Protocol):
    """Recover unpublished state while preserving the prior revision."""

    def recover(self, context: SyncRecoveryContext) -> SyncRecoveryMetadata: ...


@runtime_checkable
class CancellationStage(Protocol):
    """Check cooperative cancellation at interruptible boundaries."""

    def check_cancelled(self) -> None: ...


@runtime_checkable
class EventStage(Protocol):
    """Receive one bounded structural event."""

    def emit(self, event: SyncEvent) -> None: ...


class NeverCancelled:
    """Default cancellation implementation."""

    __slots__ = ()

    def check_cancelled(self) -> None:
        return


class DiscardEvents:
    """Default event implementation."""

    __slots__ = ()

    def emit(self, event: SyncEvent) -> None:
        del event


class SyncKernel:
    """Coordinate pure stage contracts without owning adapter state."""

    def __init__(
        self,
        *,
        snapshot: SnapshotStage,
        comparison: ComparisonStage,
        planning: PlanningStage,
        decisions: DecisionStage,
        application: ApplicationStage,
        commit: CommitStage,
        recovery: RecoveryStage,
        cancellation: CancellationStage | None = None,
        events: EventStage | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._comparison = comparison
        self._planning = planning
        self._decisions = decisions
        self._application = application
        self._commit = commit
        self._recovery = recovery
        self._cancellation = cancellation if cancellation is not None else NeverCancelled()
        self._events = events if events is not None else DiscardEvents()

    def execute(self, request: SyncRequest) -> SyncKernelResult:
        """Execute stages and return a complete success, cancellation, or failure."""

        recorded: list[SyncEvent] = []
        snapshot_state: SyncSnapshotState | None = None
        plan: SyncPlan | None = None
        staged: SyncStagedApplication | None = None
        selected: tuple[SyncDecisionSelection, ...] = ()
        current_stage = SyncStage.SNAPSHOT

        def emit(
            stage: SyncStage,
            phase: SyncEventPhase,
            code: str,
            *,
            item_count: int = 0,
            tolerate_sink_failure: bool = False,
        ) -> None:
            if len(recorded) >= MAX_EVENTS:
                raise SyncStageError("SYNC_EVENT_LIMIT")
            event = SyncEvent(
                operation_id=request.operation_id,
                sequence=len(recorded),
                stage=stage,
                phase=phase,
                item_count=item_count,
                code=code,
            )
            recorded.append(event)
            try:
                self._events.emit(event)
            except Exception as exc:
                if tolerate_sink_failure:
                    return
                if isinstance(exc, SyncStageError):
                    raise
                raise SyncStageError("SYNC_EVENT_FAILED") from exc

        def checkpoint() -> None:
            self._cancellation.check_cancelled()

        try:
            emit(current_stage, SyncEventPhase.STARTED, "SYNC_SNAPSHOT_STARTED")
            checkpoint()
            snapshot_state = self._snapshot.capture(request)
            if (
                snapshot_state.master != request.master
                or snapshot_state.manifest != request.manifest
                or snapshot_state.snapshots != request.snapshots
            ):
                raise SyncStageError("SYNC_SNAPSHOT_INPUT_MISMATCH")
            if snapshot_state.input_fingerprint != SyncSnapshotState.fingerprint_for(
                master=snapshot_state.master,
                manifest=snapshot_state.manifest,
                snapshots=snapshot_state.snapshots,
            ):
                raise SyncStageError("SYNC_SNAPSHOT_FINGERPRINT_MISMATCH")
            checkpoint()
            emit(current_stage, SyncEventPhase.COMPLETED, "SYNC_SNAPSHOT_COMPLETED")

            current_stage = SyncStage.COMPARISON
            emit(current_stage, SyncEventPhase.STARTED, "SYNC_COMPARISON_STARTED")
            checkpoint()
            deltas = self._comparison.compare(snapshot_state, request.options)
            checkpoint()
            emit(
                current_stage,
                SyncEventPhase.COMPLETED,
                "SYNC_COMPARISON_COMPLETED",
                item_count=len(deltas),
            )

            current_stage = SyncStage.PLANNING
            emit(current_stage, SyncEventPhase.STARTED, "SYNC_PLANNING_STARTED")
            checkpoint()
            output = self._planning.plan(snapshot_state, deltas, request.options)
            if tuple(output.deltas) != tuple(deltas):
                raise SyncStageError("SYNC_PLAN_DELTA_MISMATCH")
            plan = SyncPlan.create(
                operation_id=request.operation_id,
                options=request.options,
                input_fingerprint=snapshot_state.input_fingerprint,
                output=output,
            )
            checkpoint()
            emit(
                current_stage,
                SyncEventPhase.COMPLETED,
                "SYNC_PLANNING_COMPLETED",
                item_count=len(plan.entries),
            )

            if request.options.dry_run:
                return SyncKernelResult(
                    request.operation_id,
                    SyncOutcome.DRY_RUN,
                    plan,
                    (),
                    None,
                    None,
                    None,
                    None,
                    tuple(recorded),
                )
            if not plan.entries and not plan.decisions:
                return SyncKernelResult(
                    request.operation_id,
                    SyncOutcome.NO_CHANGE,
                    plan,
                    (),
                    None,
                    None,
                    None,
                    None,
                    tuple(recorded),
                )

            current_stage = SyncStage.DECISION
            emit(current_stage, SyncEventPhase.STARTED, "SYNC_DECISION_STARTED")
            selected = self._resolve_decisions(request, plan, checkpoint)
            emit(
                current_stage,
                SyncEventPhase.COMPLETED,
                "SYNC_DECISION_COMPLETED",
                item_count=len(selected),
            )

            current_stage = SyncStage.APPLICATION
            emit(current_stage, SyncEventPhase.STARTED, "SYNC_APPLICATION_STARTED")
            checkpoint()
            staged = self._application.stage(snapshot_state, plan, selected)
            if staged.plan_id != plan.plan_id:
                raise SyncStageError("SYNC_STAGED_PLAN_MISMATCH")
            checkpoint()
            emit(
                current_stage,
                SyncEventPhase.COMPLETED,
                "SYNC_APPLICATION_COMPLETED",
                item_count=len(staged.artifact_refs),
            )

            current_stage = SyncStage.COMMIT
            emit(current_stage, SyncEventPhase.STARTED, "SYNC_COMMIT_STARTED")
            checkpoint()
            publication = self._commit.prepare(staged)
            if publication.plan_id != plan.plan_id:
                raise SyncStageError("SYNC_PUBLICATION_PLAN_MISMATCH")
            if publication.artifact_refs != staged.artifact_refs:
                raise SyncStageError("SYNC_PUBLICATION_ARTIFACT_MISMATCH")
            checkpoint()
            self._commit.commit(staged, publication)
            emit(
                current_stage,
                SyncEventPhase.COMPLETED,
                "SYNC_COMMIT_COMPLETED",
                item_count=len(publication.artifact_refs),
                tolerate_sink_failure=True,
            )
            return SyncKernelResult(
                request.operation_id,
                SyncOutcome.COMMITTED,
                plan,
                selected,
                publication,
                None,
                None,
                None,
                tuple(recorded),
            )
        except Exception as exc:  # noqa: BLE001 - stage adapters are an explicit boundary
            cancelled = isinstance(exc, SyncCancelled)
            code = exc.code if isinstance(exc, SyncStageError) else "SYNC_STAGE_FAILED"
            try:
                emit(current_stage, SyncEventPhase.FAILED, code)
            except Exception as ignored:  # noqa: BLE001
                # The original stage failure remains authoritative.
                del ignored
            recovery = self._recover(
                request=request,
                stage=current_stage,
                error_code=code,
                snapshot=snapshot_state,
                plan=plan,
                staged=staged,
                recorded=recorded,
            )
            if recovery is None:
                code = "SYNC_RECOVERY_FAILED"
            elif not recovery.prior_revision_preserved or (
                staged is not None and not recovery.staged_state_removed
            ):
                code = "SYNC_RECOVERY_INCOMPLETE"
            return SyncKernelResult(
                request.operation_id,
                SyncOutcome.CANCELLED if cancelled else SyncOutcome.FAILED,
                plan,
                selected,
                None,
                recovery,
                code,
                current_stage,
                tuple(recorded),
            )

    def _resolve_decisions(
        self,
        request: SyncRequest,
        plan: SyncPlan,
        checkpoint: Callable[[], None],
    ) -> tuple[SyncDecisionSelection, ...]:
        declared = {item.decision_id: item for item in plan.decisions}
        replayed = {item.decision_id: item for item in request.replayed_decisions}
        unknown = set(replayed) - set(declared)
        if unknown:
            raise SyncStageError("SYNC_DECISION_UNKNOWN")
        selections: list[SyncDecisionSelection] = []
        for decision in plan.decisions:
            checkpoint()
            selection = replayed.get(decision.decision_id)
            if selection is None:
                selection = self._decisions.decide(decision)
            if selection.decision_id != decision.decision_id:
                raise SyncStageError("SYNC_DECISION_ID_MISMATCH")
            if selection.option_id not in decision.option_ids:
                raise SyncStageError("SYNC_DECISION_OPTION_INVALID")
            selections.append(selection)
            checkpoint()
        return tuple(selections)

    def _recover(
        self,
        *,
        request: SyncRequest,
        stage: SyncStage,
        error_code: str,
        snapshot: SyncSnapshotState | None,
        plan: SyncPlan | None,
        staged: SyncStagedApplication | None,
        recorded: list[SyncEvent],
    ) -> SyncRecoveryMetadata | None:
        if len(recorded) < MAX_EVENTS:
            event = SyncEvent(
                request.operation_id,
                len(recorded),
                SyncStage.RECOVERY,
                SyncEventPhase.STARTED,
                0,
                "SYNC_RECOVERY_STARTED",
            )
            recorded.append(event)
            try:
                self._events.emit(event)
            except Exception as ignored:  # noqa: BLE001
                # Recovery must survive a reporting adapter failure.
                del ignored
        try:
            recovery = self._recovery.recover(
                SyncRecoveryContext(
                    operation_id=request.operation_id,
                    failed_stage=stage,
                    error_code=error_code,
                    state_ref=snapshot.state_ref if snapshot is not None else None,
                    plan_id=plan.plan_id if plan is not None else None,
                    staging_ref=staged.staging_ref if staged is not None else None,
                )
            )
        except Exception:  # noqa: BLE001 - recovery failure is represented structurally
            return None
        if len(recorded) < MAX_EVENTS:
            event = SyncEvent(
                request.operation_id,
                len(recorded),
                SyncStage.RECOVERY,
                SyncEventPhase.COMPLETED,
                0,
                recovery.recovery_code,
            )
            recorded.append(event)
            try:
                self._events.emit(event)
            except Exception as ignored:  # noqa: BLE001
                # Recovery already completed safely.
                del ignored
        return recovery


__all__ = [
    "MAX_COUNT",
    "MAX_DECISION_OPTIONS",
    "MAX_EVENTS",
    "MAX_ITEMS",
    "MAX_SYNC_JSON_BYTES",
    "ApplicationStage",
    "CancellationStage",
    "CommitStage",
    "ComparisonStage",
    "DecisionStage",
    "DiscardEvents",
    "EventStage",
    "NeverCancelled",
    "PlanningStage",
    "RecoveryStage",
    "SnapshotStage",
    "SyncCancelled",
    "SyncChangeKind",
    "SyncDecisionRequest",
    "SyncDecisionSelection",
    "SyncDelta",
    "SyncDocument",
    "SyncEvent",
    "SyncEventPhase",
    "SyncKernel",
    "SyncKernelResult",
    "SyncLossEntry",
    "SyncLossReport",
    "SyncOperation",
    "SyncOptions",
    "SyncOutcome",
    "SyncPlan",
    "SyncPlanEntry",
    "SyncPlanningOutput",
    "SyncPublication",
    "SyncRecoveryContext",
    "SyncRecoveryMetadata",
    "SyncRequest",
    "SyncSnapshot",
    "SyncSnapshotState",
    "SyncStage",
    "SyncStageError",
    "SyncStagedApplication",
]
