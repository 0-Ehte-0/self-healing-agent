from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from agentcore.nodes.verification import verify_node
from agentcore.verification.evaluator import TelemetryEvaluator
from agentcore.verification.verifier import IndependentVerifier
from telemetryclient.schemas import MetricQueryResult, MetricSample


@pytest.mark.asyncio
async def test_zero_traffic_denominator_cannot_resolve_incident(
    session_factory, create_verifying_incident
):
    """When traffic is below 20 requests in 60s, error rate is UNKNOWN and cannot satisfy verification."""
    ctx = await create_verifying_incident(scenario_id="SCN-001")
    now = datetime.now(UTC)

    mock_prom = AsyncMock()
    # Prometheus reports 0 requests/sec (zero traffic)
    mock_prom.query_instant.return_value = MetricQueryResult(
        query="test",
        status="success",
        samples=[MetricSample(metric={}, values=[], latest_value=0.0, latest_timestamp=now)],
    )
    mock_prom.get_active_alerts.return_value = []

    evaluator = TelemetryEvaluator(prometheus_client=mock_prom)

    current_sim_time = datetime.now(UTC)

    def sim_clock():
        return current_sim_time

    async def sim_sleep(sec):
        nonlocal current_sim_time
        current_sim_time += timedelta(seconds=sec)

    verifier = IndependentVerifier(
        evaluator=evaluator,
        clock_fn=sim_clock,
        sleep_fn=sim_sleep,
    )

    result = await verify_node(
        state=ctx["state"],
        verifier=verifier,
        session_factory=session_factory,
        actor="test:traffic_run",
    )

    # Verification must fail because traffic is UNKNOWN
    assert result["verification_passed"] is False
    assert result["status"] == "COOLDOWN_SCHEDULED"


@pytest.mark.asyncio
async def test_telemetry_outage_resets_stabilization_timer(
    session_factory, create_verifying_incident
):
    """When Prometheus experiences an outage (connection error), checks return UNKNOWN and reset stabilization."""
    ctx = await create_verifying_incident(scenario_id="SCN-001")

    mock_prom = AsyncMock()
    # Prometheus returns unavailable error
    mock_prom.query_instant.return_value = MetricQueryResult(
        query="test",
        status="unavailable",
        error_message="Prometheus connection refused",
    )
    mock_prom.get_active_alerts.return_value = []

    evaluator = TelemetryEvaluator(prometheus_client=mock_prom)

    current_sim_time = datetime.now(UTC)

    def sim_clock():
        return current_sim_time

    async def sim_sleep(sec):
        nonlocal current_sim_time
        current_sim_time += timedelta(seconds=sec)

    verifier = IndependentVerifier(
        evaluator=evaluator,
        clock_fn=sim_clock,
        sleep_fn=sim_sleep,
    )

    result = await verify_node(
        state=ctx["state"],
        verifier=verifier,
        session_factory=session_factory,
        actor="test:outage_run",
    )

    assert result["verification_passed"] is False
    assert result["status"] == "COOLDOWN_SCHEDULED"
