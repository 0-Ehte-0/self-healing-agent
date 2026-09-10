from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from agentcore.verification.evaluator import TelemetryEvaluator
from agentcore.verification.profiles import load_verification_profile
from sharedmodels.verification import CheckStatus
from telemetryclient.schemas import AlertState, MetricQueryResult, MetricSample, MetricValue


@pytest.mark.asyncio
async def test_target_identity_checks():
    evaluator = TelemetryEvaluator()
    profile = load_verification_profile("SCN-001")
    now = datetime.now(UTC)

    # 1. Matching running container
    sample = await evaluator.evaluate_sample(
        profile=profile,
        sample_index=0,
        elapsed_healthy_seconds=0.0,
        readiness_consecutive_successes=0,
        target_container_id="c1234567890abcdef",
        binding_generation=1,
        container_state={
            "container_id": "c1234567890abcdef",
            "binding_generation": 1,
            "status": "running",
        },
    )
    assert sample.checks["target_identity"].status == CheckStatus.PASS

    # 2. Container ID mismatch
    sample_mismatch = await evaluator.evaluate_sample(
        profile=profile,
        sample_index=0,
        elapsed_healthy_seconds=0.0,
        readiness_consecutive_successes=0,
        target_container_id="c1234567890abcdef",
        binding_generation=1,
        container_state={
            "container_id": "other_container",
            "binding_generation": 1,
            "status": "running",
        },
    )
    assert sample_mismatch.checks["target_identity"].status == CheckStatus.FAIL
    assert "Container ID mismatch" in sample_mismatch.checks["target_identity"].reason

    # 3. Container stopped
    sample_stopped = await evaluator.evaluate_sample(
        profile=profile,
        sample_index=0,
        elapsed_healthy_seconds=0.0,
        readiness_consecutive_successes=0,
        target_container_id="c1234567890abcdef",
        binding_generation=1,
        container_state={
            "container_id": "c1234567890abcdef",
            "binding_generation": 1,
            "status": "exited",
        },
    )
    assert sample_stopped.checks["target_identity"].status == CheckStatus.FAIL


@pytest.mark.asyncio
async def test_readiness_probe_evaluation():
    mock_client = AsyncMock()
    evaluator = TelemetryEvaluator(readiness_client=mock_client)
    profile = load_verification_profile("SCN-001")

    # 1. Success 200 OK within 50ms
    mock_resp_ok = MagicMock(status_code=200)
    mock_client.get.return_value = mock_resp_ok

    sample = await evaluator.evaluate_sample(
        profile=profile,
        sample_index=0,
        elapsed_healthy_seconds=0.0,
        readiness_consecutive_successes=0,
    )
    assert sample.checks["readiness"].status == CheckStatus.PASS
    assert sample.readiness_consecutive_successes == 1

    # 2. HTTP 503 Failure
    mock_resp_503 = MagicMock(status_code=503)
    mock_client.get.return_value = mock_resp_503

    sample_503 = await evaluator.evaluate_sample(
        profile=profile,
        sample_index=1,
        elapsed_healthy_seconds=15.0,
        readiness_consecutive_successes=1,
    )
    assert sample_503.checks["readiness"].status == CheckStatus.FAIL
    assert sample_503.readiness_consecutive_successes == 0


@pytest.mark.asyncio
async def test_counter_reset_post_restart_protection():
    evaluator = TelemetryEvaluator()
    profile = load_verification_profile("SCN-001")
    now = datetime.now(UTC)

    # Container restarted only 5 seconds ago (< 15s required stabilization window)
    started_recently = now - timedelta(seconds=5)

    sample = await evaluator.evaluate_sample(
        profile=profile,
        sample_index=0,
        elapsed_healthy_seconds=0.0,
        readiness_consecutive_successes=0,
        container_started_at=started_recently,
    )

    # Rate calculations must wait for counter stabilization
    assert sample.checks["cpu_saturation"].status == CheckStatus.UNKNOWN
    assert (
        "Waiting for post-restart counter stabilization" in sample.checks["cpu_saturation"].reason
    )
    assert sample.checks["business_traffic"].status == CheckStatus.UNKNOWN
    assert not sample.all_passed


