import logging
import secrets
from typing import Any

import redis.asyncio as aioredis
import sqlalchemy as sa
from app.core.config import get_settings
from app.db.repositories.control_plane import unit_of_work
from app.db.session import AsyncSessionLocal
from fastapi import APIRouter, Depends, Header, HTTPException, status

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


async def verify_system_secret(authorization: str | None = Header(None)) -> None:
    expected_secret = get_settings().SYSTEM_STATUS_SECRET
    provided_secret = None

    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            provided_secret = parts[1]

    if not provided_secret or not secrets.compare_digest(provided_secret, expected_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing system status authorization token",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.get("/system/status")
@router.get("/api/v1/system/status")
async def get_system_status(_: None = Depends(verify_system_secret)) -> dict[str, Any]:
    """Exposes authenticated system-status read model: worker heartbeats, outbox backlog, wakeups, and health."""
    settings = get_settings()

    db_connected = False
    redis_connected = False
    metrics: dict[str, Any] = {
        "worker_heartbeats": [],
        "outbox_backlog": 0,
        "oldest_pending_wakeup": None,
        "last_processing_error": None,
    }

    # 1. Query PostgreSQL metrics
    try:
        async with unit_of_work(AsyncSessionLocal, actor="api:system") as repo:
            metrics = await repo.get_system_status_metrics()
            db_connected = True
    except Exception as exc:
        logger.error(f"Failed to query database metrics in system status: {exc}")
        db_connected = False
        metrics["last_processing_error"] = str(exc)

    # 2. Check Redis connectivity
    client = None
    try:
        client = aioredis.from_url(str(settings.REDIS_URL), decode_responses=True)
        await client.ping()
        redis_connected = True
    except Exception as exc:
        logger.warning(f"Redis health check failed in system status: {exc}")
        redis_connected = False
    finally:
        if client:
            await client.aclose()

    return {
        "status": "ok" if (db_connected and redis_connected) else "degraded",
        "database_connected": db_connected,
        "redis_connected": redis_connected,
        **metrics,
    }
