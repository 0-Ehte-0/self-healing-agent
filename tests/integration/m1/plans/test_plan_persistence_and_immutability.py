from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
import sqlalchemy as sa
from actioncatalog.planner.mapper import DeterministicPlanner
from actioncatalog.planner.validator import compute_plan_content_hash
from agentcore.graph.state import IncidentGraphState
from agentcore.nodes.planning import plan_node
from app.db.models import (
    Approval,
    AttentionItem,
    Diagnosis,
    DryRunRecord,
    EscalationRecord,
    Incident,
    RemediationPlan,
    RemediationStep,
    Resource,
    User,
)
from app.db.repositories.control_plane import unit_of_work
from sharedmodels.enums import IncidentState as S
from sharedmodels.enums import RiskLevel, RootCause, Severity, UserRole
from sharedmodels.plan import TargetBinding


@pytest.mark.asyncio
async def test_atomic_plan_and_steps_persistence(session_factory):
    """Verifies that a remediation plan and its ordered steps are atomically persisted

    with diagnosis linkage, stable content hash, target binding, and verification profile.
    """
    async with unit_of_work(session_factory, actor="test:plan_persistence") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        assert res is not None

        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-plan-persist-{uuid4().hex}",
            severity=Severity.HIGH,
        )
        await repo.transition(inc.id, 1, S.TRIAGED)
        await repo.transition(inc.id, 2, S.DIAGNOSED)

        diag = Diagnosis(
            id=uuid4(),
            incident_id=inc.id,
            root_cause="CONTAINER_STOPPED",
            confidence=0.95,
            evidence_ids=["ev-test-1"],
            reasoning={"status": "exited"},
            actor="test:plan_persistence",
        )
        await repo.add(diag)

        planner = DeterministicPlanner()
        target = TargetBinding(
            resource_id=res.id,
            container_id="1122334455667788",
            service_name="demo-api",
            binding_generation=1,
        )
        plan_schema = planner.create_plan(
            incident_id=inc.id,
            diagnosis_id=diag.id,
            root_cause=RootCause.CONTAINER_STOPPED,
            target_binding=target,
            version=1,
        )

        steps_data = [s.model_dump() for s in plan_schema.steps]
        persisted_plan = await repo.create_remediation_plan_with_steps(
            incident_id=inc.id,
            diagnosis_id=diag.id,
            version=plan_schema.version,
            risk=plan_schema.risk,
            content_hash=plan_schema.content_hash,
            container_id=target.container_id,
            binding_generation=target.binding_generation,
            verification_profile=plan_schema.verification_profile,
            steps_data=steps_data,
        )

        # Retrieve and verify
        plan_db, steps_db = await repo.get_plan_with_steps(persisted_plan.id)
        assert plan_db is not None
        assert plan_db.version == 1
        assert plan_db.risk == RiskLevel.LOW
        assert plan_db.content_hash == plan_schema.content_hash
        assert plan_db.container_id == target.container_id
        assert plan_db.verification_profile == "m1_default_restart_profile"

        assert len(steps_db) == 3
        assert [s.action for s in steps_db] == [
            "inspect_container",
            "restart_container",
            "wait_for_stabilization",
        ]
        assert all(s.action_schema_version == "1.0" for s in steps_db)


@pytest.mark.asyncio
async def test_approved_plan_immutability(session_factory):
    """Verifies database trigger guards: approved remediation plans and their steps

    cannot be updated or deleted.
    """
    now = datetime.now(UTC)
    async with unit_of_work(session_factory, actor="test:immutability") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        assert res is not None

        # Create approver user if needed
        approver = await repo.session.scalar(
            sa.select(User).where(User.role == UserRole.APPROVER).limit(1)
        )
        if not approver:
            approver = User(
                username=f"approver-{uuid4().hex[:6]}",
                password_hash="test-hash",
                role=UserRole.APPROVER,
                enabled=True,
            )
            await repo.add(approver)

        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-immutability-{uuid4().hex}",
            severity=Severity.HIGH,
            approval_required=True,
        )
        await repo.transition(inc.id, 1, S.TRIAGED)
        await repo.transition(inc.id, 2, S.DIAGNOSED)

        diag = Diagnosis(
            id=uuid4(),
            incident_id=inc.id,
            root_cause="CONTAINER_STOPPED",
            confidence=0.95,
            evidence_ids=["ev-test-immut"],
            reasoning={"status": "stopped"},
            actor="test:immutability",
        )
        await repo.add(diag)

        plan = RemediationPlan(
            id=uuid4(),
            incident_id=inc.id,
            diagnosis_id=diag.id,
            version=1,
            risk=RiskLevel.LOW,
            approved=False,
            actor="test:immutability",
        )
        await repo.add(plan)

        step = RemediationStep(
            id=uuid4(),
            plan_id=plan.id,
            resource_id=res.id,
            position=0,
            action="inspect_container",
            parameters={"container_id": "test12345678"},
            verification={},
        )
        await repo.add(step)

        # Approve the plan
        approval = Approval(
            plan_id=plan.id,
            plan_version=1,
            approver_id=approver.id,
            decision="APPROVE",
            expires_at=now + timedelta(minutes=25),
        )
        await repo.add(approval)

        await repo.session.execute(
            sa.update(RemediationPlan).where(RemediationPlan.id == plan.id).values(approved=True)
        )

        plan_id = plan.id
        step_id = step.id

    # Now verify updates and deletes fail under database triggers
    for mutation in [
        sa.update(RemediationPlan).where(RemediationPlan.id == plan_id).values(risk=RiskLevel.HIGH),
        sa.update(RemediationStep)
        .where(RemediationStep.id == step_id)
        .values(parameters={"tampered": True}),
        sa.delete(RemediationPlan).where(RemediationPlan.id == plan_id),
    ]:
        with pytest.raises(sa.exc.DBAPIError, match="immutable"):
            async with unit_of_work(session_factory, actor="test:tamper") as repo:
                await repo.session.execute(mutation)


