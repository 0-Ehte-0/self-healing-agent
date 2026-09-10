"""Real PostgreSQL checks. Each run uses a fresh, uniquely named disposable database."""

import asyncio
import hashlib
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
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
    ModelInvocation,
    Policy,
    RemediationPlan,
    RemediationStep,
    Resource,
    User,
    VerificationResult,
)
from app.db.repositories.control_plane import ConflictError, unit_of_work
from app.db.seed.__main__ import seed
from sharedmodels.enums import EventSource, ExecutionStatus, RiskLevel, Severity, UserRole
from sharedmodels.enums import IncidentState as S
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def database_url():
    raw = os.getenv("TEST_DATABASE_URL")
    if not raw:
        pytest.skip(
            "Set TEST_DATABASE_URL to a PostgreSQL migration-owner URL for live database tests"
        )
    base = sa.make_url(raw)
    name = "phase4_test_" + uuid4().hex
    url = base.set(database=name)

    async def manage(create):
        conn = await asyncpg.connect(
            base.set(drivername="postgresql").render_as_string(hide_password=False)
        )
        try:
            if create:
                await conn.execute(f'CREATE DATABASE "{name}"')
            else:
                await conn.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        finally:
            await conn.close()

    asyncio.run(manage(True))
    env = {**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False)}

    def migrate(revision):
        subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "upgrade" if revision == "head" else "downgrade",
                revision,
            ],
            cwd=ROOT / "apps/control-api",
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

    try:
        migrate("head")
        migrate("base")
        migrate("head")
        yield url.render_as_string(hide_password=False)
    finally:
        asyncio.run(manage(False))


@pytest_asyncio.fixture
async def factory(database_url):
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def incident_fixture(factory, approval=False):
    async with unit_of_work(factory, "test:setup") as repo:
        resource = await repo.add(
            Resource(
                provider="docker",
                external_id=uuid4().hex,
                name="disposable",
                labels={},
                managed=True,
            )
        )
        incident = await repo.create_incident(
            resource_id=resource.id,
            correlation_key=uuid4().hex,
            severity=Severity.HIGH,
            approval_required=approval,
        )
        return resource.id, incident.id


