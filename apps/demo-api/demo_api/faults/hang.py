import asyncio
import logging
import time

logger = logging.getLogger(__name__)


class ProcessHangManager:
    """Manages process-local in-memory hang state for SCN-003."""

    def __init__(self) -> None:
        self._hang_active: bool = False
        self._injected_at: float | None = None
        self._pending_tasks: set[asyncio.Task] = set()

    def is_hung(self) -> bool:
        return self._hang_active

    def get_status(self) -> dict:
        return {
            "active": self._hang_active,
            "injected_at": self._injected_at,
            "blocked_requests": len(self._pending_tasks),
        }

    def inject(self) -> dict:
        self._hang_active = True
        self._injected_at = time.time()
        logger.warning("Process-local hang injected into demo-api (affecting /health/ready)")
        return {"status": "injected", **self.get_status()}

    def clear(self) -> dict:
        self._hang_active = False
        self._injected_at = None
        cancelled_count = 0
        for task in list(self._pending_tasks):
            if not task.done():
                task.cancel()
                cancelled_count += 1
        self._pending_tasks.clear()
        logger.info(f"Cleared process-local hang; cancelled {cancelled_count} blocked sleep tasks")
        return {"status": "cleared", "cancelled_tasks": cancelled_count}

    async def wait_if_hung(self, duration_seconds: float = 60.0) -> None:
        """Blocks execution if hang is active. Can be cancelled cleanly on clear or restart."""
        if not self._hang_active:
            return

        current_task = asyncio.current_task()
        if current_task:
            self._pending_tasks.add(current_task)

        try:
            await asyncio.sleep(duration_seconds)
        except asyncio.CancelledError:
            logger.debug("Blocked sleep task cancelled by clear/shutdown")
            raise
        finally:
            if current_task and current_task in self._pending_tasks:
                self._pending_tasks.remove(current_task)


hang_manager = ProcessHangManager()
