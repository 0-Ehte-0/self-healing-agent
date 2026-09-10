from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from agentcore.verification.verifier import IndependentVerifier
from sharedmodels.verification import (
    CheckObservation,
    CheckStatus,
    EvaluationSample,
    RecoveryAttribution,
    VerificationProfile,
)


def make_sample(sample_index: int, all_passed: bool, readiness_count: int = 3) -> EvaluationSample:
    now = datetime.now(UTC)
    status = CheckStatus.PASS if all_passed else CheckStatus.FAIL
    checks = {
        "target_identity": CheckObservation(
            name="target_identity",
            observed_at=now,
            status=status,
            reason="ok" if all_passed else "fail",
        ),
        "readiness": CheckObservation(
            name="readiness", observed_at=now, status=status, reason="ok" if all_passed else "fail"
        ),
        "business_traffic": CheckObservation(
            name="business_traffic",
            observed_at=now,
            status=status,
            reason="ok" if all_passed else "fail",
        ),
        "alerts": CheckObservation(
            name="alerts", observed_at=now, status=status, reason="ok" if all_passed else "fail"
        ),
    }
    return EvaluationSample(
        timestamp=now,
        sample_index=sample_index,
        elapsed_healthy_seconds=0.0,
        readiness_consecutive_successes=readiness_count,
        all_passed=all_passed,
        health_score=1.0 if all_passed else 0.5,
        checks=checks,
    )


@pytest.mark.asyncio
async def test_sustained_90_second_stabilization_passes():
    """7 consecutive passing samples at 15s interval = 90s healthy window -> PASS."""
    mock_evaluator = AsyncMock()
    mock_evaluator.evaluate_sample.side_effect = [
        make_sample(i, all_passed=True, readiness_count=3) for i in range(15)
    ]

    current_sim_time = datetime.now(UTC)

    def sim_clock():
        return current_sim_time

    async def sim_sleep(sec):
        nonlocal current_sim_time
        current_sim_time += timedelta(seconds=sec)

    verifier = IndependentVerifier(
        evaluator=mock_evaluator,
        clock_fn=sim_clock,
        sleep_fn=sim_sleep,
    )

    verdict = await verifier.run_verification(
        execution_id=uuid4(),
        resource_id=uuid4(),
        profile_name="SCN-001",
    )

    assert verdict.passed is True
    assert verdict.status == "RESOLVED"
    assert verdict.attribution == RecoveryAttribution.AGENT_HEALED
    assert verdict.consecutive_healthy_evaluations >= 7
    assert verdict.elapsed_healthy_seconds >= 90.0
    assert verdict.stabilization_resets == 0
    assert len(verdict.samples) == 7


@pytest.mark.asyncio
async def test_six_samples_spanning_75_seconds_cannot_satisfy_90s():
    """6 samples (75 seconds) stopped prematurely cannot satisfy 90s requirement."""
    mock_evaluator = AsyncMock()
    # Return 6 passing samples, then timeout
    sample_list = [make_sample(i, all_passed=True, readiness_count=3) for i in range(6)]

    current_sim_time = datetime.now(UTC)
    start_sim_time = current_sim_time

    def sim_clock():
        return current_sim_time

    call_count = 0

    async def sim_sleep(sec):
        nonlocal current_sim_time, call_count
        call_count += 1
        current_sim_time += timedelta(seconds=sec)
        if call_count >= 5:
            # Advance clock past max duration (300s) to trigger timeout
            current_sim_time += timedelta(seconds=350)

    async def mock_sample_call(**kwargs):
        idx = kwargs.get("sample_index", 0)
        if idx < len(sample_list):
            return sample_list[idx]
        return make_sample(idx, all_passed=True, readiness_count=3)

    mock_evaluator.evaluate_sample.side_effect = mock_sample_call

    verifier = IndependentVerifier(
        evaluator=mock_evaluator,
        clock_fn=sim_clock,
        sleep_fn=sim_sleep,
    )

    verdict = await verifier.run_verification(
        execution_id=uuid4(),
        resource_id=uuid4(),
        profile_name="SCN-001",
    )

    # Must FAIL because 90 continuous seconds were not achieved
    assert verdict.passed is False
    assert "failed to maintain 90.0s continuous health" in verdict.failure_reason


