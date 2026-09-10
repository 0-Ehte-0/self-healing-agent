import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.db.models import WorkflowSchedule

from agentcore.runtime.scheduler import WorkflowScheduler
from agentcore.verification.scheduling import (
    DEFAULT_RESOURCE_COOLDOWN_SECONDS,
    DEFAULT_RETRY_LIMIT,
    DEFAULT_WORKFLOW_TIMEOUT_SECONDS,
    calculate_next_eligible_time,
    compute_attempt_counters,
    is_retry_eligible,
)

logger = logging.getLogger(__name__)


async def schedule_retry_cooldown(
    scheduler: WorkflowScheduler,
    incident_id: UUID,
    incident_version: int,
    cooldown_seconds: int = DEFAULT_RESOURCE_COOLDOWN_SECONDS,
    workflow_started_at: datetime | None = None,
    attempt: int = 1,
) -> WorkflowSchedule:
    """Schedules a persistent workflow wake-up after the 600-second cooldown expires."""
    now = datetime.now(UTC)
    next_eligible_at = calculate_next_eligible_time(now, cooldown_seconds=cooldown_seconds)
    deadline = (
        workflow_started_at + timedelta(seconds=DEFAULT_WORKFLOW_TIMEOUT_SECONDS)
        if workflow_started_at
        else None
    )

    resume_metadata = {
        "is_retry": True,
        "previous_attempt": attempt,
        "cooldown_scheduled_at": now.isoformat(),
        "next_eligible_at": next_eligible_at.isoformat(),
    }

    schedule = await scheduler.schedule_wakeup(
        incident_id=incident_id,
        version=incident_version,
        delay_seconds=cooldown_seconds,
        wait_reason="COOLDOWN",
        deadline=deadline,
        resume_metadata=resume_metadata,
    )
    logger.info(
        f"Scheduled retry cooldown for incident {incident_id} (version {incident_version}) due at {next_eligible_at}"
    )
    return schedule
