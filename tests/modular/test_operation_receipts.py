"""Verify privacy-minimal operation receipt persistence contracts."""

from __future__ import annotations

from dataclasses import replace

import pytest

from ancestryllm.application.dto import ArtifactRef, ArtifactStatus
from ancestryllm.application.operation_receipts import (
    SCHEMA_VERSION,
    OperationReceipt,
    OperationReceiptOutcome,
)
from ancestryllm.core.errors import StorageError
from ancestryllm.storage.models import OperationReceiptModel
from ancestryllm.storage.operation_receipts import OperationReceiptRepository


def _receipt(*, receipt_id: str = "receipt_" + "a" * 64) -> OperationReceipt:
    return OperationReceipt(
        schema_version=SCHEMA_VERSION,
        receipt_id=receipt_id,
        logical_operation_id="operation_" + "b" * 64,
        operation_type="remote_llm_request",
        outcome=OperationReceiptOutcome.PENDING,
        started_at="2026-09-24T01:00:00+00:00",
        completed_at=None,
        duration_ms=None,
        adapter_class="llm.service",
        provider_class="openai",
        authorization_ref="consent-policy-ref-v1",
        policy_revision_ref="consent-policy-v1",
        idempotency_digest="c" * 64,
        source_fingerprint="source_" + "d" * 32,
        target_fingerprint="target_" + "e" * 32,
        source_count=1,
        target_count=1,
        artifacts=(
            ArtifactRef(
                artifact_id="art_" + "f" * 64,
                media_type="application/json",
                artifact_type="result",
                size_bytes=128,
                status=ArtifactStatus.READY,
                sha256="1" * 64,
            ),
        ),
        estimated_input_tokens=11,
        estimated_output_tokens=7,
        estimated_cost_usd=0.12,
        provider_input_tokens=None,
        provider_output_tokens=None,
        provider_cost_usd=None,
        error_code=None,
        warnings=(),
    )


def test_terminal_receipt_is_immutable_once_written(app_context) -> None:  # type: ignore[no-untyped-def]
    repository = OperationReceiptRepository(app_context.database)
    pending = _receipt()
    repository.create_pending(pending)

    succeeded = replace(
        pending,
        outcome=OperationReceiptOutcome.SUCCEEDED,
        completed_at="2026-09-24T01:00:02+00:00",
        duration_ms=2000,
        provider_input_tokens=11,
        provider_output_tokens=7,
        provider_cost_usd=0.12,
    )
    committed = repository.finalize(succeeded)
    assert committed.outcome is OperationReceiptOutcome.SUCCEEDED

    conflicting = replace(
        pending,
        outcome=OperationReceiptOutcome.FAILED,
        completed_at="2026-09-24T01:00:03+00:00",
        duration_ms=3000,
        error_code="PROVIDER_TIMEOUT",
    )
    retained = repository.finalize(conflicting)
    assert retained.outcome is OperationReceiptOutcome.SUCCEEDED
    assert retained.error_code is None


@pytest.mark.parametrize("field", ["estimated_cost_usd", "provider_cost_usd"])
@pytest.mark.parametrize("cost", [float("nan"), float("inf"), float("-inf"), True, False])
def test_receipt_costs_must_be_finite_non_boolean_numbers(field: str, cost: float) -> None:
    with pytest.raises(ValueError, match="non-negative finite number"):
        replace(_receipt(), **{field: cost})


def test_receipt_export_is_redacted_and_listing_is_bounded(app_context) -> None:  # type: ignore[no-untyped-def]
    repository = OperationReceiptRepository(app_context.database)
    first = _receipt(receipt_id="receipt_" + "a" * 63 + "1")
    second = replace(
        _receipt(receipt_id="receipt_" + "a" * 63 + "2"),
        logical_operation_id="operation_" + "9" * 64,
        started_at="2026-09-24T01:00:01+00:00",
    )
    for pending in (first, second):
        repository.create_pending(pending)
        repository.finalize(
            replace(
                pending,
                outcome=OperationReceiptOutcome.SUCCEEDED,
                completed_at="2026-09-24T01:00:03+00:00",
                duration_ms=2000,
            )
        )

    listed = repository.list_recent(limit=1)
    assert len(listed) == 1
    assert listed[0].receipt_id == second.receipt_id

    exported = repository.export_redacted(first.receipt_id)
    assert exported["source_count"] == 1
    assert exported["source_fingerprint"] is None
    assert exported["target_fingerprint"] is None
    assert "logical_operation_id" not in exported
    assert "idempotency_digest" not in exported
    assert exported["redactions"] == {
        "idempotency_digest": "removed",
        "logical_operation_id": "removed",
        "source_fingerprint": "removed",
        "target_fingerprint": "removed",
    }


def test_create_pending_prunes_expired_receipts_in_same_write(app_context) -> None:  # type: ignore[no-untyped-def]
    repository = OperationReceiptRepository(app_context.database)
    expired = replace(
        _receipt(receipt_id="receipt_" + "8" * 64),
        logical_operation_id="operation_" + "8" * 64,
    )
    with app_context.database.session() as session:
        session.add(
            OperationReceiptModel(
                receipt_id=expired.receipt_id,
                operation_id=expired.logical_operation_id,
                operation_type=expired.operation_type,
                outcome=expired.outcome.value,
                started_at=expired.started_at,
                completed_at=None,
                duration_ms=None,
                payload_json=expired.to_json(),
                expires_at="2020-01-01T00:00:00+00:00",
            )
        )
        session.commit()

    current = _receipt(receipt_id="receipt_" + "9" * 64)
    repository.create_pending(current)

    with app_context.database.session() as session:
        assert session.get(OperationReceiptModel, expired.receipt_id) is None
        assert session.get(OperationReceiptModel, current.receipt_id) is not None


def test_missing_receipt_error(app_context) -> None:  # type: ignore[no-untyped-def]
    repository = OperationReceiptRepository(app_context.database)
    with pytest.raises(StorageError) as raised:
        repository.get("receipt_missing")
    assert raised.value.code == "OPERATION_RECEIPT_NOT_FOUND"
