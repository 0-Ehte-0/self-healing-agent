"""M1-H durable demo command intent and preliminary verification observations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260911_m1h_console"
down_revision = "20260910_m1g_verification"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "demo_commands",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("idempotency_key", sa.UUID(), unique=True, nullable=False),
        sa.Column("scenario_id", sa.String(16), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result", JSONB, nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.CheckConstraint("scenario_id IN ('SCN-001','SCN-002','SCN-003')"),
        sa.CheckConstraint("action IN ('inject','clear')"),
        sa.CheckConstraint("status IN ('PENDING','SUCCEEDED','UNCERTAIN')"),
    )
    op.create_table(
        "verification_observations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("incident_id", sa.UUID(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("execution_id", sa.UUID(), sa.ForeignKey("executions.id"), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
    )
    op.create_index(
        "ix_verification_observations_incident",
        "verification_observations",
        ["incident_id", "created_at"],
    )
    for table in (
        "demo_commands",
        "verification_observations",
        "dry_runs",
        "user_sessions",
        "automation_controls",
        "policy_decisions",
        "attention_items",
        "escalation_records",
    ):
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO control_app")
    op.execute(
        "CREATE TRIGGER audit_change AFTER INSERT OR UPDATE OR DELETE ON demo_commands FOR EACH ROW EXECUTE FUNCTION audit_record_change()"
    )
    op.execute("DROP TRIGGER IF EXISTS audit_change ON dry_runs")
    op.execute(
        "CREATE TRIGGER audit_change AFTER INSERT OR UPDATE OR DELETE ON dry_runs FOR EACH ROW EXECUTE FUNCTION audit_record_change()"
    )
    # The automation singleton has a text ID, so it cannot use the UUID domain trigger.
    op.execute("""CREATE FUNCTION audit_automation_change() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE actor_name text;
        BEGIN
            actor_name := current_setting('app.actor', true);
            IF actor_name IS NULL OR length(trim(actor_name)) = 0 THEN
                RAISE EXCEPTION 'app.actor is required for automation changes';
            END IF;
            INSERT INTO public.audit_entries(id, actor, operation, entity_type, entity_id, details)
            VALUES (gen_random_uuid(), actor_name, TG_OP, 'automation_controls',
                '00000000-0000-0000-0000-000000000001'::uuid,
                jsonb_build_object('before', CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END, 'after', to_jsonb(NEW)));
            RETURN NULL;
        END $$""")
    op.execute(
        "CREATE TRIGGER automation_audit AFTER INSERT OR UPDATE ON automation_controls FOR EACH ROW EXECUTE FUNCTION audit_automation_change()"
    )


def downgrade():
    op.execute("DROP TRIGGER automation_audit ON automation_controls")
    op.execute("DROP FUNCTION audit_automation_change()")
    op.execute("DROP TRIGGER IF EXISTS audit_change ON dry_runs")
    op.drop_table("verification_observations")
    op.drop_table("demo_commands")
