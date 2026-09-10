from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from app.db.models import (
    AnomalyScore,
    Approval,
    AttentionItem,
    AuditEntry,
    Diagnosis,
    DryRunRecord,
    EscalationRecord,
    Event,
    EvidenceItem,
    Execution,
    Incident,
    IncidentEvent,
    ModelInvocation,
    OutboxEvent,
    Policy,
    RemediationPlan,
    RemediationStep,
    Resource,
    ResourceLock,
    User,
    VerificationResult,
    WorkerHeartbeat,
    WorkflowCheckpoint,
    WorkflowCheckpointWrite,
    WorkflowLease,
    WorkflowSchedule,
)
from app.domain.incidents.state_machine import validate_transition
from sharedmodels.enums import ExecutionStatus, IncidentState, RiskLevel
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class ConflictError(ValueError):
    """A stale version, conflicting idempotency key, or held resource lock."""


@asynccontextmanager
async def unit_of_work(
    factory: async_sessionmaker, actor: str
) -> AsyncGenerator["ControlPlaneRepository"]:
    if not actor or not actor.strip() or len(actor) > 128:
        raise ValueError("A nonempty actor (at most 128 characters) is required")
    async with factory() as session, session.begin():
        await session.execute(
            sa.text("SELECT set_config('app.actor', :actor, true)"), {"actor": actor}
        )
        yield ControlPlaneRepository(session, actor)