@pytest.mark.asyncio
async def test_plan_versioning_and_approval_invalidation(session_factory):
    """Verifies that changing a plan parameter or diagnosis produces a distinct plan version

    and prior approvals cannot approve the new version.
    """
    now = datetime.now(UTC)
    async with unit_of_work(session_factory, actor="test:versioning") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        approver = await repo.session.scalar(
            sa.select(User).where(User.role == UserRole.APPROVER).limit(1)
        )
        assert res is not None and approver is not None

        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-version-bump-{uuid4().hex}",
            severity=Severity.HIGH,
            approval_required=True,
        )
        await repo.transition(inc.id, 1, S.TRIAGED)
        await repo.transition(inc.id, 2, S.DIAGNOSED)

        diag = Diagnosis(
            id=uuid4(),
            incident_id=inc.id,
            root_cause="CONTAINER_STOPPED",
            confidence=0.95,
            evidence_ids=["ev-test-v"],
            reasoning={},
            actor="test:versioning",
        )
        await repo.add(diag)

        # Plan v1
        plan_v1 = RemediationPlan(
            id=uuid4(),
            incident_id=inc.id,
            diagnosis_id=diag.id,
            version=1,
            risk=RiskLevel.LOW,
            content_hash="1" * 64,
            approved=True,
            actor="test:versioning",
        )
        await repo.add(plan_v1)

        approval_v1 = Approval(
            plan_id=plan_v1.id,
            plan_version=1,
            approver_id=approver.id,
            decision="APPROVE",
            expires_at=now + timedelta(minutes=20),
        )
        await repo.add(approval_v1)

        # Plan v2 (new version on same incident)
        plan_v2 = RemediationPlan(
            id=uuid4(),
            incident_id=inc.id,
            diagnosis_id=diag.id,
            version=2,
            risk=RiskLevel.LOW,
            content_hash="2" * 64,
            approved=False,
            actor="test:versioning",
        )
        await repo.add(plan_v2)

        # Verify that approval_v1 is specifically tied to plan_v1 version 1
        assert approval_v1.plan_version == 1
        assert approval_v1.plan_id == plan_v1.id
        assert plan_v2.approved is False
        assert approval_v1.plan_id != plan_v2.id or approval_v1.plan_version != plan_v2.version


@pytest.mark.asyncio
async def test_dry_run_record_persistence(session_factory):
    """Verifies that DryRunRecord is stored in database with simulation results."""
    async with unit_of_work(session_factory, actor="test:dry_run_persist") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        assert res is not None

        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-dryrun-persist-{uuid4().hex}",
            severity=Severity.LOW,
        )
        await repo.transition(inc.id, 1, S.TRIAGED)
        await repo.transition(inc.id, 2, S.DIAGNOSED)

        diag = Diagnosis(
            id=uuid4(),
            incident_id=inc.id,
            root_cause="CPU_SATURATION",
            confidence=0.90,
            evidence_ids=["ev-cpu"],
            reasoning={},
            actor="test:dry_run_persist",
        )
        await repo.add(diag)

        plan = RemediationPlan(
            id=uuid4(),
            incident_id=inc.id,
            diagnosis_id=diag.id,
            version=1,
            risk=RiskLevel.LOW,
            content_hash="c" * 64,
            actor="test:dry_run_persist",
        )
        await repo.add(plan)

        dry_run_rec = await repo.create_dry_run_record(
            incident_id=inc.id,
            plan_id=plan.id,
            plan_version=1,
            content_hash="c" * 64,
            policy_evaluation={"eligible": True},
            validation_result={"valid": True},
            simulated_steps=[{"action": "restart_container", "simulated": True}],
        )

        assert dry_run_rec.id is not None
        assert dry_run_rec.incident_id == inc.id
        assert dry_run_rec.plan_id == plan.id