@pytest.mark.asyncio
async def test_full_lifecycle_reconstruction(factory):
    resource_id, incident_id = await incident_fixture(factory)
    now = datetime.now(UTC)
    async with unit_of_work(factory, "test:operator") as repo:
        event = await repo.store_event(
            resource_id=resource_id,
            source=EventSource.ALERTMANAGER,
            fingerprint=hashlib.sha256(b"crash").hexdigest(),
            dedup_window=now,
            occurred_at=now,
            severity=Severity.HIGH,
            payload={"alert": "ContainerDown"},
            raw_payload={"status": "firing"},
        )
        await repo.link_event(incident_id, event.id)
        evidence = await repo.add(
            EvidenceItem(
                incident_id=incident_id,
                kind="metrics",
                source="prometheus",
                observed_at=now,
                content={"up": 0},
                sha256=hashlib.sha256(b"up=0").hexdigest(),
            )
        )
        diagnosis = await repo.add(
            Diagnosis(
                incident_id=incident_id,
                root_cause="container_crash",
                confidence=1,
                evidence_ids=[str(evidence.id)],
                reasoning={"rule": "down"},
            )
        )
        plan = await repo.add(
            RemediationPlan(
                incident_id=incident_id, diagnosis_id=diagnosis.id, version=1, risk=RiskLevel.LOW
            )
        )
        step = await repo.add(
            RemediationStep(
                plan_id=plan.id,
                resource_id=resource_id,
                position=0,
                action="restart_container",
                parameters={},
                verification={"window_seconds": 90},
            )
        )
        for version, state in enumerate([S.TRIAGED, S.DIAGNOSED, S.PLANNED, S.EXECUTING], start=1):
            await repo.transition(incident_id, version, state)
        execution = await repo.record_execution(
            incident_id=incident_id,
            step_id=step.id,
            resource_id=resource_id,
            idempotency_key=uuid4().hex,
            pre_state={"status": "exited"},
        )
        await repo.finish_execution(execution.id, 1, ExecutionStatus.RUNNING, {})
        await repo.finish_execution(
            execution.id, 2, ExecutionStatus.SUCCEEDED, {"status": "running"}
        )
        await repo.transition(incident_id, 5, S.VERIFYING)
        await repo.add(
            VerificationResult(
                execution_id=execution.id,
                passed=True,
                window_start=now,
                window_end=now + timedelta(seconds=90),
                health_score=1,
                checks={"ready": True},
            )
        )
        await repo.add(
            ModelInvocation(
                incident_id=incident_id,
                provider="null",
                model="deterministic",
                prompt_version="none",
                request={},
                response={},
                latency_ms=0,
            )
        )
        await repo.add(
            AnomalyScore(
                resource_id=resource_id,
                incident_id=incident_id,
                model_version="fixture",
                window_start=now,
                window_end=now + timedelta(seconds=60),
                score=-0.2,
                anomalous=True,
                features={},
            )
        )
        await repo.transition(incident_id, 6, S.RESOLVED)
    # Reconstruct using an entirely new connection after commit.
    async with unit_of_work(factory, "test:reader") as repo:
        result = await repo.reconstruct(incident_id)
        assert result["incident"].state == S.RESOLVED
        assert result["incident"].resolved_at is not None
        for key in [
            "events",
            "evidence",
            "diagnoses",
            "plans",
            "steps",
            "executions",
            "verifications",
            "model_invocations",
            "anomaly_scores",
        ]:
            assert len(result[key]) == 1
        states = [
            a.details["after"]["state"] for a in result["timeline"] if a.entity_type == "incidents"
        ]
        assert states == [
            "DETECTED",
            "TRIAGED",
            "DIAGNOSED",
            "PLANNED",
            "EXECUTING",
            "VERIFYING",
            "RESOLVED",
        ]
        assert all(a.actor and a.created_at for a in result["timeline"])


@pytest.mark.asyncio
async def test_illegal_transition_and_rollback(factory):
    _, incident_id = await incident_fixture(factory)
    with pytest.raises(ValueError, match="Illegal"):
        async with unit_of_work(factory, "test:illegal") as repo:
            await repo.transition(incident_id, 1, S.RESOLVED)
    # pyrefly: ignore [implicit-import]
    with pytest.raises(sa.exc.DBAPIError, match="Illegal"):
        async with unit_of_work(factory, "test:sql-bypass") as repo:
            await repo.session.execute(
                sa.update(Incident)
                .where(Incident.id == incident_id)
                .values(state=S.RESOLVED, version=2)
            )
    with pytest.raises(RuntimeError):
        async with unit_of_work(factory, "test:rollback") as repo:
            await repo.transition(incident_id, 1, S.TRIAGED)
            raise RuntimeError("abort")
    async with unit_of_work(factory, "test:read") as repo:
        result = await repo.reconstruct(incident_id)
        assert result["incident"].state == S.DETECTED
        assert len(result["timeline"]) == 1


@pytest.mark.asyncio
async def test_concurrent_events_and_versions(factory):
    resource_id, incident_id = await incident_fixture(factory)
    now = datetime.now(UTC)

    async def insert_duplicate():
        async with unit_of_work(factory, "test:ingestion") as repo:
            event = await repo.store_event(
                resource_id=resource_id,
                source=EventSource.GENERIC,
                fingerprint="a" * 64,
                dedup_window=now,
                occurred_at=now,
                severity=Severity.HIGH,
                payload={},
                raw_payload={},
            )
            return event.id

    assert len(set(await asyncio.gather(*(insert_duplicate() for _ in range(12))))) == 1

    async def transition():
        async with unit_of_work(factory, "test:race") as repo:
            return await repo.transition(incident_id, 1, S.TRIAGED)

    outcomes = await asyncio.gather(transition(), transition(), return_exceptions=True)
    assert sum(isinstance(result, ConflictError) for result in outcomes) == 1


