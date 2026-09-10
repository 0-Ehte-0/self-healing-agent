from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import sqlalchemy as sa
from agentcore.nodes.verification import verify_node
from app.db.models import (
    AttentionItem,
    EscalationRecord,
    Incident,
    VerificationResult,
    WorkflowSchedule,
)
from app.db.repositories.control_plane import unit_of_work
from sharedmodels.enums import IncidentState


@pytest.mark.asyncio
async def test_verification_node_success_resolves_and_persists_result(
    session_factory, create_verifying_incident
):
    ctx = await create_verifying_incident(scenario_id="SCN-001")
    inc_id = ctx["incident"].id
    exec_id = ctx["execution"].id

    mock_verifier = AsyncMock()
    mock_verifier.verify.return_value = {
        "passed": True,
        "status": "RESOLVED",
        "attribution": "AGENT_HEALED",
        "profile_id": "SCN-001",
        "profile_version": "1.0",
        "health_score": 1.0,
        "warm_up_duration_seconds": 30.5,
        "stabilization_resets": 0,
        "window_start": datetime.now(UTC) - timedelta(seconds=120),
        "window_end": datetime.now(UTC),
        "checks": {"readiness": {"status": "PASS"}},
        "samples": [{"sample_index": 0, "all_passed": True}],
    }

    result = await verify_node(
        state=ctx["state"],
        verifier=mock_verifier,
        session_factory=session_factory,
        actor="test:verif_run",
    )

    assert result["status"] == "RESOLVED"
    assert result["verification_passed"] is True
    assert result["retry_eligible"] is False

    # Check database persistence
    async with unit_of_work(session_factory, actor="test:check") as repo:
        inc = await repo.session.get(Incident, inc_id)
        assert inc.state == IncidentState.RESOLVED
        assert inc.resolved_at is not None

        # Check VerificationResult row
        v_results = list(
            (
                await repo.session.scalars(
                    sa.select(VerificationResult).where(VerificationResult.incident_id == inc_id)
                )
            ).all()
        )
        assert len(v_results) == 1
        assert v_results[0].passed is True
        assert v_results[0].attribution == "AGENT_HEALED"
        assert v_results[0].warm_up_duration_seconds == 30.5
        assert v_results[0].stabilization_resets == 0
        assert len(v_results[0].samples) == 1


@pytest.mark.asyncio
async def test_verification_node_failure_schedules_cooldown_and_transitions_diagnosed(
    session_factory, create_verifying_incident
):
    ctx = await create_verifying_incident(scenario_id="SCN-001")
    inc_id = ctx["incident"].id

    mock_verifier = AsyncMock()
    mock_verifier.verify.return_value = {
        "passed": False,
        "status": "VERIFICATION_FAILED",
        "attribution": "AGENT_HEALED",
        "profile_id": "SCN-001",
        "profile_version": "1.0",
        "health_score": 0.4,
        "warm_up_duration_seconds": None,
        "stabilization_resets": 2,
        "window_start": datetime.now(UTC) - timedelta(seconds=300),
        "window_end": datetime.now(UTC),
        "checks": {"business_traffic": {"status": "FAIL"}},
        "samples": [],
        "failure_reason": "Failed to sustain 90s stabilization window",
    }

    result = await verify_node(
        state=ctx["state"],
        verifier=mock_verifier,
        session_factory=session_factory,
        actor="test:verif_run",
        cooldown_seconds=600,
    )

    assert result["status"] == "COOLDOWN_SCHEDULED"
    assert result["verification_passed"] is False
    assert result["retry_eligible"] is True
    assert result["wait_reason"] == "COOLDOWN"
    assert result["attempts"] == 1  # Incremented from 0 to 1

    # Check database: state transitioned VERIFYING -> DIAGNOSED
    async with unit_of_work(session_factory, actor="test:check") as repo:
        inc = await repo.session.get(Incident, inc_id)
        assert inc.state == IncidentState.DIAGNOSED
        assert inc.attempts == 1

        # Check WorkflowSchedule created
        schedules = list(
            (
                await repo.session.scalars(
                    sa.select(WorkflowSchedule).where(
                        WorkflowSchedule.incident_id == inc_id,
                        WorkflowSchedule.wait_reason == "COOLDOWN",
                    )
                )
            ).all()
        )
        assert len(schedules) == 1
        assert schedules[0].status == "PENDING"
        assert schedules[0].resume_metadata["is_retry"] is True


@pytest.mark.asyncio
async def test_verification_node_exhausted_retries_escalates(
    session_factory, create_verifying_incident
):
    # Incident starts with attempts = 2 (retry_limit = 2)
    ctx = await create_verifying_incident(scenario_id="SCN-001", attempts=2, retry_limit=2)
    inc_id = ctx["incident"].id

    mock_verifier = AsyncMock()
    mock_verifier.verify.return_value = {
        "passed": False,
        "status": "VERIFICATION_FAILED",
        "attribution": "AGENT_HEALED",
        "profile_id": "SCN-001",
        "profile_version": "1.0",
        "health_score": 0.2,
        "stabilization_resets": 1,
        "checks": {},
        "samples": [],
        "failure_reason": "Readiness timed out",
    }

    result = await verify_node(
        state=ctx["state"],
        verifier=mock_verifier,
        session_factory=session_factory,
        actor="test:verif_run",
    )

    assert result["status"] == "VERIFICATION_ESCALATED"
    assert result["verification_passed"] is False
    assert result["retry_eligible"] is False

    # Check database: state is ESCALATED
    async with unit_of_work(session_factory, actor="test:check") as repo:
        inc = await repo.session.get(Incident, inc_id)
        assert inc.state == IncidentState.ESCALATED

        # EscalationRecord created
        escs = list(
            (
                await repo.session.scalars(
                    sa.select(EscalationRecord).where(EscalationRecord.incident_id == inc_id)
                )
            ).all()
        )
        assert len(escs) == 1
        assert escs[0].root_cause == "VERIFICATION_FAILED"

        # AttentionItem created
        attentions = list(
            (
                await repo.session.scalars(
                    sa.select(AttentionItem).where(AttentionItem.incident_id == inc_id)
                )
            ).all()
        )
        assert any("Remediation Verification Failed" in a.reason for a in attentions)
