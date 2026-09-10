import asyncio
import logging

from app.services.outbox.service import OutboxService
from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


class OutboxPublisherWorker:
    """Background worker that continuously sweeps and publishes pending outbox events to Redis."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        poll_interval: float = 1.0,
        batch_size: int = 50,
    ):
        self.session_factory = session_factory
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self.outbox_service = OutboxService(session_factory)
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Starts the background publisher task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("OutboxPublisherWorker started.")

    async def stop(self) -> None:
        """Stops the background publisher task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.outbox_service.publisher.close()
        logger.info("OutboxPublisherWorker stopped.")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                count = await self.outbox_service.sweep_and_publish(batch_size=self.batch_size)
                if count == 0:
                    await asyncio.sleep(self.poll_interval)
                else:
                    # If we processed a full batch, yield control and check again immediately
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Error in OutboxPublisherWorker loop: {exc}", exc_info=True)
                await asyncio.sleep(self.poll_interval)