@pytest.mark.asyncio
async def test_execution_idempotency_and_lock_ownership(factory):
    resource_id, incident_id = await incident_fixture(factory)
    async with unit_of_work(factory, "test:setup") as repo:
        diagnosis = await repo.add(
            Diagnosis(
                incident_id=incident_id,
                root_cause="crash",
                confidence=1,
                evidence_ids=[],
                reasoning={},
            )
        )
        plan = await repo.add(
            RemediationPlan(
                incident_id=incident_id, diagnosis_id=diagnosis.id, version=1, risk=RiskLevel.LOW
            )
        )
        step = await repo.add(
            RemediationStep(
                plan_id=plan.id,
                resource_id=resource_id,
                position=0,
                action="restart_container",
                parameters={},
                verification={},
            )
        )
        step_id = step.id
    key = uuid4().hex

    async def record():
        async with unit_of_work(factory, "test:executor") as repo:
            return (
                await repo.record_execution(
                    incident_id=incident_id,
                    resource_id=resource_id,
                    step_id=step_id,
                    idempotency_key=key,
                    pre_state={},
                )
            ).id

    assert len(set(await asyncio.gather(*(record() for _ in range(8))))) == 1
    async with unit_of_work(factory, "test:owner") as repo:
        token = await repo.acquire_lock(resource_id)
    with pytest.raises(ConflictError):
        async with unit_of_work(factory, "test:other") as repo:
            await repo.acquire_lock(resource_id)
    with pytest.raises(ConflictError):
        async with unit_of_work(factory, "test:other") as repo:
            await repo.release_lock(resource_id, token)
    async with unit_of_work(factory, "test:owner") as repo:
        await repo.release_lock(resource_id, token)
    async with unit_of_work(factory, "test:read") as repo:
        audit = await repo.session.scalar(
            sa.select(AuditEntry).where(
                AuditEntry.entity_type == "resource_locks",
                AuditEntry.entity_id == resource_id,
                AuditEntry.operation == "DELETE",
            )
        )
        # pyrefly: ignore [missing-attribute]
        assert audit.details["before"]["owner"] == "test:owner"
        # pyrefly: ignore [missing-attribute]
        assert audit.details["after"] is None


@pytest.mark.asyncio
async def test_audit_immutable_as_app_role_and_owner(factory):
    _, incident_id = await incident_fixture(factory)
    for role in ["control_app", None]:
        for operation in [
            "UPDATE audit_entries SET actor='forged'",
            "DELETE FROM audit_entries",
            "TRUNCATE audit_entries",
        ]:
            # pyrefly: ignore [implicit-import]
            with pytest.raises(sa.exc.DBAPIError):
                async with unit_of_work(factory, "test:tamper") as repo:
                    if role:
                        await repo.session.execute(sa.text("SET LOCAL ROLE control_app"))
                    await repo.session.execute(sa.text(operation))
    async with unit_of_work(factory, "test:runtime") as repo:
        await repo.session.execute(sa.text("SET LOCAL ROLE control_app"))
        await repo.transition(incident_id, 1, S.TRIAGED)
        assert (
            await repo.session.scalar(
                sa.select(sa.func.count())
                .select_from(AuditEntry)
                .where(AuditEntry.incident_id == incident_id)
            )
            == 2
        )


@pytest.mark.asyncio
async def test_seed_idempotency_and_approval_record_constraints(factory):
    await seed(factory)
    await seed(factory)
    async with unit_of_work(factory, "test:read") as repo:
        users = list(await repo.session.scalars(sa.select(User)))
        assert {u.role for u in users} == {
            UserRole.ADMIN,
            UserRole.APPROVER,
            UserRole.OPERATOR,
            UserRole.VIEWER,
        }
        assert all(u.password_hash.startswith("scrypt$") for u in users)
        assert await repo.session.scalar(sa.select(sa.func.count()).select_from(Policy)) == 10
        audits = list(
            await repo.session.scalars(
                sa.select(AuditEntry).where(AuditEntry.entity_type == "users")
            )
        )
        assert all("password_hash" not in a.details["after"] for a in audits)


