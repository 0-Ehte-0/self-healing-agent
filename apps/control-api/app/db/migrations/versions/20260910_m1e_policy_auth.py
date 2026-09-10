"""M1-E Policy evaluation, user sessions, and automation controls schema extensions.

Revision ID: 20260910_m1e_policy_auth
Revises: 20260910_m1d_action_catalog
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260910_m1e_policy_auth"
down_revision = "20260910_m1d_action_catalog"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Create policy_decisions table
    op.create_table(
        "policy_decisions",
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
        sa.Column("policy_id", sa.UUID(), sa.ForeignKey("policies.id"), nullable=True),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("container_id", sa.String(128), nullable=False),
        sa.Column("binding_generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("reason_codes", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("rule_results", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("evaluated_facts", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("evidence_freshness_seconds", sa.Float(), nullable=True),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.CheckConstraint(
            "decision IN ('ALLOW', 'DENY', 'REQUIRE_APPROVAL', 'DEFER')",
            name="chk_policy_decision_outcome",
        ),
    )
    op.create_index("ix_policy_decisions_incident_id", "policy_decisions", ["incident_id"])
    op.create_index("ix_policy_decisions_plan", "policy_decisions", ["plan_id", "plan_version"])

    # 2. Create user_sessions table
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("session_token", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_accessed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("is_revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(256), nullable=True),
    )
    op.create_index("ix_user_sessions_session_token", "user_sessions", ["session_token"])
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])

    # 3. Create automation_controls table
    op.create_table(
        "automation_controls",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("mode", sa.String(32), nullable=False, server_default="APPROVAL_REQUIRED"),
        sa.Column(
            "emergency_stopped_resources",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_by", sa.String(128), nullable=False, server_default="system:init"),
        sa.Column("reason", sa.Text(), nullable=False, server_default="Initial startup state"),
        sa.CheckConstraint(
            "mode IN ('DISABLED', 'APPROVAL_REQUIRED', 'AUTOMATIC')",
            name="chk_automation_mode",
        ),
    )


def downgrade():
    op.drop_table("automation_controls")
    op.drop_table("user_sessions")
    op.drop_table("policy_decisions")
