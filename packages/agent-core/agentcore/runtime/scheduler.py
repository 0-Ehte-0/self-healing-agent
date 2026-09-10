import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.db.models import WorkflowSchedule
from app.db.repositories.control_plane import unit_of_work
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


class WorkflowScheduler:
    """Manages persisted workflow timer schedules, approval interrupts, and recovery wake-ups.

    Per Section 6 Step 8: Persist approval interrupts and timer wake-ups. A worker process
    must not occupy a thread waiting for a person or cooldown.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        actor: str = "worker:scheduler",
    ):
        self.session_factory = session_factory
        self.actor = actor

    async def schedule_wakeup(
        self,
        incident_id: UUID,
        version: int,
        delay_seconds: float,
        wait_reason: str,
        deadline: datetime | None = None,
        resume_metadata: dict[str, Any] | None = None,
    ) -> WorkflowSchedule:
        """Schedules a future wake-up without blocking any worker thread."""
        next_run_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)
        async with unit_of_work(self.session_factory, actor=self.actor) as repo:
            schedule = await repo.create_workflow_schedule(
                incident_id=incident_id,
                incident_version=version,
                next_run_at=next_run_at,
                wait_reason=wait_reason,
                deadline=deadline,
                resume_metadata=resume_metadata or {},
            )
            logger.info(
                f"Persisted workflow schedule {schedule.id} for incident {incident_id} (reason: {wait_reason}) due at {next_run_at}"
            )
            return schedule

    async def fetch_due_schedules(self, limit: int = 50) -> list[WorkflowSchedule]:
        """Fetches pending schedules where next_run_at <= now()."""
        async with unit_of_work(self.session_factory, actor=self.actor) as repo:
            return await repo.fetch_due_workflow_schedules(limit=limit)

    async def claim_due_schedule(self, schedule_id: UUID) -> bool:
        """Atomically transitions status from PENDING to PROCESSED to prevent dual dispatch."""
        async with unit_of_work(self.session_factory, actor=self.actor) as repo:
            return await repo.mark_schedule_processed_atomic(schedule_id=schedule_id)

    async def cancel_incident_schedules(self, incident_id: UUID) -> None:
        """Cancels all pending schedules for an incident upon resolution or escalation."""
        async with unit_of_work(self.session_factory, actor=self.actor) as repo:
            await repo.cancel_pending_schedules(incident_id=incident_id)
