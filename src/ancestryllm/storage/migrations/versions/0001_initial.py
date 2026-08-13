"""Initial encrypted workspace schema.

Revision ID: 0001

This revision deliberately declares its historical schema instead of importing
the live ORM metadata. Later model additions must be introduced by a new
revision so an upgrade to ``0001`` remains stable.
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "people",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("display_name", sa.String(length=500), nullable=False),
        sa.Column("living_status", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_people_workspace_name", "people", ["workspace_id", "display_name"])
    op.create_table(
        "person_identifiers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("person_id", sa.String(length=36), nullable=False),
        sa.Column("system", sa.String(length=100), nullable=False),
        sa.Column("value", sa.String(length=500), nullable=False),
        sa.Column("tree_fingerprint", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "system",
            "value",
            "tree_fingerprint",
            name="uq_person_source_identifier",
        ),
    )
    op.create_table(
        "facts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("person_id", sa.String(length=36), nullable=False),
        sa.Column("fact_type", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("date_text", sa.String(length=200), nullable=False),
        sa.Column("place", sa.String(length=500), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("provenance_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "relationships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_person_id", sa.String(length=36), nullable=False),
        sa.Column("target_person_id", sa.String(length=36), nullable=False),
        sa.Column("relationship_type", sa.String(length=100), nullable=False),
        sa.Column("provenance_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_person_id"],
            ["people.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_person_id"],
            ["people.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_person_id",
            "target_person_id",
            "relationship_type",
            name="uq_relationship",
        ),
    )
    op.create_table(
        "prompt_templates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("purpose", sa.String(length=200), nullable=False),
        sa.Column("tags_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("template_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("variables_json", sa.Text(), nullable=False),
        sa.Column("response_schema_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["prompt_templates.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_id", "version", name="uq_prompt_version"),
    )
    op.create_table(
        "provider_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("provider_id", sa.String(length=100), nullable=False),
        sa.Column("model", sa.String(length=300), nullable=False),
        sa.Column("secret_reference", sa.String(length=300), nullable=True),
        sa.Column("settings_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "consent_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("provider_profile_id", sa.String(length=36), nullable=False),
        sa.Column("allowed_modules_json", sa.Text(), nullable=False),
        sa.Column("allowed_purposes_json", sa.Text(), nullable=False),
        sa.Column("allowed_data_classes_json", sa.Text(), nullable=False),
        sa.Column("model_allowlist_json", sa.Text(), nullable=False),
        sa.Column("max_cost_usd", sa.Float(), nullable=True),
        sa.Column("retain_payloads", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("revoked_at", sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(
            ["provider_profile_id"],
            ["provider_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "llm_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("prompt_version_id", sa.String(length=36), nullable=True),
        sa.Column("consent_profile_id", sa.String(length=36), nullable=True),
        sa.Column("provider_id", sa.String(length=100), nullable=False),
        sa.Column("model", sa.String(length=300), nullable=False),
        sa.Column("purpose", sa.String(length=200), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_hash", sa.String(length=64), nullable=True),
        sa.Column("input_payload", sa.Text(), nullable=True),
        sa.Column("output_payload", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("started_at", sa.String(length=40), nullable=False),
        sa.Column("completed_at", sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(["consent_profile_id"], ["consent_profiles.id"]),
        sa.ForeignKeyConstraint(["prompt_version_id"], ["prompt_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("llm_runs")
    op.drop_table("consent_profiles")
    op.drop_table("provider_profiles")
    op.drop_table("prompt_versions")
    op.drop_table("prompt_templates")
    op.drop_table("relationships")
    op.drop_table("facts")
    op.drop_table("person_identifiers")
    op.drop_index("ix_people_workspace_name", table_name="people")
    op.drop_table("people")
    op.drop_table("workspaces")
