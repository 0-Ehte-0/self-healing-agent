import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

from app.db.repositories.control_plane import ConflictError, unit_of_work
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


class IncidentLeaseManager:
    """Manages worker ownership of incidents during workflow processing.

    Per Section 6 Step 7: Acquire an incident-processing lease. Recheck lease ownership
    and incident version at each durable boundary. Resource mutation locks remain a separate control.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        worker_id: str,
        ttl_seconds: int = 30,
    ):
        self.session_factory = session_factory
        self.worker_id = worker_id
        self.ttl_seconds = ttl_seconds

    async def acquire(self, incident_id: UUID, expected_version: int | None = None) -> UUID:
        """Acquires a lease token for the incident. Raises ConflictError if leased elsewhere."""
        async with unit_of_work(self.session_factory, actor=self.worker_id) as repo:
            return await repo.acquire_incident_lease(
                incident_id=incident_id,
                owner=self.worker_id,
                ttl_seconds=self.ttl_seconds,
                version=expected_version,
            )

    async def verify(
        self, incident_id: UUID, token: UUID, expected_version: int | None = None
    ) -> bool:
        """Verifies active lease ownership and incident version at durable boundaries."""
        async with unit_of_work(self.session_factory, actor=self.worker_id) as repo:
            return await repo.verify_incident_lease(
                incident_id=incident_id,
                token=token,
                expected_version=expected_version,
            )

    async def renew(self, incident_id: UUID, token: UUID) -> bool:
        """Extends the lease expiry by ttl_seconds."""
        async with unit_of_work(self.session_factory, actor=self.worker_id) as repo:
            return await repo.renew_incident_lease(
                incident_id=incident_id,
                token=token,
                ttl_seconds=self.ttl_seconds,
            )

    async def release(self, incident_id: UUID, token: UUID) -> None:
        """Releases the lease immediately."""
        try:
            async with unit_of_work(self.session_factory, actor=self.worker_id) as repo:
                await repo.release_incident_lease(incident_id=incident_id, token=token)
        except Exception as exc:
            logger.warning(f"Error releasing lease for incident {incident_id}: {exc}")

    @asynccontextmanager
    async def hold(
        self, incident_id: UUID, expected_version: int | None = None
    ) -> AsyncGenerator[UUID, None]:
        """Context manager that acquires lease, runs heartbeat renewal, and releases on completion."""
        token = await self.acquire(incident_id, expected_version=expected_version)
        stop_heartbeat = asyncio.Event()

        async def _renew_loop():
            interval = max(1.0, self.ttl_seconds / 3.0)
            while not stop_heartbeat.is_set():
                try:
                    await asyncio.sleep(interval)
                    if stop_heartbeat.is_set():
                        break
                    success = await self.renew(incident_id, token)
                    if not success:
                        logger.error(
                            f"Failed to renew lease for incident {incident_id}; lease lost."
                        )
                        break
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.warning(
                        f"Error in lease renewal heartbeat for incident {incident_id}: {exc}"
                    )

        renew_task = asyncio.create_task(_renew_loop())
        try:
            yield token
        finally:
            stop_heartbeat.set()
            renew_task.cancel()
            try:
                await renew_task
            except asyncio.CancelledError:
                pass
            await self.release(incident_id, token)
