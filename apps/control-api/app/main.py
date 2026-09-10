import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from app.api.approvals import router as approvals_router
from app.api.auth import router as auth_router
from app.api.automation import router as automation_router
from app.api.events import router as events_router
from app.api.health import router as health_router
from app.api.system import router as system_router
from app.api.webhooks.alertmanager import router as alertmanager_router
from app.core.config import get_settings
from app.core.errors import AppError, app_error_handler
from app.core.logging import CorrelationIdMiddleware, setup_logging
from app.db.session import AsyncSessionLocal
from app.workers.outboxpublisher import OutboxPublisherWorker
from fastapi import Body, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

logger = logging.getLogger(__name__)
settings = get_settings()
setup_logging(settings.LOG_LEVEL)
ALERT_NOTIFICATIONS = Counter(
    "control_plane_alert_notifications_total",
    "Received alert statuses",
    ["alertname", "scenario_id", "status"],
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup validation and initializations: start OutboxPublisherWorker
    outbox_worker = OutboxPublisherWorker(AsyncSessionLocal)
    await outbox_worker.start()
    yield
    # Shutdown / teardown connections
    await outbox_worker.stop()


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

# Exception handlers
app.add_exception_handler(AppError, app_error_handler)  # type: ignore

# Middlewares
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS or ["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(health_router, prefix="")
app.include_router(alertmanager_router, prefix="")
app.include_router(events_router, prefix="")
app.include_router(system_router, prefix="")
app.include_router(auth_router, prefix="")
app.include_router(approvals_router, prefix="")
app.include_router(automation_router, prefix="")


@app.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def start():
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8088, reload=True)


if __name__ == "__main__":
    start()
