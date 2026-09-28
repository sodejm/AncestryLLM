"""Add durable privacy-minimal operation receipts.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _begin_native_sqlite_transaction() -> None:
    """Ensure SQLite DDL participates in one native transaction."""
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        return
    driver_connection = connection.connection.driver_connection
    if driver_connection is None:
        raise RuntimeError("SQLite driver connection is unavailable")
    if not driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")


def upgrade() -> None:
    """Apply this schema migration in the forward direction."""
    _begin_native_sqlite_transaction()
    op.create_table(
        "operation_receipts",
        sa.Column("receipt_id", sa.String(length=160), nullable=False),
        sa.Column("operation_id", sa.String(length=160), nullable=False),
        sa.Column("operation_type", sa.String(length=96), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.String(length=40), nullable=False),
        sa.Column("completed_at", sa.String(length=40), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.String(length=40), nullable=True),
        sa.PrimaryKeyConstraint("receipt_id"),
        sa.UniqueConstraint("operation_id"),
    )
    op.create_index(
        "ix_operation_receipts_started",
        "operation_receipts",
        ["started_at"],
    )
    op.create_index(
        "ix_operation_receipts_expires",
        "operation_receipts",
        ["expires_at"],
    )


def downgrade() -> None:
    """Revert this schema migration to its previous revision."""
    _begin_native_sqlite_transaction()
    op.drop_index("ix_operation_receipts_expires", table_name="operation_receipts")
    op.drop_index("ix_operation_receipts_started", table_name="operation_receipts")
    op.drop_table("operation_receipts")