class ControlPlaneRepository:
    def __init__(self, session: AsyncSession, actor: str):
        self.session = session
        self.actor = actor

    async def add(self, record: Any) -> Any:
        # Specialized concurrency-sensitive writes must use the methods below.
        if type(record) not in {
            Resource,
            User,
            EvidenceItem,
            Diagnosis,
            RemediationPlan,
            RemediationStep,
            VerificationResult,
            Policy,
            Approval,
            ModelInvocation,
            AnomalyScore,
            AttentionItem,
            EscalationRecord,
            DryRunRecord,
        }:
            raise TypeError("Use the specialized repository method for this record")
        if hasattr(record, "actor"):
            record.actor = self.actor
        self.session.add(record)
        await self.session.flush()
        return record

    async def store_event(
        self,
        *,
        resource_id: UUID,
        source: Any,
        fingerprint: str,
        dedup_window: datetime,
        occurred_at: datetime,
        severity: Any,
        payload: dict,
        raw_payload: dict,
    ) -> Event:
        if len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint):
            raise ValueError("fingerprint must be a lowercase SHA-256 digest")
        values = {
            "id": uuid4(),
            "resource_id": resource_id,
            "source": source,
            "fingerprint": fingerprint,
            "dedup_window": dedup_window,
            "occurred_at": occurred_at,
            "severity": severity,
            "payload": payload,
            "raw_payload": raw_payload,
            "actor": self.actor,
        }
        query = (
            insert(Event)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_event_dedup")
            .returning(Event)
        )
        event = await self.session.scalar(query)
        if event is not None:
            return event

        # 2. Fallback: If deduplication triggered, fetch the existing record
        existing = await self.session.scalar(
            sa.select(Event).where(
                Event.source == source,
                Event.resource_id == resource_id,
                Event.fingerprint == fingerprint,
                Event.dedup_window == dedup_window,
            )
        )
        if existing is None:
            raise RuntimeError(
                f"Event with fingerprint {fingerprint} conflicted on 'uq_event_dedup' "
                "but could not be found in the database."
            )
        return existing

    async def create_incident(
        self,
        *,
        resource_id: UUID,
        correlation_key: str,
        severity: Any,
        approval_required: bool = False,
    ) -> Incident:
        incident = Incident(
            resource_id=resource_id,
            correlation_key=correlation_key,
            severity=severity,
            state=IncidentState.DETECTED,
            approval_required=approval_required,
        )
        self.session.add(incident)
        await self.session.flush()
        return incident

    async def get_resource_by_identifier(
        self,
        *,
        resource_id: UUID | None = None,
        external_id: str | None = None,
        name: str | None = None,
    ) -> Resource | None:
        if resource_id is not None:
            res = await self.session.get(Resource, resource_id)
            if res is not None:
                return res
        if external_id is not None:
            res = await self.session.scalar(
                sa.select(Resource).where(Resource.external_id == external_id)
            )
            if res is not None:
                return res
        if name is not None:
            res = await self.session.scalar(sa.select(Resource).where(Resource.name == name))
            if res is not None:
                return res
        return None

    async def get_active_incident_by_correlation_key(self, correlation_key: str) -> Incident | None:
        terminal_states = [
            IncidentState.RESOLVED,
            IncidentState.ESCALATED,
            IncidentState.ROLLEDBACK,
        ]
        stmt = sa.select(Incident).where(
            Incident.correlation_key == correlation_key,
            Incident.state.notin_(terminal_states),
        )
        return await self.session.scalar(stmt)

    async def get_or_create_incident(
        self,
        *,
        resource_id: UUID,
        correlation_key: str,
        severity: Any,
        approval_required: bool = False,
    ) -> tuple[Incident, bool]:
        active = await self.get_active_incident_by_correlation_key(correlation_key)
        if active is not None:
            return active, False

        try:
            async with self.session.begin_nested():
                incident = Incident(
                    resource_id=resource_id,
                    correlation_key=correlation_key,
                    severity=severity,
                    state=IncidentState.DETECTED,
                    approval_required=approval_required,
                )
                self.session.add(incident)
                await self.session.flush()
                return incident, True
        except IntegrityError:
            active = await self.get_active_incident_by_correlation_key(correlation_key)
            if active is not None:
                return active, False
            raise

    async def link_event(self, incident_id: UUID, event_id: UUID) -> None:
        await self.session.execute(
            insert(IncidentEvent)
            .values(incident_id=incident_id, event_id=event_id)
            .on_conflict_do_nothing()
        )

    async def transition(
        self, incident_id: UUID, expected_version: int, target: IncidentState
    ) -> Incident:
        incident = await self.session.get(Incident, incident_id, populate_existing=True)
        if incident is None:
            raise LookupError("Incident not found")
        if incident.version != expected_version:
            raise ConflictError("Stale incident version")
        validate_transition(incident.state, target)
        if incident.state == IncidentState.PLANNED and target != IncidentState.ESCALATED:
            required = (
                IncidentState.PENDING_APPROVAL
                if incident.approval_required
                else IncidentState.EXECUTING
            )
            if target != required:
                raise ValueError("Transition does not match approval requirement")
        retry = incident.state == IncidentState.VERIFYING and target == IncidentState.DIAGNOSED
        if retry and incident.attempts >= incident.retry_limit:
            raise ValueError("Retry limit exhausted")
        values = {"state": target, "version": expected_version + 1, "updated_at": sa.func.now()}
        if retry:
            values["attempts"] = incident.attempts + 1
        if target == IncidentState.RESOLVED:
            values["resolved_at"] = sa.func.now()
        result = await self.session.execute(
            sa.update(Incident)
            .where(Incident.id == incident_id, Incident.version == expected_version)
            .values(**values)
            .returning(Incident)
            .execution_options(populate_existing=True)
        )
        updated = result.scalar_one_or_none()
        if updated is None:
            raise ConflictError("Concurrent incident transition")
        if target == IncidentState.APPROVED:
            outbox_ev = OutboxEvent(
                id=uuid4(),
                event_type="incident.approved",
                aggregate_type="incident",
                aggregate_id=incident_id,
                aggregate_version=expected_version + 1,
                payload={
                    "incident_id": str(incident_id),
                    "state": IncidentState.APPROVED.value,
                    "version": expected_version + 1,
                    "severity": updated.severity.value
                    if hasattr(updated.severity, "value")
                    else str(updated.severity),
                    "correlation_key": updated.correlation_key,
                    "resource_id": str(updated.resource_id),
                },
                status="PENDING",
                actor=self.actor,
            )
            self.session.add(outbox_ev)
            await self.session.flush()
        return updated

    async def record_execution(
        self,
        *,
        incident_id: UUID,
        step_id: UUID,
        resource_id: UUID,
        idempotency_key: str,
        pre_state: dict,
    ) -> Execution:
        step = await self.session.get(RemediationStep, step_id)
        plan = await self.session.get(RemediationPlan, step.plan_id) if step else None
        if (
            not step
            or not plan
            or plan.incident_id != incident_id
            or step.resource_id != resource_id
        ):
            raise ValueError("Execution does not match its plan, incident and resource")

        # 1. Insert and return the ORM entity directly
        query = (
            insert(Execution)
            .values(
                id=uuid4(),
                incident_id=incident_id,
                step_id=step_id,
                resource_id=resource_id,
                idempotency_key=idempotency_key,
                actor=self.actor,
                pre_state=pre_state,
                status=ExecutionStatus.PENDING,
                version=1,
                result={},
            )
            .on_conflict_do_nothing(index_elements=[Execution.idempotency_key])
            .returning(Execution)
        )
        execution = await self.session.scalar(query)

        # 2. Fallback: If deduplicated, fetch the existing execution record
        if execution is None:
            execution = await self.session.scalar(
                sa.select(Execution).where(Execution.idempotency_key == idempotency_key)
            )

        # 3. Guard against None to narrow the type and catch race conditions
        if execution is None:
            raise RuntimeError(
                f"Execution with idempotency key '{idempotency_key}' conflicted "
                "on insert but could not be located in the database."
            )

        # 4. Enforce idempotency parameter matching
        if (execution.incident_id, execution.step_id, execution.resource_id) != (
            incident_id,
            step_id,
            resource_id,
        ):
            raise ConflictError("Idempotency key already belongs to a different execution")
        return execution

    async def finish_execution(
        self,
        execution_id: UUID,
        expected_version: int,
        status: ExecutionStatus,
        result: dict,
    ) -> Execution:
        execution = await self.session.get(Execution, execution_id, populate_existing=True)
        allowed = {
            ExecutionStatus.PENDING: {ExecutionStatus.RUNNING},
            ExecutionStatus.RUNNING: {ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED},
            ExecutionStatus.FAILED: {ExecutionStatus.ROLLEDBACK},
        }
        if not execution or execution.version != expected_version:
            raise ConflictError("Missing execution or stale version")
        if status not in allowed.get(execution.status, set()):
            raise ValueError("Illegal execution transition")

        # Explicitly annotate dict[str, Any] to permit SQL expressions like sa.func.now()
        values: dict[str, Any] = {
            "status": status,
            "result": result,
            "version": expected_version + 1,
        }

        if status == ExecutionStatus.RUNNING:
            values["started_at"] = sa.func.now()
        else:
            values["finished_at"] = sa.func.now()

        updated = await self.session.scalar(
            sa.update(Execution)
            .where(Execution.id == execution_id, Execution.version == expected_version)
            .values(**values)
            .returning(Execution)
            .execution_options(populate_existing=True)
        )
        if updated is None:
            raise ConflictError("Concurrent execution transition")
        return updated

    async def acquire_lock(self, resource_id: UUID, seconds: int = 120) -> UUID:
        if not 1 <= seconds <= 3600:
            raise ValueError("Lock duration must be between 1 and 3600 seconds")
        token = uuid4()
        stmt = insert(ResourceLock).values(
            resource_id=resource_id,
            owner=self.actor,
            token=token,
            acquired_at=sa.func.now(),
            expires_at=sa.func.now() + timedelta(seconds=seconds),
        )
        result = await self.session.scalar(
            stmt.on_conflict_do_update(
                index_elements=[ResourceLock.resource_id],
                set_={
                    "owner": self.actor,
                    "token": token,
                    "acquired_at": sa.func.now(),
                    "expires_at": stmt.excluded.expires_at,
                },
                where=ResourceLock.expires_at <= sa.func.now(),
            ).returning(ResourceLock.token)
        )
        if result is None:
            raise ConflictError("Resource is locked")
        return result

    async def release_lock(self, resource_id: UUID, token: UUID) -> None:
        deleted = await self.session.scalar(
            sa.delete(ResourceLock)
            .where(
                ResourceLock.resource_id == resource_id,
                ResourceLock.token == token,
                ResourceLock.owner == self.actor,
            )
            .returning(ResourceLock.resource_id)
        )
        if deleted is None:
            raise ConflictError("Lock token or owner does not match")

    async def reconstruct(self, incident_id: UUID) -> dict[str, Any]:
        incident = await self.session.get(Incident, incident_id)
        if not incident:
            raise LookupError("Incident not found")

        async def rows(model: type, predicate: Any) -> list:
            return list((await self.session.scalars(sa.select(model).where(predicate))).all())

        plans = await rows(RemediationPlan, RemediationPlan.incident_id == incident_id)
        executions = await rows(Execution, Execution.incident_id == incident_id)
        plan_ids = [p.id for p in plans]
        execution_ids = [e.id for e in executions]
        timeline = list(
            (
                await self.session.scalars(
                    sa.select(AuditEntry)
                    .where(AuditEntry.incident_id == incident_id)
                    .order_by(AuditEntry.sequence)
                )
            ).all()
        )
        return {
            "incident": incident,
            "events": list(
                (
                    await self.session.scalars(
                        sa.select(Event)
                        .join(IncidentEvent)
                        .where(IncidentEvent.incident_id == incident_id)
                    )
                ).all()
            ),
            "evidence": await rows(EvidenceItem, EvidenceItem.incident_id == incident_id),
            "diagnoses": await rows(Diagnosis, Diagnosis.incident_id == incident_id),
            "plans": plans,
            "steps": await rows(RemediationStep, RemediationStep.plan_id.in_(plan_ids)),
            "approvals": await rows(Approval, Approval.plan_id.in_(plan_ids)),
            "executions": executions,
            "verifications": await rows(
                VerificationResult, VerificationResult.execution_id.in_(execution_ids)
            ),
            "model_invocations": await rows(
                ModelInvocation, ModelInvocation.incident_id == incident_id
            ),
            "anomaly_scores": await rows(AnomalyScore, AnomalyScore.incident_id == incident_id),
            "timeline": timeline,
        }

    async def add_outbox_event(
        self,
        *,
        event_type: str,
        aggregate_type: str = "incident",
        aggregate_id: UUID,
        aggregate_version: int,
        payload: dict[str, Any],
    ) -> OutboxEvent:
        event = OutboxEvent(
            id=uuid4(),
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            payload=payload,
            status="PENDING",
            retry_count=0,
            actor=self.actor,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def fetch_pending_outbox_events(
        self, limit: int = 50, max_age_seconds: int = 300
    ) -> list[OutboxEvent]:
        stmt = (
            sa.select(OutboxEvent)
            .where(OutboxEvent.status == "PENDING")
            .order_by(OutboxEvent.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def mark_outbox_published(self, event_id: UUID) -> None:
        await self.session.execute(
            sa.update(OutboxEvent)
            .where(OutboxEvent.id == event_id)
            .values(status="PUBLISHED", published_at=sa.func.now())
        )

    async def mark_outbox_failed(self, event_id: UUID, error: str) -> None:
        await self.session.execute(
            sa.update(OutboxEvent)
            .where(OutboxEvent.id == event_id)
            .values(
                status="FAILED",
                last_error=error,
                retry_count=OutboxEvent.retry_count + 1,
            )
        )

    async def acquire_incident_lease(
        self,
        incident_id: UUID,
        owner: str,
        ttl_seconds: int = 30,
        version: int | None = None,
    ) -> UUID:
        token = uuid4()
        incident = await self.session.get(Incident, incident_id)
        if incident is None:
            raise LookupError("Incident not found")
        if version is not None and incident.version != version:
            raise ConflictError(
                f"Incident version mismatch: expected {version}, found {incident.version}"
            )
        current_version = incident.version

        stmt = insert(WorkflowLease).values(
            incident_id=incident_id,
            owner=owner,
            token=token,
            acquired_at=sa.func.now(),
            expires_at=sa.func.now() + timedelta(seconds=ttl_seconds),
            incident_version=current_version,
        )
        result = await self.session.scalar(
            stmt.on_conflict_do_update(
                index_elements=[WorkflowLease.incident_id],
                set_={
                    "owner": owner,
                    "token": token,
                    "acquired_at": sa.func.now(),
                    "expires_at": stmt.excluded.expires_at,
                    "incident_version": current_version,
                },
                where=(WorkflowLease.expires_at <= sa.func.now()) | (WorkflowLease.owner == owner),
            ).returning(WorkflowLease.token)
        )
        if result is None:
            raise ConflictError("Incident is currently leased by another worker")
        return result

    async def verify_incident_lease(
        self, incident_id: UUID, token: UUID, expected_version: int | None = None
    ) -> bool:
        lease = await self.session.get(WorkflowLease, incident_id)
        if lease is None or lease.token != token:
            return False
        now_dt = (
            datetime.now(lease.expires_at.tzinfo) if lease.expires_at.tzinfo else datetime.now()
        )
        if lease.expires_at <= now_dt:
            return False
        if expected_version is not None:
            incident = await self.session.get(Incident, incident_id)
            if incident is None or incident.version != expected_version:
                return False
        return True

    async def renew_incident_lease(
        self, incident_id: UUID, token: UUID, ttl_seconds: int = 30
    ) -> bool:
        result = await self.session.scalar(
            sa.update(WorkflowLease)
            .where(
                WorkflowLease.incident_id == incident_id,
                WorkflowLease.token == token,
                WorkflowLease.expires_at > sa.func.now(),
            )
            .values(expires_at=sa.func.now() + timedelta(seconds=ttl_seconds))
            .returning(WorkflowLease.incident_id)
        )
        return result is not None

    async def release_incident_lease(self, incident_id: UUID, token: UUID) -> None:
        await self.session.execute(
            sa.delete(WorkflowLease).where(
                WorkflowLease.incident_id == incident_id,
                WorkflowLease.token == token,
            )
        )

    async def create_workflow_schedule(
        self,
        *,
        incident_id: UUID,
        incident_version: int,
        next_run_at: datetime,
        wait_reason: str,
        deadline: datetime | None = None,
        resume_metadata: dict[str, Any] | None = None,
    ) -> WorkflowSchedule:
        schedule = WorkflowSchedule(
            id=uuid4(),
            incident_id=incident_id,
            incident_version=incident_version,
            next_run_at=next_run_at,
            wait_reason=wait_reason,
            deadline=deadline,
            status="PENDING",
            resume_metadata=resume_metadata or {},
        )
        self.session.add(schedule)
        await self.session.flush()
        return schedule

    async def fetch_due_workflow_schedules(self, limit: int = 50) -> list[WorkflowSchedule]:
        stmt = (
            sa.select(WorkflowSchedule)
            .where(
                WorkflowSchedule.status == "PENDING",
                WorkflowSchedule.next_run_at <= sa.func.now(),
            )
            .order_by(WorkflowSchedule.next_run_at.asc())
            .limit(limit)
        )
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def mark_schedule_processed_atomic(self, schedule_id: UUID) -> bool:
        result = await self.session.scalar(
            sa.update(WorkflowSchedule)
            .where(
                WorkflowSchedule.id == schedule_id,
                WorkflowSchedule.status == "PENDING",
            )
            .values(status="PROCESSED")
            .returning(WorkflowSchedule.id)
        )
        return result is not None

    async def cancel_pending_schedules(self, incident_id: UUID) -> None:
        await self.session.execute(
            sa.update(WorkflowSchedule)
            .where(
                WorkflowSchedule.incident_id == incident_id,
                WorkflowSchedule.status == "PENDING",
            )
            .values(status="CANCELLED")
        )

    async def record_worker_heartbeat(
        self, worker_id: str, status: str = "HEALTHY", metadata: dict[str, Any] | None = None
    ) -> None:
        table = WorkerHeartbeat.__table__
        stmt = insert(table).values(
            worker_id=worker_id,
            last_heartbeat=sa.func.now(),
            status=status,
            metadata=metadata or {},
        )
        await self.session.execute(
            stmt.on_conflict_do_update(
                index_elements=[table.c.worker_id],
                set_={
                    "last_heartbeat": sa.func.now(),
                    "status": status,
                    "metadata": metadata or {},
                },
            )
        )

    async def get_system_status_metrics(self) -> dict[str, Any]:
        heartbeats = list((await self.session.scalars(sa.select(WorkerHeartbeat))).all())
        outbox_backlog = (
            await self.session.scalar(
                sa.select(sa.func.count(OutboxEvent.id)).where(OutboxEvent.status == "PENDING")
            )
            or 0
        )
        oldest_wakeup = await self.session.scalar(
            sa.select(sa.func.min(WorkflowSchedule.next_run_at)).where(
                WorkflowSchedule.status == "PENDING"
            )
        )
        last_error_event = await self.session.scalar(
            sa.select(OutboxEvent)
            .where(OutboxEvent.status == "FAILED")
            .order_by(OutboxEvent.created_at.desc())
            .limit(1)
        )
        return {
            "worker_heartbeats": [
                {
                    "worker_id": h.worker_id,
                    "last_heartbeat": h.last_heartbeat.isoformat() if h.last_heartbeat else None,
                    "status": h.status,
                    "metadata": h.worker_metadata,
                }
                for h in heartbeats
            ],
            "outbox_backlog": outbox_backlog,
            "oldest_pending_wakeup": oldest_wakeup.isoformat() if oldest_wakeup else None,
            "last_processing_error": last_error_event.last_error if last_error_event else None,
        }

    async def create_remediation_plan_with_steps(
        self,
        *,
        incident_id: UUID,
        diagnosis_id: UUID,
        version: int = 1,
        risk: Any = RiskLevel.LOW,
        content_hash: str,
        container_id: str | None = None,
        binding_generation: int | None = None,
        verification_profile: str | None = "m1_default_restart_profile",
        steps_data: list[dict[str, Any]],
    ) -> RemediationPlan:
        diag = await self.session.get(Diagnosis, diagnosis_id)
        if not diag or diag.incident_id != incident_id:
            raise ValueError("Diagnosis does not belong to this incident")

        plan = RemediationPlan(
            id=uuid4(),
            incident_id=incident_id,
            diagnosis_id=diagnosis_id,
            version=version,
            risk=risk,
            approved=False,
            actor=self.actor,
            content_hash=content_hash,
            container_id=container_id,
            binding_generation=binding_generation,
            verification_profile=verification_profile,
        )
        self.session.add(plan)
        await self.session.flush()

        for step_data in steps_data:
            step = RemediationStep(
                id=step_data.get("id", uuid4()),
                plan_id=plan.id,
                resource_id=step_data["resource_id"],
                position=step_data["position"],
                action=step_data["action"],
                action_schema_version=step_data.get("action_schema_version", "1.0"),
                parameters=step_data.get("parameters", {}),
                verification=step_data.get("verification", {}),
            )
            self.session.add(step)

        await self.session.flush()
        return plan

    async def create_dry_run_record(
        self,
        *,
        incident_id: UUID,
        plan_id: UUID,
        plan_version: int,
        content_hash: str,
        policy_evaluation: dict[str, Any],
        validation_result: dict[str, Any],
        simulated_steps: list[dict[str, Any]],
    ) -> DryRunRecord:
        record = DryRunRecord(
            id=uuid4(),
            incident_id=incident_id,
            plan_id=plan_id,
            plan_version=plan_version,
            content_hash=content_hash,
            policy_evaluation=policy_evaluation,
            validation_result=validation_result,
            simulated_steps=simulated_steps,
            actor=self.actor,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def create_attention_item(
        self,
        *,
        incident_id: UUID,
        severity: str,
        reason: str,
        message: str,
    ) -> AttentionItem:
        item = AttentionItem(
            id=uuid4(),
            incident_id=incident_id,
            severity=severity,
            reason=reason,
            message=message,
            acknowledged=False,
            actor=self.actor,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def create_escalation_record(
        self,
        *,
        incident_id: UUID,
        title: str,
        summary: str,
        root_cause: str,
        escalation_reason: str,
        ticket_reference: str,
    ) -> EscalationRecord:
        rec = EscalationRecord(
            id=uuid4(),
            incident_id=incident_id,
            title=title,
            summary=summary,
            root_cause=root_cause,
            escalation_reason=escalation_reason,
            ticket_reference=ticket_reference,
            actor=self.actor,
        )
        self.session.add(rec)
        await self.session.flush()
        return rec

    async def get_plan_with_steps(
        self, plan_id: UUID
    ) -> tuple[RemediationPlan | None, list[RemediationStep]]:
        plan = await self.session.get(RemediationPlan, plan_id)
        if not plan:
            return None, []
        steps = list(
            (
                await self.session.scalars(
                    sa.select(RemediationStep)
                    .where(RemediationStep.plan_id == plan_id)
                    .order_by(RemediationStep.position.asc())
                )
            ).all()
        )
        return plan, steps
