import asyncio
import os
import signal
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from demo_api.config import get_settings
from demo_api.db import JobRecord, get_db, init_db
from demo_api.logging import configure_logging, scenario_id_ctx
from demo_api.metrics import (
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
)
from demo_api.redis import get_redis, redis_client
from demo_api.telemetry import setup_telemetry
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from opentelemetry import trace
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

settings = get_settings()
configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_db()
    yield
    await redis_client.aclose()


app = FastAPI(title="demo-api", lifespan=lifespan)
setup_telemetry(app, settings)


@app.middleware("http")
async def telemetry_middleware(request: Request, call_next):
    start_time = time.perf_counter()

    # 1. Resolve scenario_id first to prevent UnboundLocalError
    scenario_id = request.headers.get("X-Scenario-ID", "")
    token = scenario_id_ctx.set(scenario_id)

    # 2. Enrich active OpenTelemetry trace span
    current_span = trace.get_current_span()
    if current_span and current_span.is_recording():
        current_span.set_attribute("scenario_id", scenario_id)

    # 3. Handle simulated latency injection (SCN-004)
    try:
        latency_val = await redis_client.get("fault:latency")
        if latency_val:
            await asyncio.sleep(float(latency_val))
    except Exception:
        pass

    response = None
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        if scenario_id:
            response.headers["X-Scenario-ID"] = scenario_id
        return response
    except Exception:
        raise
    finally:
        duration = time.perf_counter() - start_time
        path = request.url.path
        HTTP_REQUESTS_TOTAL.labels(
            method=request.method,
            endpoint=path,
            status=str(status_code),
            scenario_id=scenario_id,
        ).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(
            method=request.method,
            endpoint=path,
            scenario_id=scenario_id,
        ).observe(duration)
        scenario_id_ctx.reset(token)


@app.get("/health/live")
async def health_live(redis: Redis = Depends(get_redis)) -> dict[str, str]:
    if await redis.get("fault:bad_deployment"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Deployment configuration invalid",
        )
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict[str, object]:
    if await redis.get("fault:bad_deployment"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Deployment configuration invalid",
        )

    if await redis.get("fault:health_hang"):
        await asyncio.sleep(60.0)

    db_ok = False
    redis_ok = False
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    try:
        await redis.ping()
        redis_ok = True
    except Exception:
        redis_ok = False

    ready = db_ok and redis_ok
    if not ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"ready": False, "database": db_ok, "redis": redis_ok},
        )
    return {"ready": True, "database": "ok", "redis": "ok"}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


class JobCreateRequest(BaseModel):
    payload: str


class JobCreateResponse(BaseModel):
    job_id: str
    status: str


@app.post("/jobs", response_model=JobCreateResponse)
async def create_job(
    req: JobCreateRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> JobCreateResponse:
    if await redis.get("fault:error_rate"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Simulated elevated business error",
        )

    job_uuid = str(uuid.uuid4())
    job_record = JobRecord(job_id=job_uuid, payload=req.payload, status="QUEUED")

    # Persistent storage in PostgreSQL
    db.add(job_record)
    await db.commit()

    # Stream dispatch to Redis
    await redis.xadd("demo:jobs", {"job_id": job_uuid, "payload": req.payload})

    return JobCreateResponse(job_id=job_uuid, status="QUEUED")


@app.post("/_faults/crash")
async def trigger_crash() -> None:
    os.kill(os.getpid(), signal.SIGTERM)
