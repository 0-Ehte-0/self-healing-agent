import httpx
import redis.asyncio as aioredis
from app.core.config import Settings, get_settings
from app.db.session import get_db
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(tags=["Health"])


@router.get("/health/live", status_code=status.HTTP_200_OK)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    checks: dict[str, str] = {}
    is_ready = True

    # 1. Database Check
    try:
        await db.execute(text("SELECT 1"))
        checks["postgres"] = "healthy"
    except Exception as e:
        checks["postgres"] = f"unhealthy: {e!s}"
        is_ready = False

    # 2. Redis Check
    try:
        redis_client = aioredis.from_url(str(settings.REDIS_URL))
        await redis_client.ping()
        await redis_client.aclose()
        checks["redis"] = "healthy"
    except Exception as e:
        checks["redis"] = f"unhealthy: {e!s}"
        is_ready = False

    # 3. ChromaDB Check
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            chroma_url = f"http://{settings.CHROMA_HOST}:{settings.CHROMA_PORT}/api/v1/heartbeat"
            resp = await client.get(chroma_url)
            if resp.status_code == 200:
                checks["chromadb"] = "healthy"
            else:
                checks["chromadb"] = f"unhealthy: status {resp.status_code}"
                is_ready = False
    except Exception as e:
        checks["chromadb"] = f"unhealthy: {e!s}"
        is_ready = False

    http_status = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(
        status_code=http_status,
        content={"ready": is_ready, "components": checks},
    )
