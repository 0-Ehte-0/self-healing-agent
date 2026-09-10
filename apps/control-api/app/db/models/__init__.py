"""PostgreSQL system of record. JSON columns hold versioned, bounded domain payloads."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from app.db.base import Base
from sharedmodels.enums import (
    EventSource,
    ExecutionStatus,
    IncidentState,
    RiskLevel,
    Severity,
    UserRole,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column


def enum(cls: type, name: str) -> sa.Enum:
    return sa.Enum(cls, name=name, values_callable=lambda values: [v.value for v in values])


class Record:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )


class Resource(Record, Base):
    __tablename__ = "resources"
    provider: Mapped[str] = mapped_column(sa.String(32))
    external_id: Mapped[str] = mapped_column(sa.String(512))
    name: Mapped[str] = mapped_column(sa.String(128))
    environment: Mapped[str] = mapped_column(sa.String(32), default="local")
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    managed: Mapped[bool] = mapped_column(default=False)
    __table_args__ = (sa.UniqueConstraint("provider", "external_id"),)


class User(Record, Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(sa.String(128), unique=True)
    password_hash: Mapped[str] = mapped_column(sa.String(512))
    role: Mapped[UserRole] = mapped_column(enum(UserRole, "user_role"))
    enabled: Mapped[bool] = mapped_column(default=True)


class Event(Record, Base):
    __tablename__ = "events"
    resource_id: Mapped[UUID] = mapped_column(sa.ForeignKey("resources.id"), index=True)
    source: Mapped[EventSource] = mapped_column(enum(EventSource, "event_source"))
    fingerprint: Mapped[str] = mapped_column(sa.String(64))
    dedup_window: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    occurred_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    severity: Mapped[Severity] = mapped_column(enum(Severity, "severity"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    actor: Mapped[str] = mapped_column(sa.String(128))
    __table_args__ = (
        sa.UniqueConstraint(
            "source", "resource_id", "fingerprint", "dedup_window", name="uq_event_dedup"
        ),
    )


class Incident(Record, Base):
    __tablename__ = "incidents"
    resource_id: Mapped[UUID] = mapped_column(sa.ForeignKey("resources.id"), index=True)
    correlation_key: Mapped[str] = mapped_column(sa.String(256), index=True)
    state: Mapped[IncidentState] = mapped_column(
        enum(IncidentState, "incident_state"), default=IncidentState.DETECTED
    )
    severity: Mapped[Severity] = mapped_column(enum(Severity, "severity"))
    version: Mapped[int] = mapped_column(default=1)
    attempts: Mapped[int] = mapped_column(default=0)
    retry_limit: Mapped[int] = mapped_column(default=2)
    approval_required: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    __table_args__ = (
        sa.CheckConstraint("version >= 1 AND attempts >= 0 AND retry_limit >= 0"),
        sa.Index(
            "uq_active_incident",
            "correlation_key",
            unique=True,
            postgresql_where=sa.text("state NOT IN ('RESOLVED', 'ESCALATED', 'ROLLEDBACK')"),
        ),
    )


class IncidentEvent(Base):
    __tablename__ = "incident_events"
    incident_id: Mapped[UUID] = mapped_column(sa.ForeignKey("incidents.id"), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(sa.ForeignKey("events.id"), primary_key=True)
    linked_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )


class EvidenceItem(Record, Base):
    __tablename__ = "evidence_items"
    incident_id: Mapped[UUID] = mapped_column(sa.ForeignKey("incidents.id"), index=True)
    kind: Mapped[str] = mapped_column(sa.String(32))
    source: Mapped[str] = mapped_column(sa.String(512))
    observed_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    unit: Mapped[str | None] = mapped_column(sa.String(32), nullable=True, default=None)
    binding_generation: Mapped[int | None] = mapped_column(sa.Integer, nullable=True, default=None)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    sha256: Mapped[str] = mapped_column(sa.String(64))
    actor: Mapped[str] = mapped_column(sa.String(128))


class Diagnosis(Record, Base):
    __tablename__ = "diagnoses"
    incident_id: Mapped[UUID] = mapped_column(sa.ForeignKey("incidents.id"), index=True)
    root_cause: Mapped[str] = mapped_column(sa.String(128))
    confidence: Mapped[float]
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB)
    rule_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, default=None)
    rule_version: Mapped[str | None] = mapped_column(sa.String(16), nullable=True, default=None)
    contradictory_findings: Mapped[list[str]] = mapped_column(JSONB, default=list)
    escalation_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True, default=None)
    parent_diagnosis_id: Mapped[UUID | None] = mapped_column(
        sa.ForeignKey("diagnoses.id"), nullable=True, default=None
    )
    reasoning: Mapped[dict[str, Any]] = mapped_column(JSONB)
    actor: Mapped[str] = mapped_column(sa.String(128))
    __table_args__ = (sa.CheckConstraint("confidence BETWEEN 0 AND 1"),)


class RemediationPlan(Record, Base):
    __tablename__ = "remediation_plans"
    incident_id: Mapped[UUID] = mapped_column(sa.ForeignKey("incidents.id"), index=True)
    diagnosis_id: Mapped[UUID] = mapped_column(sa.ForeignKey("diagnoses.id"))
    version: Mapped[int]
    risk: Mapped[RiskLevel] = mapped_column(enum(RiskLevel, "risk_level"))
    approved: Mapped[bool] = mapped_column(default=False)
    actor: Mapped[str] = mapped_column(sa.String(128))
    content_hash: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    container_id: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    binding_generation: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    verification_profile: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    __table_args__ = (
        sa.UniqueConstraint("incident_id", "version"),
        sa.UniqueConstraint("id", "version", name="uq_plan_id_version"),
        sa.CheckConstraint("version >= 1"),
    )


class RemediationStep(Record, Base):
    __tablename__ = "remediation_steps"
    plan_id: Mapped[UUID] = mapped_column(sa.ForeignKey("remediation_plans.id"), index=True)
    resource_id: Mapped[UUID] = mapped_column(sa.ForeignKey("resources.id"))
    position: Mapped[int]
    action: Mapped[str] = mapped_column(sa.String(128))
    action_schema_version: Mapped[str] = mapped_column(
        sa.String(16), default="1.0", server_default="1.0"
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    verification: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (
        sa.UniqueConstraint("plan_id", "position"),
        sa.CheckConstraint("position >= 0"),
    )


class Execution(Record, Base):
    __tablename__ = "executions"
    incident_id: Mapped[UUID] = mapped_column(sa.ForeignKey("incidents.id"), index=True)
    step_id: Mapped[UUID] = mapped_column(sa.ForeignKey("remediation_steps.id"))
    resource_id: Mapped[UUID] = mapped_column(sa.ForeignKey("resources.id"))
    idempotency_key: Mapped[str] = mapped_column(sa.String(256), unique=True)
    status: Mapped[ExecutionStatus] = mapped_column(
        enum(ExecutionStatus, "execution_status"), default=ExecutionStatus.PENDING
    )
    version: Mapped[int] = mapped_column(default=1)
    actor: Mapped[str] = mapped_column(sa.String(128))
    pre_state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    __table_args__ = (sa.CheckConstraint("version >= 1"),)


class VerificationResult(Record, Base):
    __tablename__ = "verification_results"
    execution_id: Mapped[UUID] = mapped_column(sa.ForeignKey("executions.id"), index=True)
    passed: Mapped[bool]
    window_start: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    health_score: Mapped[float]
    checks: Mapped[dict[str, Any]] = mapped_column(JSONB)
    actor: Mapped[str] = mapped_column(sa.String(128))
    __table_args__ = (
        sa.CheckConstraint("health_score BETWEEN 0 AND 1 AND window_end >= window_start"),
    )


class Policy(Record, Base):
    __tablename__ = "policies"
    name: Mapped[str] = mapped_column(sa.String(128))
    version: Mapped[int]
    environment: Mapped[str] = mapped_column(sa.String(32))
    action: Mapped[str] = mapped_column(sa.String(128))
    risk: Mapped[RiskLevel] = mapped_column(enum(RiskLevel, "risk_level"))
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(default=True)
    actor: Mapped[str] = mapped_column(sa.String(128))
    __table_args__ = (sa.UniqueConstraint("name", "version"), sa.CheckConstraint("version >= 1"))


class Approval(Record, Base):
    __tablename__ = "approvals"
    plan_id: Mapped[UUID] = mapped_column(sa.ForeignKey("remediation_plans.id"), index=True)
    plan_version: Mapped[int]
    approver_id: Mapped[UUID] = mapped_column(sa.ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(sa.String(16))
    rejection_reason: Mapped[str | None] = mapped_column(sa.Text)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["plan_id", "plan_version"],
            ["remediation_plans.id", "remediation_plans.version"],
            name="fk_approval_plan_version",
        ),
        sa.CheckConstraint("decision IN ('APPROVE', 'REJECT')"),
        sa.CheckConstraint(
            "decision != 'REJECT' OR length(trim(rejection_reason)) > 0 AND rejection_reason IS NOT NULL"
        ),
        sa.CheckConstraint("plan_version >= 1 AND expires_at > created_at"),
    )


class AuditEntry(Record, Base):
    __tablename__ = "audit_entries"
    sequence: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), unique=True)
    incident_id: Mapped[UUID | None] = mapped_column(sa.ForeignKey("incidents.id"), index=True)
    actor: Mapped[str] = mapped_column(sa.String(128))
    operation: Mapped[str] = mapped_column(sa.String(128))
    entity_type: Mapped[str] = mapped_column(sa.String(64))
    entity_id: Mapped[UUID]
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ResourceLock(Base):
    __tablename__ = "resource_locks"
    resource_id: Mapped[UUID] = mapped_column(sa.ForeignKey("resources.id"), primary_key=True)
    owner: Mapped[str] = mapped_column(sa.String(128))
    token: Mapped[UUID] = mapped_column(default=uuid4, unique=True)
    acquired_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    __table_args__ = (sa.CheckConstraint("expires_at > acquired_at"),)


class ModelInvocation(Record, Base):
    __tablename__ = "model_invocations"
    incident_id: Mapped[UUID] = mapped_column(sa.ForeignKey("incidents.id"), index=True)
    provider: Mapped[str] = mapped_column(sa.String(64))
    model: Mapped[str] = mapped_column(sa.String(128))
    prompt_version: Mapped[str] = mapped_column(sa.String(128))
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    latency_ms: Mapped[int]
    actor: Mapped[str] = mapped_column(sa.String(128))
    __table_args__ = (sa.CheckConstraint("latency_ms >= 0"),)


class AnomalyScore(Record, Base):
    __tablename__ = "anomaly_scores"
    resource_id: Mapped[UUID] = mapped_column(sa.ForeignKey("resources.id"), index=True)
    incident_id: Mapped[UUID | None] = mapped_column(sa.ForeignKey("incidents.id"), index=True)
    model_version: Mapped[str] = mapped_column(sa.String(128))
    window_start: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    score: Mapped[float]
    anomalous: Mapped[bool]
    features: Mapped[dict[str, Any]] = mapped_column(JSONB)
    actor: Mapped[str] = mapped_column(sa.String(128))
    __table_args__ = (
        sa.UniqueConstraint("resource_id", "model_version", "window_start", "window_end"),
        sa.CheckConstraint("window_end > window_start"),
    )


class OutboxEvent(Record, Base):
    __tablename__ = "outbox_events"
    event_type: Mapped[str] = mapped_column(sa.String(64))
    aggregate_type: Mapped[str] = mapped_column(sa.String(64), default="incident")
    aggregate_id: Mapped[UUID] = mapped_column()
    aggregate_version: Mapped[int] = mapped_column()
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(sa.String(32), default="PENDING")
    retry_count: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    actor: Mapped[str] = mapped_column(sa.String(128))
    __table_args__ = (
        sa.Index("ix_outbox_events_status_created", "status", "created_at"),
        sa.Index("ix_outbox_events_aggregate", "aggregate_id", "aggregate_version"),
        sa.CheckConstraint("status IN ('PENDING', 'PUBLISHED', 'FAILED')"),
    )


class WorkflowLease(Base):
    __tablename__ = "workflow_leases"
    incident_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True
    )
    owner: Mapped[str] = mapped_column(sa.String(128))
    token: Mapped[UUID] = mapped_column(unique=True, default=uuid4)
    acquired_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    incident_version: Mapped[int] = mapped_column()
    __table_args__ = (
        sa.CheckConstraint("expires_at > acquired_at", name="chk_workflow_lease_expiry"),
    )


class WorkflowSchedule(Record, Base):
    __tablename__ = "workflow_schedules"
    incident_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    incident_version: Mapped[int] = mapped_column()
    next_run_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    wait_reason: Mapped[str] = mapped_column(sa.String(64))
    deadline: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(sa.String(32), default="PENDING")
    resume_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    __table_args__ = (
        sa.Index("ix_workflow_schedules_status_next_run", "status", "next_run_at"),
        sa.CheckConstraint("status IN ('PENDING', 'PROCESSED', 'CANCELLED')"),
    )


class WorkflowCheckpoint(Base):
    __tablename__ = "workflow_checkpoints"
    thread_id: Mapped[str] = mapped_column(sa.String(128), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(sa.String(128), primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(sa.String(128), primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    type: Mapped[str] = mapped_column(sa.String(64))
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSONB)
    checkpoint_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )


class WorkflowCheckpointWrite(Base):
    __tablename__ = "workflow_checkpoint_writes"
    thread_id: Mapped[str] = mapped_column(sa.String(128), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(sa.String(128), primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(sa.String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(sa.String(128), primary_key=True)
    idx: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(sa.String(128))
    type: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(sa.String(128), primary_key=True)
    last_heartbeat: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    status: Mapped[str] = mapped_column(sa.String(32), default="HEALTHY")
    worker_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)


class AttentionItem(Record, Base):
    __tablename__ = "attention_items"
    incident_id: Mapped[UUID] = mapped_column(sa.ForeignKey("incidents.id"), index=True)
    severity: Mapped[str] = mapped_column(sa.String(32))
    reason: Mapped[str] = mapped_column(sa.String(256))
    message: Mapped[str] = mapped_column(sa.Text)
    acknowledged: Mapped[bool] = mapped_column(default=False)
    actor: Mapped[str] = mapped_column(sa.String(128))


class EscalationRecord(Record, Base):
    __tablename__ = "escalation_records"
    incident_id: Mapped[UUID] = mapped_column(sa.ForeignKey("incidents.id"), index=True)
    title: Mapped[str] = mapped_column(sa.String(256))
    summary: Mapped[str] = mapped_column(sa.Text)
    root_cause: Mapped[str] = mapped_column(sa.String(128))
    escalation_reason: Mapped[str] = mapped_column(sa.Text)
    ticket_reference: Mapped[str] = mapped_column(sa.String(128))
    actor: Mapped[str] = mapped_column(sa.String(128))


class DryRunRecord(Record, Base):
    __tablename__ = "dry_runs"
    incident_id: Mapped[UUID] = mapped_column(sa.ForeignKey("incidents.id"), index=True)
    plan_id: Mapped[UUID] = mapped_column(sa.ForeignKey("remediation_plans.id"))
    plan_version: Mapped[int] = mapped_column()
    content_hash: Mapped[str] = mapped_column(sa.String(64))
    policy_evaluation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    validation_result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    simulated_steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    actor: Mapped[str] = mapped_column(sa.String(128))
