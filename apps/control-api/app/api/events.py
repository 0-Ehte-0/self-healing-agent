import logging
import secrets
from typing import Any

from app.core.config import get_settings
from app.db.repositories.control_plane import unit_of_work
from app.db.session import AsyncSessionLocal
from app.services.correlation.engine import CorrelationEngine
from app.services.ingestion.normalizer import EventNormalizer
from app.services.ingestion.resource_resolver import ResourceResolver
from app.services.ingestion.schemas import GenericEventPayload
from app.workers.incidentpublisher import IncidentPublisher
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

logger = logging.getLogger(__name__)
router = APIRouter(prefix="", tags=["events"])


def verify_generic_secret(
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None),
) -> None:
    settings = get_settings()
    expected_secret = settings.GENERIC_EVENT_SECRET

    provided_secret = None
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            provided_secret = parts[1]
        elif len(parts) == 1:
            provided_secret = parts[0]
    elif x_api_key:
        provided_secret = x_api_key

    if not provided_secret or not secrets.compare_digest(provided_secret, expected_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing event API secret",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post("/events/generic")
@router.post("/api/v1/events/generic")
async def ingest_generic_event(
    payload: GenericEventPayload,
    request: Request,
    _: None = Depends(verify_generic_secret),
) -> dict[str, Any]:
    settings = get_settings()
    normalizer = EventNormalizer(dedup_window_seconds=settings.EVENT_DEDUP_WINDOW_SECONDS)
    publisher = IncidentPublisher()

    try:
        async with unit_of_work(AsyncSessionLocal, actor="api:generic") as repo:
            resolver = ResourceResolver(repo)
            engine = CorrelationEngine(repo)

            resource = await resolver.resolve(
                resource_id=payload.resource_id,
                external_id=payload.resource_external_id,
                name=payload.resource_name,
                service=payload.service,
                labels=payload.labels,
            )

            norm_event = normalizer.normalize_generic_event(payload, resource.id)
            res = await engine.ingest_event(norm_event)

            if res.incident and res.was_created:
                try:
                    await publisher.publish_incident_created(
                        incident=res.incident,
                        event_ids=[res.event.id],
                    )
                except Exception as exc:
                    logger.warning(
                        f"Immediate Redis stream publish failed for incident {res.incident.id}: {exc}. "
                        "Incident is safely persisted in transactional outbox for background delivery."
                    )

            return {
                "status": "ok",
                "event_id": str(res.event.id),
                "incident_id": str(res.incident.id) if res.incident else None,
                "was_created": res.was_created,
                "action": res.action,
            }
    finally:
        await publisher.close()
