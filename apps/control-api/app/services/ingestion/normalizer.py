import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.services.ingestion.schemas import (
    AlertItem,
    GenericEventPayload,
    NormalizedEventPayload,
)
from app.services.redaction.redactor import redact_sensitive_data
from sharedmodels.enums import EventSource, Severity


def map_severity(raw_severity: str | None) -> Severity:
    if not raw_severity:
        return Severity.MEDIUM
    # pyrefly: ignore [unnecessary-type-conversion]
    clean = str(raw_severity).strip().lower()
    if clean in ("critical", "fatal", "page"):
        return Severity.CRITICAL
    elif clean in ("high", "error"):
        return Severity.HIGH
    elif clean in ("warning", "warn", "medium"):
        return Severity.MEDIUM
    elif clean in ("info", "low", "notice", "debug", "none"):
        return Severity.LOW
    return Severity.MEDIUM


def compute_dedup_window(dt: datetime, window_seconds: int = 60) -> datetime:
    """Rounds the datetime down to the current sliding window bucket."""
    epoch = int(dt.timestamp())
    bucket_epoch = (epoch // window_seconds) * window_seconds
    return datetime.fromtimestamp(bucket_epoch, tz=UTC)


def compute_fingerprint(
    source: EventSource,
    resource_id: UUID,
    event_type: str,
    scenario_id: str | None,
    identifying_keys: dict[str, Any] | None = None,
) -> str:
    """Generates a deterministic 64-character lowercase SHA-256 fingerprint."""
    ident_str = ""
    if identifying_keys:
        ident_str = json.dumps(identifying_keys, sort_keys=True, default=str)
    canonical = f"{source.value}:{str(resource_id)}:{event_type}:{scenario_id or ''}:{ident_str}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class EventNormalizer:
    def __init__(self, dedup_window_seconds: int = 60):
        self.dedup_window_seconds = dedup_window_seconds

    def normalize_alertmanager_alert(
        self,
        alert: AlertItem,
        resource_id: UUID,
        common_labels: dict[str, Any] | None = None,
        common_annotations: dict[str, Any] | None = None,
    ) -> NormalizedEventPayload:
        labels = {**(common_labels or {}), **alert.labels}
        annotations = {**(common_annotations or {}), **alert.annotations}

        event_type = labels.get("alertname", "UnknownAlert")
        scenario_id = labels.get("scenario_id")
        service = labels.get("service") or labels.get("job")
        severity = map_severity(labels.get("severity"))

        occurred_at = alert.startsAt or datetime.now(UTC)
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)

        dedup_window = compute_dedup_window(occurred_at, self.dedup_window_seconds)

        # Distinguishing identity keys for fingerprinting
        identifying_keys = {
            "alertname": event_type,
            "scenario_id": scenario_id,
            "service": service,
            "instance": labels.get("instance"),
            "fingerprint_hint": alert.fingerprint,
        }
        fingerprint = compute_fingerprint(
            EventSource.ALERTMANAGER,
            resource_id,
            event_type,
            scenario_id,
            identifying_keys,
        )

        message = (
            annotations.get("summary")
            or annotations.get("description")
            or f"Alert {event_type} status {alert.status}"
        )

        # Build normalized payload
        payload_data = {
            "alertname": event_type,
            "status": alert.status,
            "message": message,
            "scenario_id": scenario_id,
            "service": service,
            "labels": labels,
            "annotations": annotations,
            "generatorURL": alert.generatorURL,
        }

        raw_data = {
            "status": alert.status,
            "labels": alert.labels,
            "annotations": alert.annotations,
            "startsAt": alert.startsAt.isoformat() if alert.startsAt else None,
            "endsAt": alert.endsAt.isoformat() if alert.endsAt else None,
            "generatorURL": alert.generatorURL,
            "fingerprint": alert.fingerprint,
        }

        # Redact sensitive data at ingestion
        redacted_payload = redact_sensitive_data(payload_data)
        redacted_raw = redact_sensitive_data(raw_data)

        return NormalizedEventPayload(
            source=EventSource.ALERTMANAGER,
            event_type=event_type,
            severity=severity,
            resource_id=resource_id,
            occurred_at=occurred_at,
            fingerprint=fingerprint,
            dedup_window=dedup_window,
            payload=redacted_payload,
            raw_payload=redacted_raw,
            status=alert.status.lower(),
            scenario_id=scenario_id,
            service=service,
            actor="webhook:alertmanager",
        )

    def normalize_generic_event(
        self,
        event: GenericEventPayload,
        resource_id: UUID,
    ) -> NormalizedEventPayload:
        occurred_at = event.occurred_at or datetime.now(UTC)
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)

        dedup_window = compute_dedup_window(occurred_at, self.dedup_window_seconds)

        identifying_keys = {
            "event_type": event.event_type,
            "scenario_id": event.scenario_id,
            "service": event.service,
            "labels": event.labels,
        }
        fingerprint = compute_fingerprint(
            event.source,
            resource_id,
            event.event_type,
            event.scenario_id,
            identifying_keys,
        )

        message = event.message or f"Generic event {event.event_type}"
        payload_data = {
            "event_type": event.event_type,
            "message": message,
            "scenario_id": event.scenario_id,
            "service": event.service,
            "details": event.details,
            "labels": event.labels,
            "annotations": event.annotations,
        }
        raw_data = event.model_dump(mode="json")

        redacted_payload = redact_sensitive_data(payload_data)
        redacted_raw = redact_sensitive_data(raw_data)

        return NormalizedEventPayload(
            source=event.source,
            event_type=event.event_type,
            severity=event.severity,
            resource_id=resource_id,
            occurred_at=occurred_at,
            fingerprint=fingerprint,
            dedup_window=dedup_window,
            payload=redacted_payload,
            raw_payload=redacted_raw,
            status="firing",
            scenario_id=event.scenario_id,
            service=event.service,
            actor="api:generic",
        )
