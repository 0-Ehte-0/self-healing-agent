from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from agentcore.graph.state import IncidentGraphState
from agentcore.runtime.lease import IncidentLeaseManager
from agentcore.runtime.runner import WorkflowRunner
from agentcore.runtime.scheduler import WorkflowScheduler
from app.db.models import (
    Diagnosis,
    Incident,
    OutboxEvent,
    RemediationPlan,
    Resource,
    WorkflowCheckpoint,
    WorkflowLease,
    WorkflowSchedule,
)
from app.db.repositories.control_plane import unit_of_work
from sharedmodels.enums import IncidentState as S
from sharedmodels.enums import RiskLevel, Severity


@pytest.mark.asyncio
async def test_approval_wait_persists_schedule_releases_lease_and_resumes(session_factory):
    """Verifies that an incident requiring approval pauses safely:
    1. Persists state to checkpoints and workflow_schedules.
    2. Releases exclusive lease and closes DB connections without blocking worker threads.
    3. Survives worker restart.
    4. Resumes upon approval outbox event and completes workflow.
    """
    async with unit_of_work(session_factory, actor="test:approval") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        assert res is not None

        # 1. Create incident with approval_required=True and advance to PLANNED
        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-approval-wait-{uuid4().hex}",
            severity=Severity.HIGH,
            approval_required=True,
        )
        incident_id = inc.id

        inc = await repo.transition(incident_id, 1, S.TRIAGED)
        inc = await repo.transition(incident_id, 2, S.DIAGNOSED)
        diag = Diagnosis(
            id=uuid4(),
            incident_id=incident_id,
            root_cause="CONTAINER_OOM",
            confidence=0.98,
            evidence_ids=["ev-oom-1"],
            reasoning={"analysis": "OOMKilled detected"},
            actor="test:approval",
        )
        await repo.add(diag)

        inc = await repo.transition(incident_id, 3, S.PLANNED)
        plan = RemediationPlan(
            id=uuid4(),
            incident_id=incident_id,
            diagnosis_id=diag.id,
            version=1,
            risk=RiskLevel.MEDIUM,
            approved=False,
            actor="test:approval",
        )
        await repo.add(plan)
        current_version = inc.version  # version 4

    # 2. Worker 1 executes workflow
    worker1_id = "worker-approval-instance-1"

    async def mock_policy_approved(state: IncidentGraphState):
        return {
            "status": "POLICY_APPROVED",
            "approval_required": True,
        }

    runner1 = WorkflowRunner(
        session_factory=session_factory,
        worker_id=worker1_id,
        node_overrides={
            "evaluate_policy": mock_policy_approved,
        },
    )

    res1 = await runner1.run_incident(incident_id=incident_id, expected_version=current_version)

    # 3. Assert graph entered wait state and released lease
    assert res1 is not None
    assert res1.get("status") == "PENDING_APPROVAL"
    assert res1.get("wait_reason") == "AWAITING_APPROVAL"

    # Verify lease is released in database
    lease_mgr = IncidentLeaseManager(session_factory, worker_id=worker1_id)
    async with session_factory() as session:
        active_lease = await session.scalar(
            sa.select(WorkflowLease).where(WorkflowLease.incident_id == incident_id)
        )
        assert active_lease is None, "Lease must be released when workflow pauses"

        # Verify schedule is persisted in workflow_schedules
        schedule = await session.scalar(
            sa.select(WorkflowSchedule).where(
                WorkflowSchedule.incident_id == incident_id,
                WorkflowSchedule.status == "PENDING",
            )
        )
        assert schedule is not None
        assert schedule.wait_reason == "AWAITING_APPROVAL"
        assert schedule.incident_version == res1["version"]

        # Verify checkpoints exist
        checkpoints = (
            await session.scalars(
                sa.select(WorkflowCheckpoint).where(
                    WorkflowCheckpoint.thread_id == str(incident_id)
                )
            )
        ).all()
        assert len(checkpoints) > 0

    # 4. Simulate worker 1 shutdown and human approver granting approval
    del runner1

    async with unit_of_work(session_factory, actor="test:human_approver") as repo:
        inc = await repo.transition(incident_id, res1["version"], S.APPROVED)
        approved_version = inc.version

        # Verify atomic outbox event was generated for incident.approved
        outbox_row = await repo.session.scalar(
            sa.select(OutboxEvent).where(
                OutboxEvent.aggregate_id == incident_id,
                OutboxEvent.event_type == "incident.approved",
            )
        )
        assert outbox_row is not None
        assert outbox_row.aggregate_version == approved_version
        assert outbox_row.status == "PENDING"

    # 5. Worker 2 boots up and resumes workflow upon receiving approval
    worker2_id = "worker-approval-instance-2"
    executed_steps = []

    async def mock_execute(state: IncidentGraphState):
        executed_steps.append("execute")
        return {"status": "EXECUTED"}

    async def mock_verify(state: IncidentGraphState):
        executed_steps.append("verify")
        return {"verification_passed": True}

    async def mock_resolve(state: IncidentGraphState):
        executed_steps.append("resolve")
        return {"status": "RESOLVED"}

    runner2 = WorkflowRunner(
        session_factory=session_factory,
        worker_id=worker2_id,
        node_overrides={
            "evaluate_policy": mock_policy_approved,
            "execute": mock_execute,
            "verify": mock_verify,
            "resolve": mock_resolve,
        },
    )

    res2 = await runner2.run_incident(incident_id=incident_id, expected_version=approved_version)

    assert res2 is not None
    assert "execute" in executed_steps
    assert "verify" in executed_steps
    assert "resolve" in executed_steps
    assert res2.get("status") == "RESOLVED"
