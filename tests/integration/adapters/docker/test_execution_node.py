from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import sqlalchemy as sa
from agentcore.graph.state import IncidentGraphState
from agentcore.nodes.execution import execute_node
from app.db.models import (
    AttentionItem,
    Diagnosis,
    EscalationRecord,
    Incident,
    PolicyDecision,
    RemediationPlan,
    RemediationStep,
    Resource,
)
from app.db.repositories.control_plane import unit_of_work
from provideradapters.base import (
    ExecutionOutcome,
    ExecutionOutcomeStatus,
    PrecheckFailedError,
)
from sharedmodels.enums import ExecutionStatus, IncidentState, RiskLevel, Severity


@pytest.fixture
async def setup_node_context(session_factory):
    res_id = uuid4()
    cid = f"node11223344556677889900{res_id.hex[:16]}"
    async with unit_of_work(session_factory, actor="test:node_setup") as repo:
        res = Resource(
            id=res_id,
            provider="compose",
            external_id=f"self-healing:demo-api-{res_id.hex[:8]}",
            name="demo-api",
            environment="local",
            labels={
                "compose_service": "demo-api",
                "compose_project": "self-healing-agent",
                "docker_container_id": cid,
                "binding_generation": 1,
            },
            managed=True,
        )
        repo.session.add(res)
        await repo.session.flush()

        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"corr-node-{uuid4().hex}",
            severity=Severity.HIGH,
            approval_required=False,
        )
        await repo.transition(inc.id, 1, IncidentState.TRIAGED)
        await repo.transition(inc.id, 2, IncidentState.DIAGNOSED)

        diag = Diagnosis(
            id=uuid4(),
            incident_id=inc.id,
            root_cause="CONTAINER_STOPPED",
            confidence=0.95,
            evidence_ids=["ev-test"],
            reasoning={"state": "exited"},
            actor="test:node_setup",
        )
        await repo.add(diag)

        step_id = uuid4()
        plan = await repo.create_remediation_plan_with_steps(
            incident_id=inc.id,
            diagnosis_id=diag.id,
            version=1,
            risk=RiskLevel.LOW,
            content_hash="hash_node_123",
            container_id=cid,
            binding_generation=1,
            verification_profile="m1_default_restart_profile",
            steps_data=[
                {
                    "id": step_id,
                    "resource_id": res.id,
                    "position": 0,
                    "action": "restart_container",
                    "action_schema_version": "1.0",
                    "parameters": {
                        "container_id": cid,
                        "resource_id": str(res.id),
                        "service_name": "demo-api",
                        "timeout_seconds": 15,
                        "binding_generation": 1,
                    },
                    "verification": {"profile": "m1_default_restart_profile"},
                }
            ],
        )

        policy_dec = PolicyDecision(
            id=uuid4(),
            incident_id=inc.id,
            plan_id=plan.id,
            plan_version=1,
            policy_version=1,
            content_hash="hash_node_123",
            container_id=cid,
            binding_generation=1,
            decision="ALLOW",
            reason_codes=["M1_POLICY_AUTO_ALLOW"],
            rule_results=[],
            evaluated_facts={},
            actor="test:node_setup",
        )
        repo.session.add(policy_dec)
        await repo.session.flush()

        await repo.transition(inc.id, 3, IncidentState.PLANNED)

    state: IncidentGraphState = {
        "incident_id": str(inc.id),
        "version": 4,
        "correlation_key": inc.correlation_key,
        "resource_id": str(res.id),
        "current_diagnosis_id": str(diag.id),
        "current_plan_id": str(plan.id),
        "attempts": 0,
        "retry_limit": 2,
        "approval_required": False,
        "is_approved": False,
        "target_container_id": cid,
        "binding_generation": 1,
    }

    return {
        "resource": res,
        "incident": inc,
        "plan": plan,
        "state": state,
        "container_id": cid,
    }


