"""M1-C Evidence and Diagnosis schema extensions.

Revision ID: 20260910_m1c_evidence_diagnosis
Revises: 20260910_m1b_workflow
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260910_m1c_evidence_diagnosis"
down_revision = "20260910_m1b_workflow"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Extend diagnoses table with rule identity, structured contradictions, escalation reason, and parent link
    op.add_column("diagnoses", sa.Column("rule_id", sa.String(64), nullable=True))
    op.add_column("diagnoses", sa.Column("rule_version", sa.String(16), nullable=True))
    op.add_column(
        "diagnoses",
        sa.Column(
            "contradictory_findings",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("diagnoses", sa.Column("escalation_reason", sa.Text(), nullable=True))
    op.add_column(
        "diagnoses",
        sa.Column(
            "parent_diagnosis_id",
            sa.UUID(),
            sa.ForeignKey("diagnoses.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_diagnoses_parent_diagnosis_id", "diagnoses", ["parent_diagnosis_id"])

    # 2. Extend evidence_items table with signal unit and binding generation
    op.add_column("evidence_items", sa.Column("unit", sa.String(32), nullable=True))
    op.add_column("evidence_items", sa.Column("binding_generation", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("evidence_items", "binding_generation")
    op.drop_column("evidence_items", "unit")

    op.drop_index("ix_diagnoses_parent_diagnosis_id", table_name="diagnoses")
    op.drop_column("diagnoses", "parent_diagnosis_id")
    op.drop_column("diagnoses", "escalation_reason")
    op.drop_column("diagnoses", "contradictory_findings")
    op.drop_column("diagnoses", "rule_version")
    op.drop_column("diagnoses", "rule_id")
