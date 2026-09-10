from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa
from agentcore.nodes.verification import verify_node
from agentcore.verification.verifier import IndependentVerifier
from app.db.models import (
    AttentionItem,
    EscalationRecord,
    Incident,
    VerificationResult,
)
from app.db.repositories.control_plane import unit_of_work
from sharedmodels.enums import IncidentState
from sharedmodels.verification import CheckObservation, CheckStatus, EvaluationSample


@pytest.mark.asyncio
async def test_fault_ttl_expiry_leads_to_external_recovery_escalation(
    session_factory, create_verifying_incident
):
    """If fault TTL expired before verification finished, run is marked EXTERNALLY_RECOVERED and escalates."""
    ctx = await create_verifying_incident(scenario_id="SCN-002")
    inc_id = ctx["incident"].id

    mock_evaluator = AsyncMock()
    now = datetime.now(UTC)

    def passing_sample(idx):
        return EvaluationSample(
            timestamp=now,
            sample_index=idx,
            elapsed_healthy_seconds=float(idx * 15),
            readiness_consecutive_successes=3,
            all_passed=True,
            health_score=1.0,
            checks={
                "target_identity": CheckObservation(
                    name="target_identity", observed_at=now, status=CheckStatus.PASS, reason="ok"
                ),
                "readiness": CheckObservation(
                    name="readiness", observed_at=now, status=CheckStatus.PASS, reason="ok"
                ),
            },
        )

    mock_evaluator.evaluate_sample.side_effect = [passing_sample(i) for i in range(10)]

    current_sim_time = datetime.now(UTC)

    def sim_clock():
        return current_sim_time

    async def sim_sleep(sec):
        nonlocal current_sim_time
        current_sim_time += timedelta(seconds=sec)

    # Injected 550s ago with 600s TTL (expires after 50s, while 90s verification is in progress)
    injected_at = current_sim_time.timestamp() - 550.0

    verifier = IndependentVerifier(
        evaluator=mock_evaluator,
        clock_fn=sim_clock,
        sleep_fn=sim_sleep,
    )

    # Wrap verifier.verify to pass injected_at and ttl_seconds
    orig_verify = verifier.verify

    async def custom_verify(**kwargs):
        kwargs["injected_at"] = injected_at
        kwargs["ttl_seconds"] = 600.0
        return await orig_verify(**kwargs)

    verifier.verify = custom_verify

    result = await verify_node(
        state=ctx["state"],
        verifier=verifier,
        session_factory=session_factory,
        actor="test:attr_run",
    )

    # Escalated because recovery was external!
    assert result["status"] == "ESCALATED"
    assert result["verification_passed"] is False
    assert result["attribution"] == "EXTERNALLY_RECOVERED"
    assert "TTL expired" in result["last_error"]

    # Verify database persistence
    async with unit_of_work(session_factory, actor="test:check") as repo:
        inc = await repo.session.get(Incident, inc_id)
        assert inc.state == IncidentState.ESCALATED

        v_res = await repo.session.scalar(
            sa.select(VerificationResult).where(VerificationResult.incident_id == inc_id)
        )
        assert v_res is not None
        assert v_res.passed is False
        assert v_res.attribution == "EXTERNALLY_RECOVERED"

        esc = await repo.session.scalar(
            sa.select(EscalationRecord).where(EscalationRecord.incident_id == inc_id)
        )
        assert esc is not None
        assert esc.root_cause == "EXTERNALLY_RECOVERED"

        attention = await repo.session.scalar(
            sa.select(AttentionItem).where(AttentionItem.incident_id == inc_id)
        )
        assert attention is not None
        assert "External Recovery" in attention.reason


@pytest.mark.asyncio
async def test_manual_fault_clear_leads_to_external_recovery_escalation(
    session_factory, create_verifying_incident
):
    """If fault status provider reports 'cleared', run is marked EXTERNALLY_RECOVERED and escalates."""
    ctx = await create_verifying_incident(scenario_id="SCN-002")
    inc_id = ctx["incident"].id

    mock_evaluator = AsyncMock()
    now = datetime.now(UTC)

    def passing_sample(idx):
        return EvaluationSample(
            timestamp=now,
            sample_index=idx,
            elapsed_healthy_seconds=float(idx * 15),
            readiness_consecutive_successes=3,
            all_passed=True,
            health_score=1.0,
            checks={
                "target_identity": CheckObservation(
                    name="target_identity", observed_at=now, status=CheckStatus.PASS, reason="ok"
                ),
                "readiness": CheckObservation(
                    name="readiness", observed_at=now, status=CheckStatus.PASS, reason="ok"
                ),
            },
        )

    mock_evaluator.evaluate_sample.side_effect = [passing_sample(i) for i in range(10)]

    current_sim_time = datetime.now(UTC)

    def sim_clock():
        return current_sim_time

    async def sim_sleep(sec):
        nonlocal current_sim_time
        current_sim_time += timedelta(seconds=sec)

    # Fault status provider returns cleared by operator
    async def mock_fault_provider(scenario_id):
        return {
            "status": "cleared",
            "record": {
                "status": "cleared",
                "cleared_at": current_sim_time.timestamp() - 10.0,
            },
        }

    verifier = IndependentVerifier(
        evaluator=mock_evaluator,
        clock_fn=sim_clock,
        sleep_fn=sim_sleep,
        fault_status_provider=mock_fault_provider,
    )

    result = await verify_node(
        state=ctx["state"],
        verifier=verifier,
        session_factory=session_factory,
        actor="test:attr_run",
    )

    assert result["status"] == "ESCALATED"
    assert result["verification_passed"] is False
    assert result["attribution"] == "EXTERNALLY_RECOVERED"
