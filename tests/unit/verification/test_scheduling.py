from datetime import UTC, datetime, timedelta

from agentcore.runtime.scheduling import (
    calculate_next_eligible_time,
    compute_attempt_counters,
    is_retry_eligible,
)


def test_compute_attempt_counters():
    c0 = compute_attempt_counters(0, retry_limit=2)
    assert c0["attempt_number"] == 1
    assert c0["retries_used"] == 0
    assert c0["max_dispatches"] == 3

    c1 = compute_attempt_counters(1, retry_limit=2)
    assert c1["attempt_number"] == 2
    assert c1["retries_used"] == 1

    c2 = compute_attempt_counters(2, retry_limit=2)
    assert c2["attempt_number"] == 3
    assert c2["retries_used"] == 2


def test_is_retry_eligible_attempt_budget():
    # Attempt 1 (attempts = 0): eligible
    ok, reason = is_retry_eligible(attempts=0, retry_limit=2)
    assert ok is True

    # Attempt 2 (attempts = 1): eligible
    ok, reason = is_retry_eligible(attempts=1, retry_limit=2)
    assert ok is True

    # Attempt 3 (attempts = 2): EXHAUSTED! (2 retries used = 3 attempts total)
    ok, reason = is_retry_eligible(attempts=2, retry_limit=2)
    assert ok is False
    assert "Retry budget exhausted" in reason


def test_is_retry_eligible_workflow_deadline():
    now = datetime.now(UTC)
    # Started 40 minutes (2400s) ago: with 600s cooldown + 90s, total = 3090s > 2700s (45m)
    started_long_ago = now - timedelta(minutes=40)

    ok, reason = is_retry_eligible(
        attempts=1,
        retry_limit=2,
        workflow_started_at=started_long_ago,
        now=now,
    )
    assert ok is False
    assert "Workflow deadline would be exceeded" in reason


def test_calculate_next_eligible_time():
    now = datetime.now(UTC)
    next_time = calculate_next_eligible_time(now, cooldown_seconds=600)
    assert (next_time - now).total_seconds() == 600
