"""Cross-record integrity and approved-plan immutability.

Revision ID: 20260909_record_guards
Revises: 20260909_phase4
"""

from alembic import op

revision = "20260909_record_guards"
down_revision = "20260909_phase4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint("uq_plan_id_version", "remediation_plans", ["id", "version"])
    op.create_foreign_key(
        "fk_approval_plan_version",
        "approvals",
        "remediation_plans",
        ["plan_id", "plan_version"],
        ["id", "version"],
    )
    op.execute("""CREATE FUNCTION guard_plan() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP != 'INSERT' AND OLD.approved THEN
                RAISE EXCEPTION 'Approved plans are immutable'; END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            IF NOT EXISTS (SELECT 1 FROM diagnoses WHERE id=NEW.diagnosis_id AND incident_id=NEW.incident_id) THEN
                RAISE EXCEPTION 'Plan diagnosis belongs to another incident'; END IF;
            RETURN NEW;
        END $$""")
    op.execute(
        "CREATE TRIGGER plan_guard BEFORE INSERT OR UPDATE OR DELETE ON remediation_plans FOR EACH ROW EXECUTE FUNCTION guard_plan()"
    )
    op.execute("""CREATE FUNCTION guard_plan_step() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE locked boolean;
        BEGIN
            IF TG_OP != 'INSERT' THEN
                SELECT approved INTO locked FROM remediation_plans WHERE id=OLD.plan_id FOR UPDATE;
                IF locked THEN RAISE EXCEPTION 'Approved plan steps are immutable'; END IF;
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            SELECT approved INTO locked FROM remediation_plans WHERE id=NEW.plan_id FOR UPDATE;
            IF locked THEN RAISE EXCEPTION 'Approved plan steps are immutable'; END IF;
            RETURN NEW;
        END $$""")
    op.execute(
        "CREATE TRIGGER step_guard BEFORE INSERT OR UPDATE OR DELETE ON remediation_steps FOR EACH ROW EXECUTE FUNCTION guard_plan_step()"
    )
    op.execute("""CREATE FUNCTION guard_approval_record() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP != 'INSERT' THEN RAISE EXCEPTION 'Approval records are immutable'; END IF;
            IF NEW.expires_at > NEW.created_at + interval '30 minutes' OR NEW.expires_at <= now() THEN
                RAISE EXCEPTION 'Approval expiration must be within thirty minutes'; END IF;
            IF NOT EXISTS (SELECT 1 FROM users WHERE id=NEW.approver_id AND enabled AND role IN ('approver','admin')) THEN
                RAISE EXCEPTION 'Approval requires an enabled approver or admin'; END IF;
            RETURN NEW;
        END $$""")
    op.execute(
        "CREATE TRIGGER approval_guard BEFORE INSERT OR UPDATE OR DELETE ON approvals FOR EACH ROW EXECUTE FUNCTION guard_approval_record()"
    )


def downgrade():
    for table, trigger in [
        ("remediation_plans", "plan_guard"),
        ("remediation_steps", "step_guard"),
        ("approvals", "approval_guard"),
    ]:
        op.execute(f"DROP TRIGGER {trigger} ON {table}")
    for function in ["guard_plan", "guard_plan_step", "guard_approval_record"]:
        op.execute(f"DROP FUNCTION {function}()")
    op.drop_constraint("fk_approval_plan_version", "approvals", type_="foreignkey")
    op.drop_constraint("uq_plan_id_version", "remediation_plans", type_="unique")