@pytest.mark.asyncio
async def test_traffic_denominator_minimum_20_requests():
    mock_prom = AsyncMock()
    evaluator = TelemetryEvaluator(prometheus_client=mock_prom)
    profile = load_verification_profile("SCN-001")
    now = datetime.now(UTC)

    # Return request rate = 0.1 req/s -> 6 requests in 60s (< 20 required)
    mock_prom.query_instant.return_value = MetricQueryResult(
        query="test",
        status="success",
        freshness_seconds=5.0,
        samples=[
            MetricSample(
                metric={},
                values=[MetricValue(timestamp=now, value=0.1)],
                latest_value=0.1,
                latest_timestamp=now,
            )
        ],
    )

    sample = await evaluator.evaluate_sample(
        profile=profile,
        sample_index=0,
        elapsed_healthy_seconds=0.0,
        readiness_consecutive_successes=3,
        container_started_at=now - timedelta(seconds=60),
    )

    # Insufficient traffic denominator cannot pass!
    assert sample.checks["business_traffic"].status == CheckStatus.UNKNOWN
    assert "Insufficient traffic denominator" in sample.checks["business_traffic"].reason
    assert not sample.all_passed


@pytest.mark.asyncio
async def test_scn002_baseline_band_check():
    mock_prom = AsyncMock()
    evaluator = TelemetryEvaluator(prometheus_client=mock_prom)
    profile = load_verification_profile("SCN-002")
    now = datetime.now(UTC)

    # Query returns total rate = 10 req/s (600 req/60s), error rate = 0.5 req/s -> 95% success rate (< 98% baseline)
    async def mock_query(q):
        if "5.." in q:
            return MetricQueryResult(
                query=q,
                status="success",
                samples=[
                    MetricSample(metric={}, values=[], latest_value=0.5, latest_timestamp=now)
                ],
            )
        else:
            return MetricQueryResult(
                query=q,
                status="success",
                samples=[
                    MetricSample(metric={}, values=[], latest_value=10.0, latest_timestamp=now)
                ],
            )

    mock_prom.query_instant.side_effect = mock_query

    sample = await evaluator.evaluate_sample(
        profile=profile,
        sample_index=0,
        elapsed_healthy_seconds=0.0,
        readiness_consecutive_successes=3,
        container_started_at=now - timedelta(seconds=60),
    )

    # Success rate 95% is below SCN-002 baseline band 98%
    assert sample.checks["business_traffic"].status == CheckStatus.FAIL
    assert "below baseline band" in sample.checks["business_traffic"].reason


@pytest.mark.asyncio
async def test_prohibited_active_alerts_fail():
    mock_prom = AsyncMock()
    mock_prom.get_active_alerts.return_value = [
        AlertState(name="ContainerDown", state="firing", labels={}, annotations={})
    ]
    evaluator = TelemetryEvaluator(prometheus_client=mock_prom)
    profile = load_verification_profile("SCN-001")

    sample = await evaluator.evaluate_sample(
        profile=profile,
        sample_index=0,
        elapsed_healthy_seconds=0.0,
        readiness_consecutive_successes=3,
    )

    assert sample.checks["alerts"].status == CheckStatus.FAIL
    assert "ContainerDown" in sample.checks["alerts"].reason
    assert not sample.all_passed


@pytest.mark.asyncio
async def test_stale_metric_rejection():
    mock_prom = AsyncMock()
    now = datetime.now(UTC)
    # Metric is 45 seconds old (> 30s limit)
    mock_prom.query_instant.return_value = MetricQueryResult(
        query="test",
        status="success",
        freshness_seconds=45.0,
        samples=[
            MetricSample(
                metric={}, values=[], latest_value=0.2, latest_timestamp=now - timedelta(seconds=45)
            )
        ],
    )
    evaluator = TelemetryEvaluator(prometheus_client=mock_prom)
    profile = load_verification_profile("SCN-001")

    sample = await evaluator.evaluate_sample(
        profile=profile,
        sample_index=0,
        elapsed_healthy_seconds=0.0,
        readiness_consecutive_successes=3,
        container_started_at=now - timedelta(seconds=60),
    )

    assert sample.checks["cpu_saturation"].status == CheckStatus.UNKNOWN
    assert "Stale" in sample.checks["cpu_saturation"].reason
