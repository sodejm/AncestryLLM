"""Add restart-safe background job persistence.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(length=13), nullable=False),
        sa.Column("job_number", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("submitted_at", sa.String(length=64), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("job_id"),
        sa.UniqueConstraint("job_number"),
    )
    op.create_index("ix_jobs_number", "jobs", ["job_number"])
    op.create_index("ix_jobs_state", "jobs", ["state"])
    op.create_table(
        "job_events",
        sa.Column("job_id", sa.String(length=13), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("event_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id", "sequence"),
    )
    op.create_index("ix_job_events_replay", "job_events", ["job_id", "sequence"])


def downgrade() -> None:
    op.drop_index("ix_job_events_replay", table_name="job_events")
    op.drop_table("job_events")
    op.drop_index("ix_jobs_state", table_name="jobs")
    op.drop_index("ix_jobs_number", table_name="jobs")
    op.drop_table("jobs")
