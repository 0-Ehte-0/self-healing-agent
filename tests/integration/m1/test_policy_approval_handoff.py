"""Real PostgreSQL guards must accept policy-driven approval routing."""

from uuid import uuid4

import pytest
import sqlalchemy as sa
from agentcore.nodes.policy import evaluate_policy_node
from app.db import models as m
from app.db.repositories.control_plane import ConflictError, unit_of_work
from policyengine import AutomationMode, EvaluationContext
from sharedmodels.enums import IncidentState, RiskLevel, Severity


@pytest.mark.parametrize("explicit_context", [True, False])
async def test_policy_enables_approval_with_versioned_audit(session_factory, explicit_context):
    async with unit_of_work(session_factory, actor="test:approval-handoff") as repo:
        await repo.update_automation_controls(
            mode="APPROVAL_REQUIRED", reason="Approval handoff regression"
        )
        resource = await repo.session.scalar(sa.select(m.Resource).limit(1))
        inc = await repo.create_incident(
            resource_id=resource.id, correlation_key=f"approval-{uuid4()}", severity=Severity.HIGH
        )
        await repo.transition(inc.id, 1, IncidentState.TRIAGED)
        await repo.transition(inc.id, 2, IncidentState.DIAGNOSED)
        diagnosis = m.Diagnosis(
            incident_id=inc.id,
            root_cause="CPU_SATURATION",
            confidence=0.97,
            evidence_ids=[],
            reasoning={},
            actor="test:approval-handoff",
        )
        repo.session.add(diagnosis)
        await repo.session.flush()
        plan = m.RemediationPlan(
            incident_id=inc.id,
            diagnosis_id=diagnosis.id,
            version=1,
            risk=RiskLevel.LOW,
            content_hash="a" * 64,
            container_id="b" * 64,
            binding_generation=1,
            actor="test:approval-handoff",
        )
        repo.session.add(plan)
        await repo.session.flush()
        await repo.transition(inc.id, 3, IncidentState.PLANNED)
        incident_id, plan_id = inc.id, plan.id

    result = await evaluate_policy_node(
        {
            "incident_id": str(incident_id),
            "current_plan_id": str(plan_id),
            "version": 4,
            "automation_mode": "AUTOMATIC",
            "approval_required": False,
        },
        context=EvaluationContext(
            incident_id=incident_id,
            plan_id=plan_id,
            plan_version=1,
            content_hash="a" * 64,
            container_id="b" * 64,
            binding_generation=1,
            service_name="demo-api",
            confidence=0.97,
            root_cause="CPU_SATURATION",
            automation_mode=AutomationMode.APPROVAL_REQUIRED,
        )
        if explicit_context
        else None,
        session_factory=session_factory,
    )
    assert result["status"] == "PENDING_APPROVAL"
    assert result["version"] == 6
    async with session_factory() as db:
        inc = await db.get(m.Incident, incident_id)
        assert inc.approval_required is True
        assert inc.state == IncidentState.PENDING_APPROVAL
        assert inc.attempts == 0
        assert (
            await db.scalar(
                sa.select(sa.func.count())
                .select_from(m.PolicyDecision)
                .where(m.PolicyDecision.incident_id == incident_id)
            )
            == 1
        )
    async with unit_of_work(session_factory, actor="test:stale-approval") as repo:
        with pytest.raises(ConflictError):
            await repo.require_incident_approval(incident_id, 4)
