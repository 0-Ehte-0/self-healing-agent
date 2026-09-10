import logging
from dataclasses import dataclass

from app.db.models import Event, Incident
from app.db.repositories.control_plane import ControlPlaneRepository
from app.services.ingestion.schemas import NormalizedEventPayload

logger = logging.getLogger(__name__)


@dataclass
class IngestionResult:
    event: Event
    incident: Incident | None
    was_created: bool
    is_duplicate_event: bool
    action: str


class CorrelationEngine:
    def __init__(self, repo: ControlPlaneRepository):
        self.repo = repo

    def compute_correlation_key(self, event: NormalizedEventPayload) -> str:
        """Determines the active incident grouping key for the failure scope."""
        if event.scenario_id:
            return f"{event.resource_id}:{event.scenario_id}"
        return f"{event.resource_id}:{event.event_type}"

    async def ingest_event(
        self,
        event_payload: NormalizedEventPayload,
    ) -> IngestionResult:
        # 1. Store event atomically; repo.store_event deduplicates on (source, resource_id, fingerprint, dedup_window)
        event_record = await self.repo.store_event(
            resource_id=event_payload.resource_id,
            source=event_payload.source,
            fingerprint=event_payload.fingerprint,
            dedup_window=event_payload.dedup_window,
            occurred_at=event_payload.occurred_at,
            severity=event_payload.severity,
            payload=event_payload.payload,
            raw_payload=event_payload.raw_payload,
        )

        correlation_key = self.compute_correlation_key(event_payload)

        # 2. Check if this is a resolved notification
        if event_payload.status != "firing":
            # Resolved notifications must NOT resurrect an incident in DETECTED.
            active_incident = await self.repo.get_active_incident_by_correlation_key(
                correlation_key
            )
            if active_incident:
                await self.repo.link_event(active_incident.id, event_record.id)
                return IngestionResult(
                    event=event_record,
                    incident=active_incident,
                    was_created=False,
                    is_duplicate_event=False,
                    action="resolved_linked",
                )
            return IngestionResult(
                event=event_record,
                incident=None,
                was_created=False,
                is_duplicate_event=False,
                action="resolved_ignored",
            )

        # 3. For firing alerts/events: Correlate to active incident or initialize new incident in DETECTED
        incident, was_created = await self.repo.get_or_create_incident(
            resource_id=event_payload.resource_id,
            correlation_key=correlation_key,
            severity=event_payload.severity,
            approval_required=False,
        )

        # 4. Idempotently link event to incident
        await self.repo.link_event(incident.id, event_record.id)

        if was_created:
            state_str = (
                incident.state.value if hasattr(incident.state, "value") else str(incident.state)
            )
            sev_str = (
                incident.severity.value
                if hasattr(incident.severity, "value")
                else str(incident.severity)
            )
            await self.repo.add_outbox_event(
                event_type="incident.detected",
                aggregate_type="incident",
                aggregate_id=incident.id,
                aggregate_version=incident.version,
                payload={
                    "event_type": "incident.detected",
                    "incident_id": str(incident.id),
                    "resource_id": str(incident.resource_id),
                    "correlation_key": incident.correlation_key,
                    "state": state_str,
                    "severity": sev_str,
                    "version": incident.version,
                    "event_ids": [str(event_record.id)],
                },
            )

        action = "incident_created" if was_created else "incident_correlated"
        logger.info(
            f"Event {event_record.id} ({event_payload.event_type}) -> Incident {incident.id} "
            f"({action}, correlation_key={correlation_key})"
        )

        return IngestionResult(
            event=event_record,
            incident=incident,
            was_created=was_created,
            is_duplicate_event=False,
            action=action,
        )
