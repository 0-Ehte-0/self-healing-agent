"""Control-plane record model, restricted runtime role, and audit enforcement.

Revision ID: 20260909_phase4
Revises: 91477cb0cf34
"""

import json
from pathlib import Path

from alembic import op

revision = "20260909_phase4"
down_revision = "91477cb0cf34"
branch_labels = None
depends_on = None


def upgrade():
    # Frozen DDL, generated once from the Phase 4 model. Never import mutable ORM models here.
    for statement in json.loads(Path(__file__).with_name("phase4_schema.json").read_text()):
        op.execute(statement)
    op.execute("""DO $$ BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'control_app') THEN
            CREATE ROLE control_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
        END IF;
    END $$""")
    op.execute("GRANT USAGE ON SCHEMA public TO control_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO control_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO control_app")
    op.execute("REVOKE ALL ON alembic_version FROM control_app")
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON audit_entries FROM control_app")
    op.execute("""CREATE FUNCTION protect_audit() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'audit_entries is append-only'; END $$""")
    op.execute(
        "CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_entries FOR EACH STATEMENT EXECUTE FUNCTION protect_audit()"
    )
    op.execute("""CREATE FUNCTION audit_record_change() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE entry jsonb; old_entry jsonb; actor_name text; incident uuid; entity uuid;
        BEGIN
            actor_name := current_setting('app.actor', true);
            IF actor_name IS NULL OR length(trim(actor_name)) = 0 THEN
                RAISE EXCEPTION 'app.actor is required for mutations';
            END IF;
            IF TG_OP = 'DELETE' THEN entry := to_jsonb(OLD); ELSE entry := to_jsonb(NEW); END IF;
            IF TG_OP = 'UPDATE' THEN old_entry := to_jsonb(OLD); END IF;
            incident := (entry->>'incident_id')::uuid;
            IF TG_TABLE_NAME = 'incidents' THEN incident := (entry->>'id')::uuid; END IF;
            IF TG_TABLE_NAME IN ('remediation_steps', 'approvals') THEN
                SELECT incident_id INTO incident FROM public.remediation_plans WHERE id = (entry->>'plan_id')::uuid;
            END IF;
            IF TG_TABLE_NAME = 'verification_results' THEN
                SELECT incident_id INTO incident FROM public.executions WHERE id = (entry->>'execution_id')::uuid;
            END IF;
            entity := coalesce(entry->>'id', entry->>'event_id', entry->>'resource_id')::uuid;
            -- Never copy password hashes into the audit log.
            entry := entry - 'password_hash'; old_entry := old_entry - 'password_hash';
            INSERT INTO public.audit_entries(id, actor, operation, entity_type, entity_id, incident_id, details)
            VALUES (gen_random_uuid(), actor_name, TG_OP, TG_TABLE_NAME, entity, incident,
                jsonb_build_object('before', old_entry, 'after', CASE WHEN TG_OP='DELETE' THEN NULL ELSE entry END));
            RETURN NULL;
        END $$""")
    op.execute("""CREATE FUNCTION guard_incident() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE allowed boolean;
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF NEW.state != 'DETECTED' OR NEW.version != 1 OR NEW.attempts != 0 THEN
              RAISE EXCEPTION 'Incidents must start DETECTED at version 1, attempts 0';
            END IF;
            RETURN NEW;
          END IF;
          IF NEW.version != OLD.version + 1 THEN RAISE EXCEPTION 'Incident version must increment by one'; END IF;
          IF NEW.state != OLD.state THEN
            allowed := CASE OLD.state
              WHEN 'DETECTED' THEN NEW.state IN ('TRIAGED')
              WHEN 'TRIAGED' THEN NEW.state IN ('DIAGNOSED','ESCALATED')
              WHEN 'DIAGNOSED' THEN NEW.state IN ('PLANNED','ESCALATED')
              WHEN 'PLANNED' THEN NEW.state IN ('PENDING_APPROVAL','EXECUTING')
              WHEN 'PENDING_APPROVAL' THEN NEW.state IN ('APPROVED','ESCALATED')
              WHEN 'APPROVED' THEN NEW.state IN ('EXECUTING')
              WHEN 'EXECUTING' THEN NEW.state IN ('VERIFYING','FAILED')
              WHEN 'VERIFYING' THEN NEW.state IN ('RESOLVED','DIAGNOSED','ESCALATED')
              WHEN 'FAILED' THEN NEW.state IN ('ROLLEDBACK','ESCALATED')
              ELSE false END;
            IF NOT allowed THEN RAISE EXCEPTION 'Illegal incident transition: % -> %', OLD.state, NEW.state; END IF;
            IF OLD.state = 'PLANNED' AND ((OLD.approval_required AND NEW.state != 'PENDING_APPROVAL') OR
              (NOT OLD.approval_required AND NEW.state != 'EXECUTING')) THEN
              RAISE EXCEPTION 'Approval routing mismatch';
            END IF;
          END IF;
          IF OLD.state = 'VERIFYING' AND NEW.state = 'DIAGNOSED' THEN
            IF OLD.attempts >= OLD.retry_limit OR NEW.attempts != OLD.attempts + 1 THEN
              RAISE EXCEPTION 'Retry limit exhausted or invalid attempt counter'; END IF;
          ELSIF NEW.attempts != OLD.attempts THEN RAISE EXCEPTION 'Attempts change only on retry'; END IF;
          IF NEW.state = 'RESOLVED' THEN NEW.resolved_at := now(); END IF;
          NEW.updated_at := now();
          RETURN NEW;
        END $$""")
    op.execute(
        "CREATE TRIGGER incident_guard BEFORE INSERT OR UPDATE ON incidents FOR EACH ROW EXECUTE FUNCTION guard_incident()"
    )
    op.execute("""CREATE FUNCTION guard_execution() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM remediation_steps s JOIN remediation_plans p ON p.id=s.plan_id
              WHERE s.id=NEW.step_id AND s.resource_id=NEW.resource_id AND p.incident_id=NEW.incident_id) THEN
            RAISE EXCEPTION 'Execution plan/target mismatch'; END IF;
          IF TG_OP='INSERT' THEN
            IF NEW.status != 'PENDING' OR NEW.version != 1 THEN RAISE EXCEPTION 'Invalid initial execution'; END IF;
          ELSE
            IF NEW.version != OLD.version+1 THEN RAISE EXCEPTION 'Stale execution version'; END IF;
            IF NEW.idempotency_key != OLD.idempotency_key OR NEW.step_id != OLD.step_id OR
                NEW.resource_id != OLD.resource_id OR NEW.incident_id != OLD.incident_id THEN
                RAISE EXCEPTION 'Execution identity is immutable'; END IF;
            IF NEW.status != OLD.status AND NOT (
              (OLD.status='PENDING' AND NEW.status='RUNNING') OR
              (OLD.status='RUNNING' AND NEW.status IN ('SUCCEEDED','FAILED')) OR
              (OLD.status='FAILED' AND NEW.status='ROLLEDBACK')) THEN
                RAISE EXCEPTION 'Illegal execution transition'; END IF;
          END IF;
          RETURN NEW;
        END $$""")
    op.execute(
        "CREATE TRIGGER execution_guard BEFORE INSERT OR UPDATE ON executions FOR EACH ROW EXECUTE FUNCTION guard_execution()"
    )
    tables = [
        "resources",
        "users",
        "events",
        "incidents",
        "incident_events",
        "evidence_items",
        "diagnoses",
        "remediation_plans",
        "remediation_steps",
        "executions",
        "verification_results",
        "policies",
        "approvals",
        "resource_locks",
        "model_invocations",
        "anomaly_scores",
    ]
    for table in tables:
        op.execute(
            f"CREATE TRIGGER audit_change AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION audit_record_change()"
        )
    op.execute("REVOKE ALL ON FUNCTION audit_record_change() FROM PUBLIC")


def downgrade():
    tables = [
        "anomaly_scores",
        "model_invocations",
        "resource_locks",
        "audit_entries",
        "approvals",
        "policies",
        "verification_results",
        "executions",
        "remediation_steps",
        "remediation_plans",
        "diagnoses",
        "evidence_items",
        "incident_events",
        "incidents",
        "events",
        "users",
        "resources",
    ]
    for table in tables:
        op.execute(f"DROP TABLE {table} CASCADE")
    for name in ["guard_execution", "guard_incident", "audit_record_change", "protect_audit"]:
        op.execute(f"DROP FUNCTION {name}()")
    for name in [
        "user_role",
        "event_source",
        "severity",
        "incident_state",
        "risk_level",
        "execution_status",
    ]:
        op.execute(f"DROP TYPE {name}")
    # A cluster-wide role may be used by another database. Never drop it here.
