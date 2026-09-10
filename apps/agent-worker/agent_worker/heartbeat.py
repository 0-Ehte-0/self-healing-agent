import asyncio
import logging
from typing import Any

from app.db.repositories.control_plane import unit_of_work
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


class WorkerHeartbeatPublisher:
    """Publishes periodic liveness heartbeats to PostgreSQL worker_heartbeats table.

    Per Section 6 Step 13: Expose worker heartbeat, oldest pending wake-up, outbox backlog,
    and last processing error through an authenticated system-status read model.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        worker_id: str,
        interval_seconds: float = 5.0,
        metadata: dict[str, Any] | None = None,
    ):
        self.session_factory = session_factory
        self.worker_id = worker_id
        self.interval_seconds = interval_seconds
        self.metadata = metadata or {"version": "0.1.0"}
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"WorkerHeartbeatPublisher started for {self.worker_id}")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info(f"WorkerHeartbeatPublisher stopped for {self.worker_id}")

    async def _loop(self) -> None:
        while self._running:
            try:
                async with unit_of_work(self.session_factory, actor=self.worker_id) as repo:
                    await repo.record_worker_heartbeat(
                        worker_id=self.worker_id,
                        status="HEALTHY",
                        metadata=self.metadata,
                    )
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"Failed to record worker heartbeat: {exc}")
                await asyncio.sleep(self.interval_seconds)
