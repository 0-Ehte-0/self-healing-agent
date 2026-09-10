"""M1-G Independent verification, observation sample history, and recovery attribution.

Revision ID: 20260910_m1g_verification
Revises: 20260910_m1f_docker_adapter
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260910_m1g_verification"
down_revision = "20260910_m1f_docker_adapter"
branch_labels = None
depends_on = None


def upgrade():
    # Extend verification_results with M1-G Section 4.2 & Section 11 contracts
    op.add_column(
        "verification_results",
        sa.Column("incident_id", sa.UUID(), sa.ForeignKey("incidents.id"), nullable=True),
    )
    op.add_column(
        "verification_results",
        sa.Column("profile_version", sa.String(32), nullable=True),
    )
    op.add_column(
        "verification_results",
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "verification_results",
        sa.Column("warm_up_duration_seconds", sa.Float(), nullable=True),
    )
    op.add_column(
        "verification_results",
        sa.Column("stabilization_resets", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "verification_results",
        sa.Column("samples", JSONB, nullable=False, server_default="[]"),
    )
    op.add_column(
        "verification_results",
        sa.Column("attribution", sa.String(32), nullable=False, server_default="AGENT_HEALED"),
    )

    op.create_index(
        "ix_verification_results_incident_id",
        "verification_results",
        ["incident_id"],
    )


def downgrade():
    op.drop_index("ix_verification_results_incident_id", table_name="verification_results")
    op.drop_column("verification_results", "attribution")
    op.drop_column("verification_results", "samples")
    op.drop_column("verification_results", "stabilization_resets")
    op.drop_column("verification_results", "warm_up_duration_seconds")
    op.drop_column("verification_results", "attempt_number")
    op.drop_column("verification_results", "profile_version")
    op.drop_column("verification_results", "incident_id")
