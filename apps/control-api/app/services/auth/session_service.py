from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

from app.core.security import generate_csrf_token, generate_session_token
from app.db.models import UserSession
from app.db.repositories.control_plane import ControlPlaneRepository


class SessionService:
    DEFAULT_SESSION_HOURS: int = 8

    @classmethod
    async def create_user_session(
        cls,
        repo: ControlPlaneRepository,
        user_id: UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
        ttl_hours: int | None = None,
    ) -> tuple[UserSession, str, str]:
        hours = ttl_hours or cls.DEFAULT_SESSION_HOURS
        session_token = generate_session_token()
        csrf_token = generate_csrf_token()
        expires_at = datetime.now(UTC) + timedelta(hours=hours)

        session = await repo.create_session(
            user_id=user_id,
            session_token=session_token,
            csrf_token=csrf_token,
            expires_at=expires_at,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return session, session_token, csrf_token

    @classmethod
    async def validate_session(
        cls,
        repo: ControlPlaneRepository,
        session_token: str,
    ) -> UserSession | None:
        session = await repo.get_session(session_token)
        if session is None or session.is_revoked:
            return None

        now = datetime.now(UTC)
        expires_at = session.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)

        if expires_at <= now:
            return None

        # Update last accessed
        session.last_accessed_at = now
        return session

    @classmethod
    async def revoke_session(
        cls,
        repo: ControlPlaneRepository,
        session_token: str,
    ) -> None:
        await repo.revoke_session(session_token)