@pytest.mark.asyncio
async def test_intermittent_check_failure_resets_stabilization_timer():
    """A failing sample resets the elapsed healthy timer to 0 and increments stabilization_resets."""
    mock_evaluator = AsyncMock()

    # Sequence: 3 pass (45s), 1 fail (RESET!), then 7 pass (90s) -> 11 samples total
    sample_sequence = [
        make_sample(0, True),
        make_sample(1, True),
        make_sample(2, True),
        make_sample(3, False),  # Resets timer!
        make_sample(4, True),
        make_sample(5, True),
        make_sample(6, True),
        make_sample(7, True),
        make_sample(8, True),
        make_sample(9, True),
        make_sample(10, True),  # Now reaches 90s!
    ]

    mock_evaluator.evaluate_sample.side_effect = sample_sequence

    current_sim_time = datetime.now(UTC)

    def sim_clock():
        return current_sim_time

    async def sim_sleep(sec):
        nonlocal current_sim_time
        current_sim_time += timedelta(seconds=sec)

    verifier = IndependentVerifier(
        evaluator=mock_evaluator,
        clock_fn=sim_clock,
        sleep_fn=sim_sleep,
    )

    verdict = await verifier.run_verification(
        execution_id=uuid4(),
        resource_id=uuid4(),
        profile_name="SCN-001",
    )

    assert verdict.passed is True
    assert verdict.stabilization_resets == 1
    assert verdict.consecutive_healthy_evaluations >= 7
    assert len(verdict.samples) == 11


@pytest.mark.asyncio
async def test_readiness_warmup_prerequisite_gating():
    """Readiness must succeed 3 consecutive times before the 90s stabilization timer starts."""
    mock_evaluator = AsyncMock()

    # First 2 samples readiness count < 3 (warmup phase)
    s0 = make_sample(0, True, readiness_count=1)
    s1 = make_sample(1, True, readiness_count=2)
    # Sample 2 hits readiness count = 3 (warmup finishes)
    s2 = make_sample(2, True, readiness_count=3)
    # Then 6 more passing samples to reach 90s
    pass_samples = [make_sample(i, True, readiness_count=3) for i in range(3, 9)]

    mock_evaluator.evaluate_sample.side_effect = [s0, s1, s2] + pass_samples

    current_sim_time = datetime.now(UTC)

    def sim_clock():
        return current_sim_time

    async def sim_sleep(sec):
        nonlocal current_sim_time
        current_sim_time += timedelta(seconds=sec)

    verifier = IndependentVerifier(
        evaluator=mock_evaluator,
        clock_fn=sim_clock,
        sleep_fn=sim_sleep,
    )

    verdict = await verifier.run_verification(
        execution_id=uuid4(),
        resource_id=uuid4(),
        profile_name="SCN-001",
    )

    assert verdict.passed is True
    assert verdict.warm_up_duration_seconds is not None
    # 2 samples in warmup @ 15s = approx 30s warmup
    assert verdict.warm_up_duration_seconds >= 30.0


@pytest.mark.asyncio
async def test_fault_expiry_attribution_blocks_agent_healing():
    """If fault expired or was cleared externally, recovery is labeled EXTERNALLY_RECOVERED."""
    mock_evaluator = AsyncMock()
    mock_evaluator.evaluate_sample.side_effect = [
        make_sample(i, all_passed=True, readiness_count=3) for i in range(10)
    ]

    current_sim_time = datetime.now(UTC)

    def sim_clock():
        return current_sim_time

    async def sim_sleep(sec):
        nonlocal current_sim_time
        current_sim_time += timedelta(seconds=sec)

    # Injected 550s ago with 600s TTL -> expires before 90s verification finishes
    injected_at = current_sim_time.timestamp() - 550.0

    verifier = IndependentVerifier(
        evaluator=mock_evaluator,
        clock_fn=sim_clock,
        sleep_fn=sim_sleep,
    )

    verdict = await verifier.run_verification(
        execution_id=uuid4(),
        resource_id=uuid4(),
        scenario_id="SCN-001",
        injected_at=injected_at,
        ttl_seconds=600.0,
    )

    # Must be marked EXTERNALLY_RECOVERED, not passed!
    assert verdict.passed is False
    assert verdict.attribution == RecoveryAttribution.EXTERNALLY_RECOVERED
    assert verdict.status == "EXTERNALLY_RECOVERED"
    assert "TTL expired" in verdict.failure_reason
