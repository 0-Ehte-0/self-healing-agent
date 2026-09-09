"""Retain the complete before snapshot when a domain record is deleted."""

from alembic import op

revision = "20260909_audit_delete"
down_revision = "20260909_record_guards"
branch_labels = None
depends_on = None


def install(include_delete):
    condition = "TG_OP IN ('UPDATE', 'DELETE')" if include_delete else "TG_OP = 'UPDATE'"
    op.execute(f"""CREATE OR REPLACE FUNCTION audit_record_change() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE entry jsonb; old_entry jsonb; actor_name text; incident uuid; entity uuid;
        BEGIN
            actor_name := current_setting('app.actor', true);
            IF actor_name IS NULL OR length(trim(actor_name)) = 0 THEN
                RAISE EXCEPTION 'app.actor is required for mutations';
            END IF;
            IF TG_OP = 'DELETE' THEN entry := to_jsonb(OLD); ELSE entry := to_jsonb(NEW); END IF;
            IF {condition} THEN old_entry := to_jsonb(OLD); END IF;
            incident := (entry->>'incident_id')::uuid;
            IF TG_TABLE_NAME = 'incidents' THEN incident := (entry->>'id')::uuid; END IF;
            IF TG_TABLE_NAME IN ('remediation_steps', 'approvals') THEN
                SELECT incident_id INTO incident FROM public.remediation_plans WHERE id = (entry->>'plan_id')::uuid;
            END IF;
            IF TG_TABLE_NAME = 'verification_results' THEN
                SELECT incident_id INTO incident FROM public.executions WHERE id = (entry->>'execution_id')::uuid;
            END IF;
            entity := coalesce(entry->>'id', entry->>'event_id', entry->>'resource_id')::uuid;
            entry := entry - 'password_hash'; old_entry := old_entry - 'password_hash';
            INSERT INTO public.audit_entries(id, actor, operation, entity_type, entity_id, incident_id, details)
            VALUES (gen_random_uuid(), actor_name, TG_OP, TG_TABLE_NAME, entity, incident,
                jsonb_build_object('before', old_entry, 'after', CASE WHEN TG_OP='DELETE' THEN NULL ELSE entry END));
            RETURN NULL;
        END $$""")


def upgrade():
    install(True)


def downgrade():
    install(False)
