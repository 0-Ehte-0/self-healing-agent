import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from app.api.health import router as health_router
from app.core.config import get_settings
from app.core.errors import AppError, app_error_handler
from app.core.logging import CorrelationIdMiddleware, setup_logging
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
    # Startup validation and initializations
    yield
    # Shutdown / teardown connections


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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(health_router, prefix="")


@app.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/api/v1/webhooks/alertmanager")
async def alertmanager_webhook(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, str]:
    alerts = payload.get("alerts", [])
    for alert in alerts:
        labels = alert.get("labels", {})
        ALERT_NOTIFICATIONS.labels(
            labels.get("alertname", "unknown"),
            labels.get("scenario_id", "unknown"),
            alert.get("status", payload.get("status", "unknown")),
        ).inc()
    logger.info(
        "Received Alertmanager webhook notification",
        extra={"status": payload.get("status"), "alerts_count": len(alerts)},
    )
    return {"status": "ok"}


def start():
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8088, reload=True)


if __name__ == "__main__":
    start()
