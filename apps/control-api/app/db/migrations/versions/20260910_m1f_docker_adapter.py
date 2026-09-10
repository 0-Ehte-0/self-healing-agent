"""M1-F Docker adapter, execution contract extensions, and resource lock quarantining.

Revision ID: 20260910_m1f_docker_adapter
Revises: 20260910_m1e_policy_auth
"""

import sqlalchemy as sa
from alembic import op

revision = "20260910_m1f_docker_adapter"
down_revision = "20260910_m1e_policy_auth"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Extend executions table with Section 4.2 contract fields
    op.add_column(
        "executions",
        sa.Column("plan_id", sa.UUID(), sa.ForeignKey("remediation_plans.id"), nullable=True),
    )
    op.add_column(
        "executions", sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column("executions", sa.Column("container_id", sa.String(128), nullable=True))
    op.add_column("executions", sa.Column("binding_generation", sa.Integer(), nullable=True))
    op.add_column("executions", sa.Column("lock_token", sa.UUID(), nullable=True))
    op.add_column("executions", sa.Column("uncertainty_reason", sa.Text(), nullable=True))

    op.create_index(
        "ix_executions_incident_plan_attempt",
        "executions",
        ["incident_id", "plan_id", "attempt_number"],
    )

    # 2. Extend resource_locks table with quarantine support
    op.add_column(
        "resource_locks",
        sa.Column("quarantined", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("resource_locks", sa.Column("quarantine_reason", sa.Text(), nullable=True))
    op.add_column(
        "resource_locks", sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade():
    op.drop_column("resource_locks", "quarantined_at")
    op.drop_column("resource_locks", "quarantine_reason")
    op.drop_column("resource_locks", "quarantined")

    op.drop_index("ix_executions_incident_plan_attempt", table_name="executions")
    op.drop_column("executions", "uncertainty_reason")
    op.drop_column("executions", "lock_token")
    op.drop_column("executions", "binding_generation")
    op.drop_column("executions", "container_id")
    op.drop_column("executions", "attempt_number")
    op.drop_column("executions", "plan_id")
