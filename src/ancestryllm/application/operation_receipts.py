"""Transport-neutral privacy-minimal operation receipt DTOs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from ancestryllm.application.dto import ArtifactRef, BoundaryDTO

SCHEMA_VERSION = "ancestryllm.operation-receipt/1"
_MAX_WARNINGS = 16


def _code(label: str, value: str, *, maximum: int = 96) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ValueError(f"{label} must be a non-empty bounded string.")
    if any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
        for character in value
    ):
        raise ValueError(f"{label} contains unsupported characters.")


def _opaque(label: str, value: str, *, minimum: int = 16, maximum: int = 160) -> None:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise ValueError(f"{label} must be a bounded opaque string.")
    if any(marker in value for marker in ("/", "\\", "..", "\n", "\r", "\x00")):
        raise ValueError(f"{label} must not contain path or control markers.")


def _timestamp(label: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)", value)
        is None
    ):
        raise ValueError(f"{label} must be a UTC timestamp.")


def _digest_or_none(label: str, value: str | None) -> None:
    if value is None:
        return
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{label} must be a lowercase sha256 digest.")


class OperationReceiptOutcome(StrEnum):
    """Stable lifecycle outcomes for auditable operations."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    DENIED = "denied"
    RECOVERED = "recovered"


@dataclass(frozen=True, slots=True)
class OperationReceipt(BoundaryDTO):
    """Versioned durable receipt for one logical operation."""

    schema_version: str
    receipt_id: str
    logical_operation_id: str
    operation_type: str
    outcome: OperationReceiptOutcome
    started_at: str
    completed_at: str | None
    duration_ms: int | None
    adapter_class: str
    provider_class: str | None
    authorization_ref: str | None
    policy_revision_ref: str | None
    idempotency_digest: str | None
    source_fingerprint: str | None
    target_fingerprint: str | None
    source_count: int | None
    target_count: int | None
    artifacts: tuple[ArtifactRef, ...]
    estimated_input_tokens: int | None
    estimated_output_tokens: int | None
    estimated_cost_usd: float | None
    provider_input_tokens: int | None
    provider_output_tokens: int | None
    provider_cost_usd: float | None
    error_code: str | None
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("receipt schema_version is unsupported.")
        _opaque("receipt_id", self.receipt_id)
        _opaque("logical_operation_id", self.logical_operation_id)
        _code("operation_type", self.operation_type)
        if not isinstance(self.outcome, OperationReceiptOutcome):
            raise ValueError("receipt outcome is unsupported.")
        _timestamp("started_at", self.started_at)
        if self.completed_at is not None:
            _timestamp("completed_at", self.completed_at)
        if self.duration_ms is not None and (
            type(self.duration_ms) is not int or self.duration_ms < 0
        ):
            raise ValueError("duration_ms must be a non-negative integer.")
        _code("adapter_class", self.adapter_class)
        if self.provider_class is not None:
            _code("provider_class", self.provider_class)
        for label, reference in (
            ("authorization_ref", self.authorization_ref),
            ("policy_revision_ref", self.policy_revision_ref),
            ("source_fingerprint", self.source_fingerprint),
            ("target_fingerprint", self.target_fingerprint),
        ):
            if reference is not None:
                _opaque(label, reference)
        _digest_or_none("idempotency_digest", self.idempotency_digest)
        for label, count in (
            ("source_count", self.source_count),
            ("target_count", self.target_count),
        ):
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError(f"{label} must be a non-negative integer.")
        if (
            not isinstance(self.artifacts, tuple)
            or len(self.artifacts) > 32
            or any(not isinstance(item, ArtifactRef) for item in self.artifacts)
        ):
            raise ValueError("artifacts must be a bounded tuple of artifact references.")
        for label, token_count in (
            ("estimated_input_tokens", self.estimated_input_tokens),
            ("estimated_output_tokens", self.estimated_output_tokens),
            ("provider_input_tokens", self.provider_input_tokens),
            ("provider_output_tokens", self.provider_output_tokens),
        ):
            if token_count is not None and (type(token_count) is not int or token_count < 0):
                raise ValueError(f"{label} must be a non-negative integer.")
        for label, cost in (
            ("estimated_cost_usd", self.estimated_cost_usd),
            ("provider_cost_usd", self.provider_cost_usd),
        ):
            if cost is not None and (not isinstance(cost, (int, float)) or cost < 0):
                raise ValueError(f"{label} must be a non-negative number.")
        if self.error_code is not None:
            _code("error_code", self.error_code)
        if (
            not isinstance(self.warnings, tuple)
            or len(self.warnings) > _MAX_WARNINGS
            or any(not isinstance(item, str) for item in self.warnings)
        ):
            raise ValueError("warnings must be a bounded tuple of warning codes.")
        for warning in self.warnings:
            _code("warning", warning)


__all__ = ["SCHEMA_VERSION", "OperationReceipt", "OperationReceiptOutcome"]
