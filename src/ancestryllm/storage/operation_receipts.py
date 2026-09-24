"""Encrypted persistence for privacy-minimal operation receipts."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

from sqlalchemy import delete, select, update

from ancestryllm.application.operation_receipts import OperationReceipt, OperationReceiptOutcome
from ancestryllm.core.errors import StorageError
from ancestryllm.storage.database import Database
from ancestryllm.storage.models import OperationReceiptModel

_MAX_LIST_LIMIT = 200
_DEFAULT_LIST_LIMIT = 50


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _iso(value: dt.datetime) -> str:
    return value.isoformat()


class OperationReceiptRepository:
    """Store and query durable operation receipts with bounded retention."""

    def __init__(self, database: Database, *, retention_days: int = 30) -> None:
        if type(retention_days) is not int or retention_days < 1 or retention_days > 3660:
            raise ValueError("retention_days must be a bounded positive integer.")
        self.database = database
        self.retention_days = retention_days

    def _retention_expiry(self, now: dt.datetime) -> str:
        return _iso(now + dt.timedelta(days=self.retention_days))

    def _prune_expired(self) -> None:
        now = _iso(_utc_now())
        with self.database.session() as session:
            session.execute(
                delete(OperationReceiptModel).where(
                    OperationReceiptModel.expires_at.is_not(None),
                    OperationReceiptModel.expires_at < now,
                )
            )
            session.commit()

    def create_pending(self, receipt: OperationReceipt) -> None:
        if receipt.outcome is not OperationReceiptOutcome.PENDING or receipt.completed_at is not None:
            raise ValueError("pending receipt creation requires pending outcome and no completion.")
        now = _utc_now()
        payload = receipt.to_json()
        try:
            with self.database.session() as session:
                session.add(
                    OperationReceiptModel(
                        receipt_id=receipt.receipt_id,
                        operation_id=receipt.logical_operation_id,
                        operation_type=receipt.operation_type,
                        outcome=receipt.outcome.value,
                        started_at=receipt.started_at,
                        completed_at=None,
                        duration_ms=None,
                        payload_json=payload,
                        expires_at=self._retention_expiry(now),
                    )
                )
                session.commit()
        except Exception as exc:  # noqa: BLE001
            raise StorageError(
                "OPERATION_RECEIPT_INIT_FAILED",
                "The operation receipt could not be initialized durably.",
                "Retry after local encrypted storage becomes available.",
                details={"error_type": type(exc).__name__},
            ) from exc

    def finalize(self, receipt: OperationReceipt) -> OperationReceipt:
        if receipt.outcome is OperationReceiptOutcome.PENDING or receipt.completed_at is None:
            raise ValueError("terminal receipt finalization requires a terminal outcome.")
        payload = receipt.to_json()
        with self.database.session() as session:
            updated = session.execute(
                update(OperationReceiptModel)
                .where(
                    OperationReceiptModel.receipt_id == receipt.receipt_id,
                    OperationReceiptModel.completed_at.is_(None),
                    OperationReceiptModel.outcome == OperationReceiptOutcome.PENDING.value,
                )
                .values(
                    outcome=receipt.outcome.value,
                    completed_at=receipt.completed_at,
                    duration_ms=receipt.duration_ms,
                    payload_json=payload,
                )
            )
            if updated.rowcount == 1:
                session.commit()
                return receipt
            row = session.get(OperationReceiptModel, receipt.receipt_id)
            if row is None:
                session.rollback()
                raise StorageError(
                    "OPERATION_RECEIPT_MISSING",
                    "The operation receipt does not exist.",
                )
            committed = OperationReceipt.from_json(row.payload_json)
            session.rollback()
            if committed.outcome is OperationReceiptOutcome.PENDING:
                raise StorageError(
                    "OPERATION_RECEIPT_TERMINALIZE_FAILED",
                    "The operation receipt could not be terminalized.",
                )
            return committed

    def get(self, receipt_id: str) -> OperationReceipt:
        self._prune_expired()
        with self.database.session() as session:
            row = session.get(OperationReceiptModel, receipt_id)
            if row is None:
                raise StorageError("OPERATION_RECEIPT_NOT_FOUND", "The operation receipt was not found.")
            return OperationReceipt.from_json(row.payload_json)

    def list_recent(self, *, limit: int = _DEFAULT_LIST_LIMIT) -> tuple[OperationReceipt, ...]:
        self._prune_expired()
        if type(limit) is not int or not 1 <= limit <= _MAX_LIST_LIMIT:
            raise ValueError("receipt list limit is out of range.")
        with self.database.session() as session:
            rows = session.scalars(
                select(OperationReceiptModel)
                .order_by(OperationReceiptModel.started_at.desc(), OperationReceiptModel.receipt_id.desc())
                .limit(limit)
            )
            return tuple(OperationReceipt.from_json(row.payload_json) for row in rows)

    def export_redacted(self, receipt_id: str) -> dict[str, object]:
        receipt = self.get(receipt_id)
        redacted = replace(
            receipt,
            source_fingerprint=None,
            target_fingerprint=None,
            warnings=tuple(sorted(set(receipt.warnings))),
        )
        return {
            "schema_version": redacted.schema_version,
            "receipt_id": redacted.receipt_id,
            "logical_operation_id": redacted.logical_operation_id,
            "operation_type": redacted.operation_type,
            "outcome": redacted.outcome.value,
            "started_at": redacted.started_at,
            "completed_at": redacted.completed_at,
            "duration_ms": redacted.duration_ms,
            "adapter_class": redacted.adapter_class,
            "provider_class": redacted.provider_class,
            "authorization_ref": redacted.authorization_ref,
            "policy_revision_ref": redacted.policy_revision_ref,
            "idempotency_digest": redacted.idempotency_digest,
            "source_fingerprint": redacted.source_fingerprint,
            "target_fingerprint": redacted.target_fingerprint,
            "source_count": redacted.source_count,
            "target_count": redacted.target_count,
            "artifacts": [artifact.to_serializable() for artifact in redacted.artifacts],
            "estimated_input_tokens": redacted.estimated_input_tokens,
            "estimated_output_tokens": redacted.estimated_output_tokens,
            "estimated_cost_usd": redacted.estimated_cost_usd,
            "provider_input_tokens": redacted.provider_input_tokens,
            "provider_output_tokens": redacted.provider_output_tokens,
            "provider_cost_usd": redacted.provider_cost_usd,
            "error_code": redacted.error_code,
            "warnings": list(redacted.warnings),
            "redactions": {
                "source_fingerprint": "removed",
                "target_fingerprint": "removed",
            },
        }


__all__ = ["OperationReceiptRepository"]
