from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from app.db.models import (
    AnomalyScore,
    Approval,
    AuditEntry,
    Diagnosis,
    Event,
    EvidenceItem,
    Execution,
    Incident,
    IncidentEvent,
    ModelInvocation,
    Policy,
    RemediationPlan,
    RemediationStep,
    Resource,
    ResourceLock,
    User,
    VerificationResult,
)
from app.domain.incidents.state_machine import validate_transition
from sharedmodels.enums import ExecutionStatus, IncidentState
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
        if incident.state == IncidentState.PLANNED:
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