@pytest.mark.asyncio
async def test_execute_node_precheck_failure_escalates_without_executing(
    session_factory, setup_node_context
):
    """Verifies that precheck failure transitions incident directly PLANNED -> ESCALATED without entering EXECUTING."""
    ctx = setup_node_context
    inc_id = ctx["incident"].id

    mock_adapter = MagicMock()
    mock_adapter.precheck = AsyncMock(
        side_effect=PrecheckFailedError("Container target label missing")
    )
    mock_adapter.execute = AsyncMock()

    result = await execute_node(
        state=ctx["state"],
        adapter=mock_adapter,
        session_factory=session_factory,
        actor="test:node_exec",
    )

    assert result["status"] == "PRECHECK_ESCALATED"
    assert "Container target label missing" in result["last_error"]
    assert mock_adapter.execute.call_count == 0  # Mutation dispatch NEVER called

    # Check incident was transitioned directly to ESCALATED
    async with unit_of_work(session_factory, actor="test:verify") as repo:
        inc = await repo.session.get(Incident, inc_id)
        assert inc.state == IncidentState.ESCALATED

        escalations = list(
            (
                await repo.session.scalars(
                    sa.select(EscalationRecord).where(EscalationRecord.incident_id == inc_id)
                )
            ).all()
        )
        assert len(escalations) == 1
        assert escalations[0].root_cause == "PRECHECK_FAILED"


@pytest.mark.asyncio
async def test_execute_node_success_transitions_executing_to_verifying(
    session_factory, setup_node_context
):
    """Verifies that successful precheck & execution transitions PLANNED -> EXECUTING -> VERIFYING."""
    ctx = setup_node_context
    inc_id = ctx["incident"].id

    mock_adapter = MagicMock()
    mock_adapter.precheck = AsyncMock(return_value={"precheck_passed": True})
    mock_adapter.execute = AsyncMock(
        return_value=ExecutionOutcome(
            status=ExecutionOutcomeStatus.SUCCEEDED,
            execution_id=uuid4(),
            idempotency_key="key-test",
            post_state={"status": "running"},
        )
    )

    result = await execute_node(
        state=ctx["state"],
        adapter=mock_adapter,
        session_factory=session_factory,
        actor="test:node_exec",
    )

    assert result["status"] == "EXECUTED"
    assert result["attempts"] == 1

    # Check incident is in VERIFYING state
    async with unit_of_work(session_factory, actor="test:verify") as repo:
        inc = await repo.session.get(Incident, inc_id)
        assert inc.state == IncidentState.VERIFYING


@pytest.mark.asyncio
async def test_execute_node_failure_transitions_executing_to_failed_to_escalated(
    session_factory, setup_node_context
):
    """Verifies that failed execution transitions EXECUTING -> FAILED -> ESCALATED."""
    ctx = setup_node_context
    inc_id = ctx["incident"].id

    mock_adapter = MagicMock()
    mock_adapter.precheck = AsyncMock(return_value={"precheck_passed": True})
    mock_adapter.execute = AsyncMock(
        return_value=ExecutionOutcome(
            status=ExecutionOutcomeStatus.FAILED,
            execution_id=uuid4(),
            idempotency_key="key-test",
            error="Docker daemon crashed during restart",
        )
    )

    result = await execute_node(
        state=ctx["state"],
        adapter=mock_adapter,
        session_factory=session_factory,
        actor="test:node_exec",
    )

    assert result["status"] == "EXECUTION_FAILED"

    # Check incident transitioned to ESCALATED
    async with unit_of_work(session_factory, actor="test:verify") as repo:
        inc = await repo.session.get(Incident, inc_id)
        assert inc.state == IncidentState.ESCALATED

        attentions = list(
            (
                await repo.session.scalars(
                    sa.select(AttentionItem).where(AttentionItem.incident_id == inc_id)
                )
            ).all()
        )
        assert any("Execution Failed" in a.reason for a in attentions)