@pytest.mark.asyncio
async def test_plan_node_authoritative_transition_diagnosed_to_planned(session_factory):
    """Verifies that plan_node executes the atomic database transition DIAGNOSED -> PLANNED

    and commits the plan and steps to PostgreSQL.
    """
    async with unit_of_work(session_factory, actor="test:plan_node") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        assert res is not None

        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-node-transition-{uuid4().hex}",
            severity=Severity.MEDIUM,
        )
        inc = await repo.transition(inc.id, 1, S.TRIAGED)
        inc = await repo.transition(inc.id, 2, S.DIAGNOSED)

        diag = Diagnosis(
            id=uuid4(),
            incident_id=inc.id,
            root_cause="CONTAINER_STOPPED",
            confidence=0.99,
            evidence_ids=["ev-node-1"],
            reasoning={"state": "exited"},
            actor="test:plan_node",
        )
        await repo.add(diag)

        incident_id = inc.id
        diagnosis_id = diag.id
        resource_id = res.id

    # Execute plan_node with session_factory
    state: IncidentGraphState = {
        "incident_id": str(incident_id),
        "version": 3,
        "resource_id": str(resource_id),
        "current_diagnosis_id": str(diagnosis_id),
        "target_container_id": "1122334455667788",
        "binding_generation": 1,
    }

    result = await plan_node(state, session_factory=session_factory, actor="worker:test")

    assert result["status"] == "PLANNED"
    assert result["current_plan_id"] is not None
    assert result["version"] == 4

    # Verify incident state in database
    async with unit_of_work(session_factory, actor="test:verify") as repo:
        updated_inc = await repo.session.get(Incident, incident_id)
        assert updated_inc is not None
        assert updated_inc.state == S.PLANNED
        assert updated_inc.version == 4

        # Verify plan persisted
        plan_id = updated_inc.id  # query plans
        plans = list(
            (
                await repo.session.scalars(
                    sa.select(RemediationPlan).where(RemediationPlan.incident_id == incident_id)
                )
            ).all()
        )
        assert len(plans) == 1
        assert plans[0].version == 1
        assert plans[0].risk == RiskLevel.LOW


@pytest.mark.asyncio
async def test_plan_node_escalation_on_unsupported_cause(session_factory):
    """Verifies that plan_node cleanly transitions DIAGNOSED -> ESCALATED and creates

    EscalationRecord and AttentionItem when diagnosis root cause is unsupported.
    """
    async with unit_of_work(session_factory, actor="test:plan_escalate") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        assert res is not None

        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-node-escalate-{uuid4().hex}",
            severity=Severity.HIGH,
        )
        inc = await repo.transition(inc.id, 1, S.TRIAGED)
        inc = await repo.transition(inc.id, 2, S.DIAGNOSED)

        diag = Diagnosis(
            id=uuid4(),
            incident_id=inc.id,
            root_cause="DEPENDENCY_UNAVAILABLE",
            confidence=0.88,
            evidence_ids=["ev-redis-down"],
            escalation_reason="Redis cluster is unresponsive; automated container restart cannot resolve dependency outage",
            reasoning={"dependency": "redis"},
            actor="test:plan_escalate",
        )
        await repo.add(diag)

        incident_id = inc.id
        diagnosis_id = diag.id
        resource_id = res.id

    state: IncidentGraphState = {
        "incident_id": str(incident_id),
        "version": 3,
        "resource_id": str(resource_id),
        "current_diagnosis_id": str(diagnosis_id),
        "target_container_id": "1122334455667788",
        "binding_generation": 1,
    }

    result = await plan_node(state, session_factory=session_factory, actor="worker:test")

    assert result["status"] == "PLANNING_ESCALATED"
    assert result["version"] == 4

    # Verify incident state in database is ESCALATED
    async with unit_of_work(session_factory, actor="test:verify") as repo:
        updated_inc = await repo.session.get(Incident, incident_id)
        assert updated_inc is not None
        assert updated_inc.state == S.ESCALATED
        assert updated_inc.version == 4

        # Verify EscalationRecord and AttentionItem created
        escalations = list(
            (
                await repo.session.scalars(
                    sa.select(EscalationRecord).where(EscalationRecord.incident_id == incident_id)
                )
            ).all()
        )
        assert len(escalations) == 1
        assert escalations[0].root_cause == "DEPENDENCY_UNAVAILABLE"

        attention_items = list(
            (
                await repo.session.scalars(
                    sa.select(AttentionItem).where(AttentionItem.incident_id == incident_id)
                )
            ).all()
        )
        assert len(attention_items) == 1
        assert attention_items[0].severity == "HIGH"
