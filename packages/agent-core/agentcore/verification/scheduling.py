import logging
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_RESOURCE_COOLDOWN_SECONDS = 600  # 10 minutes per-resource cooldown
DEFAULT_WORKFLOW_TIMEOUT_SECONDS = 2700  # 45 minutes overall workflow deadline
DEFAULT_RETRY_LIMIT = 2  # 2 retries = at most 3 dispatches


def compute_attempt_counters(
    attempts: int, retry_limit: int = DEFAULT_RETRY_LIMIT
) -> dict[str, int]:
    """Computes distinct attempt_number and retries_used to avoid misleading operators.

    Per ADR-0004 & Section 11 Step 12:
    - Initial attempt: attempt_number = 1, retries_used = 0
    - Retry 1: attempt_number = 2, retries_used = 1
    - Retry 2: attempt_number = 3, retries_used = 2
    """
    retries_used = max(0, attempts)
    attempt_number = retries_used + 1
    max_dispatches = retry_limit + 1
    return {
        "attempt_number": attempt_number,
        "retries_used": retries_used,
        "retry_limit": retry_limit,
        "max_dispatches": max_dispatches,
    }


def is_retry_eligible(
    attempts: int,
    retry_limit: int = DEFAULT_RETRY_LIMIT,
    workflow_started_at: datetime | None = None,
    cooldown_seconds: int = DEFAULT_RESOURCE_COOLDOWN_SECONDS,
    max_workflow_duration_seconds: int = DEFAULT_WORKFLOW_TIMEOUT_SECONDS,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Checks whether an incident is eligible for another remediation retry."""
    current_time = now or datetime.now(UTC)

    # 1. Check attempt budget
    if attempts >= retry_limit:
        return False, f"Retry budget exhausted ({attempts}/{retry_limit} retries used)"

    # 2. Check overall workflow deadline (45 minutes)
    if workflow_started_at is not None:
        elapsed = (current_time - workflow_started_at).total_seconds()
        # Ensure that cooldown + estimated execution (approx 90s) can complete within deadline
        if elapsed + cooldown_seconds + 90.0 > max_workflow_duration_seconds:
            return (
                False,
                f"Workflow deadline would be exceeded (elapsed {elapsed:.0f}s + cooldown {cooldown_seconds}s > {max_workflow_duration_seconds}s)",
            )

    return True, "Retry eligible"


def calculate_next_eligible_time(
    last_action_at: datetime | None = None,
    cooldown_seconds: int = DEFAULT_RESOURCE_COOLDOWN_SECONDS,
    now: datetime | None = None,
) -> datetime:
    """Calculates the exact UTC timestamp when the resource cooldown expires."""
    base_time = last_action_at or now or datetime.now(UTC)
    return base_time + timedelta(seconds=cooldown_seconds)
