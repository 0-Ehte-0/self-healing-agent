import asyncio
import logging
import os
import signal
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from demo_api.config import get_settings
from demo_api.db import JobRecord, get_db, init_db
from demo_api.logging import configure_logging, correlation_id_ctx, scenario_id_ctx
from demo_api.metrics import (
    HEALTH_LIVE_STATUS,
    HEALTH_READY_STATUS,
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    REDIS_CONNECTED,
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

HEALTH_LIVE_STATUS.set(1)
HEALTH_READY_STATUS.set(1)
REDIS_CONNECTED.set(1)


async def monitor_redis() -> None:
    """Measure connectivity independently of request paths and their injected hangs."""
    while True:
        try:
            await asyncio.wait_for(redis_client.ping(), timeout=1.0)
            REDIS_CONNECTED.set(1)
        except Exception:
            REDIS_CONNECTED.set(0)
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_db()
    HEALTH_LIVE_STATUS.set(1)
    HEALTH_READY_STATUS.set(1)
    REDIS_CONNECTED.set(1)
    monitor = asyncio.create_task(monitor_redis())
    try:
        yield
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
        await redis_client.aclose()


app = FastAPI(title="demo-api", lifespan=lifespan)
setup_telemetry(app, settings)


@app.middleware("http")
async def telemetry_middleware(request: Request, call_next):
    start_time = time.perf_counter()

    # 1. Resolve scenario_id first to prevent UnboundLocalError
    scenario_id = request.headers.get("X-Scenario-ID", "BASELINE")
    token = scenario_id_ctx.set(scenario_id)
    correlation_id = request.headers.get("X-Correlation-ID") or "none"
    correlation_token = correlation_id_ctx.set(correlation_id)

    # 2. Enrich active OpenTelemetry trace span
    current_span = trace.get_current_span()
    if current_span and current_span.is_recording():
        current_span.set_attribute("scenario_id", scenario_id)
        current_span.set_attribute("correlation_id", correlation_id)

    # 3. Handle simulated latency injection (SCN-004)
    try:
        latency_val = await asyncio.wait_for(redis_client.get("fault:latency"), timeout=1.0)
        if latency_val:
            await asyncio.sleep(float(latency_val))
    except Exception:
        pass

    response = None
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Correlation-ID"] = correlation_id
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
        logging.getLogger("demo-api").info(
            "%s %s status=%s duration=%.3f", request.method, path, status_code, duration
        )
        scenario_id_ctx.reset(token)
        correlation_id_ctx.reset(correlation_token)


@app.get("/health/live")
async def health_live(redis: Redis = Depends(get_redis)) -> dict[str, str]:
    if await redis.get("fault:bad_deployment"):
        HEALTH_LIVE_STATUS.set(0)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Deployment configuration invalid",
        )
    HEALTH_LIVE_STATUS.set(1)
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict[str, object]:
    if await redis.get("fault:bad_deployment"):
        HEALTH_READY_STATUS.set(0)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Deployment configuration invalid",
        )

    if await redis.get("fault:health_hang"):
        HEALTH_READY_STATUS.set(0)
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

    REDIS_CONNECTED.set(1 if redis_ok else 0)

    ready = db_ok and redis_ok
    HEALTH_READY_STATUS.set(1 if ready else 0)
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
    await redis.xadd(
        "demo:jobs",
        {
            "job_id": job_uuid,
            "payload": req.payload,
            "scenario_id": scenario_id_ctx.get(),
            "correlation_id": correlation_id_ctx.get(),
        },
    )

    return JobCreateResponse(job_id=job_uuid, status="QUEUED")


@app.post("/_faults/crash")
async def trigger_crash() -> None:
    os.kill(os.getpid(), signal.SIGTERM)
