import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from app.db.models import AutomationControl, ResourceLock
from app.db.repositories.control_plane import ConflictError, ControlPlaneRepository
from sqlalchemy.dialects.postgresql import insert

logger = logging.getLogger(__name__)


class ResourceLockedError(ConflictError):
    """Raised when resource is currently locked by another worker."""


class QuarantinedResourceError(ConflictError):
    """Raised when resource is quarantined or emergency-stopped."""


class LockOwnershipError(ConflictError):
    """Raised when lock token or owner does not match."""


class ResourceLockService:
    """Manages distributed resource leases, safety boundaries, and quarantine enforcement."""

    def __init__(self, repo: ControlPlaneRepository):
        self.repo = repo
        self.session = repo.session
        self.actor = repo.actor

    async def check_safety(self, resource_id: UUID) -> None:
        """Verifies resource is not emergency-stopped or quarantined."""
        # 1. Check global automation emergency stop list
        control = await self.session.get(AutomationControl, "global")
        if control:
            if control.mode == "DISABLED":
                raise QuarantinedResourceError("Global automation is currently DISABLED")
            res_str = str(resource_id)
            if res_str in (control.emergency_stopped_resources or []):
                raise QuarantinedResourceError(
                    f"Resource {resource_id} is on the emergency stop list"
                )

        # 2. Check resource lock quarantine status
        lock = await self.session.get(ResourceLock, resource_id)
        if lock and lock.quarantined:
            raise QuarantinedResourceError(
                f"Resource {resource_id} is quarantined: {lock.quarantine_reason}"
            )

    async def acquire_lock(self, resource_id: UUID, seconds: int = 120) -> UUID:
        """Acquires exclusive lease for resource_id if not locked or quarantined."""
        if not 1 <= seconds <= 3600:
            raise ValueError("Lock duration must be between 1 and 3600 seconds")

        # Check safety/quarantine first
        await self.check_safety(resource_id)

        token = uuid4()
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=seconds)

        # Attempt atomic upsert where expired or unowned
        stmt = insert(ResourceLock).values(
            resource_id=resource_id,
            owner=self.actor,
            token=token,
            acquired_at=sa.func.now(),
            expires_at=sa.func.now() + timedelta(seconds=seconds),
            quarantined=False,
            quarantine_reason=None,
            quarantined_at=None,
        )

        update_dict = {
            "owner": self.actor,
            "token": token,
            "acquired_at": sa.func.now(),
            "expires_at": stmt.excluded.expires_at,
        }

        # Can only acquire if not quarantined AND (lock expired OR owned by self)
        result = await self.session.scalar(
            stmt.on_conflict_do_update(
                index_elements=[ResourceLock.resource_id],
                set_=update_dict,
                where=(ResourceLock.quarantined == False)  # noqa: E712
                & ((ResourceLock.expires_at <= sa.func.now()) | (ResourceLock.owner == self.actor)),
            ).returning(ResourceLock.token)
        )

        if result is None:
            # Check why it failed for precise error message
            lock = await self.session.get(ResourceLock, resource_id)
            if lock and lock.quarantined:
                raise QuarantinedResourceError(
                    f"Resource {resource_id} is quarantined: {lock.quarantine_reason}"
                )
            raise ResourceLockedError(
                f"Resource {resource_id} is currently locked by another worker ({lock.owner if lock else 'unknown'})"
            )

        await self.session.flush()
        return result

    async def verify_lock(self, resource_id: UUID, token: UUID) -> bool:
        """Verifies the caller holds an active, unexpired lock on resource_id."""
        lock = await self.session.get(ResourceLock, resource_id)
        if lock is None or lock.token != token:
            return False
        if lock.owner != self.actor:
            return False
        now_dt = (
            datetime.now(lock.expires_at.tzinfo) if lock.expires_at.tzinfo else datetime.now(UTC)
        )
        if lock.expires_at <= now_dt:
            return False
        if lock.quarantined:
            return False
        return True

    async def renew_lock(self, resource_id: UUID, token: UUID, seconds: int = 120) -> bool:
        """Extends lock expiry if caller still owns the active lock."""
        result = await self.session.scalar(
            sa.update(ResourceLock)
            .where(
                ResourceLock.resource_id == resource_id,
                ResourceLock.token == token,
                ResourceLock.owner == self.actor,
                ResourceLock.expires_at > sa.func.now(),
                ResourceLock.quarantined == False,  # noqa: E712
            )
            .values(expires_at=sa.func.now() + timedelta(seconds=seconds))
            .returning(ResourceLock.resource_id)
        )
        return result is not None

    async def release_lock(self, resource_id: UUID, token: UUID) -> None:
        """Releases the held lock, ensuring an expired worker cannot release another worker's lock."""
        lock = await self.session.get(ResourceLock, resource_id)
        if lock is None:
            return
        if lock.token != token or lock.owner != self.actor:
            raise LockOwnershipError("Lock token or owner does not match current holder")

        if lock.quarantined:
            # If quarantined, do not delete the record; simply clear token/owner
            lock.token = uuid4()
            lock.owner = "system:quarantine"
            lock.expires_at = datetime.now(UTC) + timedelta(days=365)
        else:
            await self.session.delete(lock)
        await self.session.flush()

    async def quarantine_resource(self, resource_id: UUID, reason: str) -> None:
        """Quarantines resource after uncertain execution or manual escalation."""
        now = datetime.now(UTC)
        stmt = insert(ResourceLock).values(
            resource_id=resource_id,
            owner="system:quarantine",
            token=uuid4(),
            acquired_at=sa.func.now(),
            expires_at=sa.func.now() + timedelta(days=365),
            quarantined=True,
            quarantine_reason=reason,
            quarantined_at=sa.func.now(),
        )
        await self.session.execute(
            stmt.on_conflict_do_update(
                index_elements=[ResourceLock.resource_id],
                set_={
                    "owner": "system:quarantine",
                    "quarantined": True,
                    "quarantine_reason": reason,
                    "quarantined_at": sa.func.now(),
                    "expires_at": sa.func.now() + timedelta(days=365),
                },
            )
        )
        await self.session.flush()
        logger.warning("Resource %s quarantined: %s", resource_id, reason)

    async def clear_quarantine(self, resource_id: UUID) -> None:
        """Clears quarantine flag for resource_id."""
        lock = await self.session.get(ResourceLock, resource_id)
        if lock and lock.quarantined:
            await self.session.delete(lock)
            await self.session.flush()
            logger.info("Resource %s quarantine cleared by %s", resource_id, self.actor)
