"""Initial encrypted workspace schema.

Revision ID: 0001
"""

from ancestryllm.storage.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    from alembic import op

    Base.metadata.drop_all(op.get_bind())
