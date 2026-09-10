"""M1-B workflow runtime: safe escalation edges, outbox, leases, schedules, and checkpoints.

Revision ID: 20260910_m1b_workflow
Revises: 20260909_audit_delete
"""

from alembic import op

revision = "20260910_m1b_workflow"
down_revision = "20260909_audit_delete"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Update guard_incident trigger to allow PLANNED -> ESCALATED and APPROVED -> ESCALATED
    op.execute("""
    CREATE OR REPLACE FUNCTION guard_incident() RETURNS trigger LANGUAGE plpgsql AS $$
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
              WHEN 'PLANNED' THEN NEW.state IN ('PENDING_APPROVAL','EXECUTING','ESCALATED')
              WHEN 'PENDING_APPROVAL' THEN NEW.state IN ('APPROVED','ESCALATED')
              WHEN 'APPROVED' THEN NEW.state IN ('EXECUTING','ESCALATED')
              WHEN 'EXECUTING' THEN NEW.state IN ('VERIFYING','FAILED')
              WHEN 'VERIFYING' THEN NEW.state IN ('RESOLVED','DIAGNOSED','ESCALATED')
              WHEN 'FAILED' THEN NEW.state IN ('ROLLEDBACK','ESCALATED')
              ELSE false END;
            IF NOT allowed THEN RAISE EXCEPTION 'Illegal incident transition: % -> %', OLD.state, NEW.state; END IF;
            IF OLD.state = 'PLANNED' AND NEW.state != 'ESCALATED' AND ((OLD.approval_required AND NEW.state != 'PENDING_APPROVAL') OR
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
        END $$;
    """)

    # 2. Transactional outbox table
    op.execute("""
    CREATE TABLE IF NOT EXISTS outbox_events (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        event_type VARCHAR(64) NOT NULL,
        aggregate_type VARCHAR(64) NOT NULL DEFAULT 'incident',
        aggregate_id UUID NOT NULL,
        aggregate_version INT NOT NULL,
        payload JSONB NOT NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'PUBLISHED', 'FAILED')),
        retry_count INT NOT NULL DEFAULT 0,
        last_error TEXT,
        published_at TIMESTAMPTZ,
        actor VARCHAR(128) NOT NULL
    )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outbox_events_status_created ON outbox_events(status, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outbox_events_aggregate ON outbox_events(aggregate_id, aggregate_version)"
    )

    # 3. Workflow leases table
    op.execute("""
    CREATE TABLE IF NOT EXISTS workflow_leases (
        incident_id UUID PRIMARY KEY REFERENCES incidents(id) ON DELETE CASCADE,
        owner VARCHAR(128) NOT NULL,
        token UUID NOT NULL UNIQUE,
        acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at TIMESTAMPTZ NOT NULL,
        incident_version INT NOT NULL,
        CONSTRAINT chk_workflow_lease_expiry CHECK (expires_at > acquired_at)
    )
    """)

    # 4. Workflow schedules table
    op.execute("""
    CREATE TABLE IF NOT EXISTS workflow_schedules (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        incident_version INT NOT NULL,
        next_run_at TIMESTAMPTZ NOT NULL,
        wait_reason VARCHAR(64) NOT NULL,
        deadline TIMESTAMPTZ,
        status VARCHAR(32) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'PROCESSED', 'CANCELLED')),
        resume_metadata JSONB NOT NULL DEFAULT '{}'
    )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_schedules_status_next_run ON workflow_schedules(status, next_run_at)"
    )

    # 5. Workflow checkpoints and writes for LangGraph PostgreSQL checkpointer
    op.execute("""
    CREATE TABLE IF NOT EXISTS workflow_checkpoints (
        thread_id VARCHAR(128) NOT NULL,
        checkpoint_ns VARCHAR(128) NOT NULL DEFAULT '',
        checkpoint_id VARCHAR(128) NOT NULL,
        parent_checkpoint_id VARCHAR(128),
        type VARCHAR(64) NOT NULL,
        checkpoint JSONB NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
    )
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS workflow_checkpoint_writes (
        thread_id VARCHAR(128) NOT NULL,
        checkpoint_ns VARCHAR(128) NOT NULL DEFAULT '',
        checkpoint_id VARCHAR(128) NOT NULL,
        task_id VARCHAR(128) NOT NULL,
        idx INT NOT NULL,
        channel VARCHAR(128) NOT NULL,
        type VARCHAR(64),
        value JSONB NOT NULL,
        PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
    )
    """)

    # 6. Worker heartbeats table
    op.execute("""
    CREATE TABLE IF NOT EXISTS worker_heartbeats (
        worker_id VARCHAR(128) PRIMARY KEY,
        last_heartbeat TIMESTAMPTZ NOT NULL DEFAULT now(),
        status VARCHAR(32) NOT NULL DEFAULT 'HEALTHY',
        metadata JSONB NOT NULL DEFAULT '{}'
    )
    """)

    # 7. Role permissions
    op.execute("""
    DO $$ BEGIN
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'control_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON outbox_events, workflow_leases, workflow_schedules,
                workflow_checkpoints, workflow_checkpoint_writes, worker_heartbeats TO control_app;
        END IF;
    END $$;
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS worker_heartbeats CASCADE")
    op.execute("DROP TABLE IF EXISTS workflow_checkpoint_writes CASCADE")
    op.execute("DROP TABLE IF EXISTS workflow_checkpoints CASCADE")
    op.execute("DROP TABLE IF EXISTS workflow_schedules CASCADE")
    op.execute("DROP TABLE IF EXISTS workflow_leases CASCADE")
    op.execute("DROP TABLE IF EXISTS outbox_events CASCADE")

    # Revert guard_incident trigger to omit PLANNED/APPROVED -> ESCALATED
    op.execute("""
    CREATE OR REPLACE FUNCTION guard_incident() RETURNS trigger LANGUAGE plpgsql AS $$
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
        END $$;
    """)
