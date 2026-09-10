"""M1-D Action Catalog and Planning schema extensions.

Revision ID: 20260910_m1d_action_catalog
Revises: 20260910_m1c_evidence_diagnosis
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260910_m1d_action_catalog"
down_revision = "20260910_m1c_evidence_diagnosis"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Extend remediation_plans with content hash, target binding, and verification profile
    op.add_column("remediation_plans", sa.Column("content_hash", sa.String(64), nullable=True))
    op.add_column("remediation_plans", sa.Column("container_id", sa.String(128), nullable=True))
    op.add_column("remediation_plans", sa.Column("binding_generation", sa.Integer(), nullable=True))
    op.add_column(
        "remediation_plans", sa.Column("verification_profile", sa.String(64), nullable=True)
    )

    # 2. Extend remediation_steps with action schema version
    op.add_column(
        "remediation_steps",
        sa.Column(
            "action_schema_version",
            sa.String(16),
            nullable=False,
            server_default="1.0",
        ),
    )

    # 3. Create attention_items table
    op.create_table(
        "attention_items",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("incident_id", sa.UUID(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(256), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("actor", sa.String(128), nullable=False),
    )
    op.create_index("ix_attention_items_incident_id", "attention_items", ["incident_id"])

    # 4. Create escalation_records table
    op.create_table(
        "escalation_records",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("incident_id", sa.UUID(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("root_cause", sa.String(128), nullable=False),
        sa.Column("escalation_reason", sa.Text(), nullable=False),
        sa.Column("ticket_reference", sa.String(128), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
    )
    op.create_index("ix_escalation_records_incident_id", "escalation_records", ["incident_id"])

    # 5. Create dry_runs table
    op.create_table(
        "dry_runs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("incident_id", sa.UUID(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("plan_id", sa.UUID(), sa.ForeignKey("remediation_plans.id"), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "policy_evaluation", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "validation_result", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("simulated_steps", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("actor", sa.String(128), nullable=False),
    )
    op.create_index("ix_dry_runs_incident_id", "dry_runs", ["incident_id"])


def downgrade():
    op.drop_index("ix_dry_runs_incident_id", table_name="dry_runs")
    op.drop_table("dry_runs")

    op.drop_index("ix_escalation_records_incident_id", table_name="escalation_records")
    op.drop_table("escalation_records")

    op.drop_index("ix_attention_items_incident_id", table_name="attention_items")
    op.drop_table("attention_items")

    op.drop_column("remediation_steps", "action_schema_version")

    op.drop_column("remediation_plans", "verification_profile")
    op.drop_column("remediation_plans", "binding_generation")
    op.drop_column("remediation_plans", "container_id")
    op.drop_column("remediation_plans", "content_hash")
