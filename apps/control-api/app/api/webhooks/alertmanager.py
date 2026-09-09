import logging
import secrets
from typing import Any

from app.core.config import get_settings
from app.db.repositories.control_plane import unit_of_work
from app.db.session import AsyncSessionLocal
from app.services.correlation.engine import CorrelationEngine
from app.services.ingestion.normalizer import EventNormalizer
from app.services.ingestion.resource_resolver import ResourceResolver
from app.services.ingestion.schemas import AlertManagerWebhookPayload
from app.workers.incidentpublisher import IncidentPublisher
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

logger = logging.getLogger(__name__)
router = APIRouter(prefix="", tags=["webhooks"])


def verify_alertmanager_secret(
    authorization: str | None = Header(None),
    x_webhook_secret: str | None = Header(None),
) -> None:
    settings = get_settings()
    expected_secret = settings.ALERTMANAGER_WEBHOOK_SECRET

    provided_secret = None
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            provided_secret = parts[1]
        elif len(parts) == 1:
            provided_secret = parts[0]
    elif x_webhook_secret:
        provided_secret = x_webhook_secret

    if not provided_secret or not secrets.compare_digest(provided_secret, expected_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing webhook secret",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post("/webhooks/alertmanager")
@router.post("/api/v1/webhooks/alertmanager")
async def alertmanager_webhook(
    payload: AlertManagerWebhookPayload,
    request: Request,
    _: None = Depends(verify_alertmanager_secret),
) -> dict[str, Any]:
    settings = get_settings()
    normalizer = EventNormalizer(dedup_window_seconds=settings.EVENT_DEDUP_WINDOW_SECONDS)
    publisher = IncidentPublisher()

    created_incidents: list[str] = []
    correlated_incidents: list[str] = []
    event_ids: list[str] = []

    if not payload.alerts:
        return {
            "status": "ok",
            "processed": 0,
            "event_ids": [],
            "created_incidents": [],
            "correlated_incidents": [],
        }

    try:
        # Implementation Detail #1: Iterate over batch alerts independently
        async with unit_of_work(AsyncSessionLocal, actor="webhook:alertmanager") as repo:
            resolver = ResourceResolver(repo)
            engine = CorrelationEngine(repo)

            for alert in payload.alerts:
                # 1. Resolve matching resource group
                resource = await resolver.resolve(
                    service=alert.labels.get("service") or payload.commonLabels.get("service"),
                    job=alert.labels.get("job") or payload.commonLabels.get("job"),
                    labels={**payload.commonLabels, **alert.labels},
                )

                # 2. Normalize and redact alert
                norm_event = normalizer.normalize_alertmanager_alert(
                    alert=alert,
                    resource_id=resource.id,
                    common_labels=payload.commonLabels,
                    common_annotations=payload.commonAnnotations,
                )

                # 3. Deduplicate and correlate (Implementation Detail #2: resolved alerts filtered from resurrecting incidents)
                res = await engine.ingest_event(norm_event)
                event_ids.append(str(res.event.id))

                if res.incident:
                    incident_id_str = str(res.incident.id)
                    if res.was_created:
                        created_incidents.append(incident_id_str)
                        # Implementation Detail #4: Publish incident created strictly when was_created is True
                        await publisher.publish_incident_created(
                            incident=res.incident,
                            event_ids=[res.event.id],
                        )
                    else:
                        correlated_incidents.append(incident_id_str)

    finally:
        await publisher.close()

    return {
        "status": "ok",
        "processed": len(payload.alerts),
        "event_ids": event_ids,
        "created_incidents": list(set(created_incidents)),
        "correlated_incidents": list(set(correlated_incidents)),
    }