@pytest.mark.asyncio
async def test_approval_routing_and_retry_limit(factory):
    _, incident_id = await incident_fixture(factory, approval=True)
    async with unit_of_work(factory, "test:workflow") as repo:
        for version, state in enumerate([S.TRIAGED, S.DIAGNOSED, S.PLANNED], start=1):
            await repo.transition(incident_id, version, state)
        with pytest.raises(ValueError, match="approval"):
            await repo.transition(incident_id, 4, S.EXECUTING)
        for version, state in enumerate(
            [S.PENDING_APPROVAL, S.APPROVED, S.EXECUTING, S.VERIFYING], start=4
        ):
            await repo.transition(incident_id, version, state)
        version = 8
        for _ in range(2):
            for state in [
                S.DIAGNOSED,
                S.PLANNED,
                S.PENDING_APPROVAL,
                S.APPROVED,
                S.EXECUTING,
                S.VERIFYING,
            ]:
                await repo.transition(incident_id, version, state)
                version += 1
        with pytest.raises(ValueError, match="Retry"):
            await repo.transition(incident_id, version, S.DIAGNOSED)
        await repo.transition(incident_id, version, S.ESCALATED)


@pytest.mark.asyncio
async def test_plan_version_approver_role_and_immutability(factory):
    resource_id, incident_id = await incident_fixture(factory)
    now = datetime.now(UTC)
    async with unit_of_work(factory, "test:setup") as repo:
        approver = await repo.add(
            User(username=uuid4().hex, role=UserRole.APPROVER, password_hash="test-only")
        )
        viewer = await repo.add(
            User(username=uuid4().hex, role=UserRole.VIEWER, password_hash="test-only")
        )
        diagnosis = await repo.add(
            Diagnosis(
                incident_id=incident_id,
                root_cause="crash",
                confidence=1,
                evidence_ids=[],
                reasoning={},
            )
        )
        plan = await repo.add(
            RemediationPlan(
                incident_id=incident_id, diagnosis_id=diagnosis.id, version=1, risk=RiskLevel.LOW
            )
        )
        step = await repo.add(
            RemediationStep(
                plan_id=plan.id,
                resource_id=resource_id,
                position=0,
                action="restart_container",
                parameters={},
                verification={},
            )
        )
        plan_id, step_id, approver_id, viewer_id = plan.id, step.id, approver.id, viewer.id
    for overrides in [
        {"plan_version": 2},
        {"approver_id": viewer_id},
        {"decision": "REJECT"},
        {"expires_at": now - timedelta(seconds=1)},
        {"expires_at": now + timedelta(hours=1)},
    ]:
        values = {
            "plan_id": plan_id,
            "plan_version": 1,
            "approver_id": approver_id,
            "decision": "APPROVE",
            "expires_at": now + timedelta(minutes=29),
        }
        values.update(overrides)
        # pyrefly: ignore [implicit-import]
        with pytest.raises(sa.exc.DBAPIError):
            async with unit_of_work(factory, "test:approval") as repo:
                await repo.add(Approval(**values))
    async with unit_of_work(factory, "test:approval") as repo:
        approval = await repo.add(
            Approval(
                plan_id=plan_id,
                plan_version=1,
                approver_id=approver_id,
                decision="APPROVE",
                expires_at=now + timedelta(minutes=29),
            )
        )
        approval_id = approval.id
        await repo.session.execute(
            sa.update(RemediationPlan).where(RemediationPlan.id == plan_id).values(approved=True)
        )
    for mutation in [
        sa.update(RemediationPlan).where(RemediationPlan.id == plan_id).values(risk=RiskLevel.HIGH),
        sa.update(RemediationStep)
        .where(RemediationStep.id == step_id)
        .values(parameters={"forged": True}),
        sa.delete(Approval).where(Approval.id == approval_id),
    ]:
        # pyrefly: ignore [implicit-import]
        with pytest.raises(sa.exc.DBAPIError, match="immutable"):
            async with unit_of_work(factory, "test:tamper") as repo:
                await repo.session.execute(mutation)
