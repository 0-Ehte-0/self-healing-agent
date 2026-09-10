import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from agentcore.runtime.runner import WorkflowRunner
from agentcore.runtime.scheduler import WorkflowScheduler
from app.db.models import Incident, WorkflowLease, WorkflowSchedule
from app.db.repositories.control_plane import unit_of_work
from sharedmodels.enums import IncidentState
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


class WorkflowRecoveryService:
    """Periodic background sweeper recovering expired leases, due schedules, and unhandled incidents.

    Per Section 6 Step 10 & Gap 5: When recovery identifies due schedules (next_run_at <= now()),
    it atomically updates the schedule status to PROCESSED and dispatches execution to prevent
    dual dispatch across multiple worker instances.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        runner: WorkflowRunner,
        interval_seconds: float = 3.0,
    ):
        self.session_factory = session_factory
        self.runner = runner
        self.interval_seconds = interval_seconds
        self.scheduler = WorkflowScheduler(session_factory, actor="worker:recovery")
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("WorkflowRecoveryService started.")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("WorkflowRecoveryService stopped.")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.recover_due_schedules()
                await self.cleanup_expired_leases()
                await self.recover_unprocessed_incidents()
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Error in recovery loop: {exc}", exc_info=True)
                await asyncio.sleep(self.interval_seconds)

    async def recover_due_schedules(self) -> int:
        """Finds due schedules, atomically claims them, and resumes the workflow."""
        due_schedules = await self.scheduler.fetch_due_schedules(limit=25)
        dispatched = 0

        for schedule in due_schedules:
            # Atomic transition to PROCESSED (Gap 5)
            claimed = await self.scheduler.claim_due_schedule(schedule.id)
            if not claimed:
                # Another worker claimed it concurrently
                continue

            logger.info(
                f"Recovery claimed due schedule {schedule.id} for incident {schedule.incident_id} (reason: {schedule.wait_reason})"
            )
            try:
                await self.runner.run_incident(
                    incident_id=schedule.incident_id,
                    expected_version=schedule.incident_version,
                    resume_metadata=schedule.resume_metadata,
                )
                dispatched += 1
            except Exception as exc:
                logger.error(
                    f"Error resuming workflow from schedule {schedule.id}: {exc}",
                    exc_info=True,
                )

        return dispatched

    async def cleanup_expired_leases(self) -> int:
        """Removes expired leases so other workers can acquire ownership."""
        async with unit_of_work(self.session_factory, actor="worker:recovery") as repo:
            stmt = sa.delete(WorkflowLease).where(WorkflowLease.expires_at <= sa.func.now())
            conn = await repo.session.connection()
            res = await conn.execute(stmt)
            return res.rowcount or 0

    async def recover_unprocessed_incidents(self) -> int:
        """Finds incidents stuck in non-terminal states with no active lease and no pending schedule."""
        async with unit_of_work(self.session_factory, actor="worker:recovery") as repo:
            # Non-terminal states including executing and verifying
            active_states = [
                IncidentState.DETECTED,
                IncidentState.TRIAGED,
                IncidentState.DIAGNOSED,
                IncidentState.PLANNED,
                IncidentState.APPROVED,
                IncidentState.EXECUTING,
                IncidentState.VERIFYING,
            ]
            # Incidents without active leases or pending future schedules (e.g. cooldown wait), older than 10s
            stmt = (
                sa.select(Incident)
                .outerjoin(WorkflowLease, WorkflowLease.incident_id == Incident.id)
                .outerjoin(
                    WorkflowSchedule,
                    sa.and_(
                        WorkflowSchedule.incident_id == Incident.id,
                        WorkflowSchedule.status == "PENDING",
                        WorkflowSchedule.next_run_at > sa.func.now(),
                    ),
                )
                .where(
                    Incident.state.in_(active_states),
                    WorkflowLease.incident_id.is_(None),
                    WorkflowSchedule.id.is_(None),
                    Incident.updated_at <= sa.func.now() - sa.text("interval '10 seconds'"),
                )
                .order_by(Incident.created_at.asc())
                .limit(10)
            )
            stuck_incidents = list((await repo.session.scalars(stmt)).all())

        dispatched = 0
        for incident in stuck_incidents:
            try:
                logger.info(
                    f"Recovery dispatching stuck incident {incident.id} in state {incident.state}"
                )
                await self.runner.run_incident(
                    incident_id=incident.id,
                    expected_version=incident.version,
                )
                dispatched += 1
            except Exception as exc:
                logger.warning(f"Failed to recover stuck incident {incident.id}: {exc}")

        return dispatched
