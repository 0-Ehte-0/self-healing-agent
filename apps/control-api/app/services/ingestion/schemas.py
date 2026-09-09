from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sharedmodels.enums import EventSource, Severity


class AlertLabel(BaseModel):
    model_config = ConfigDict(extra="allow")
    alertname: str
    severity: str = "warning"
    scenario_id: str | None = None
    service: str | None = None
    job: str | None = None
    instance: str | None = None


class AlertItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: str = "firing"  # "firing" or "resolved"
    labels: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)
    startsAt: datetime | None = None
    endsAt: datetime | None = None
    generatorURL: str | None = None
    fingerprint: str | None = None


class AlertManagerWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    version: str | None = "4"
    groupKey: str | None = None
    status: str = "firing"
    receiver: str | None = None
    groupLabels: dict[str, Any] = Field(default_factory=dict)
    commonLabels: dict[str, Any] = Field(default_factory=dict)
    commonAnnotations: dict[str, Any] = Field(default_factory=dict)
    externalURL: str | None = None
    alerts: list[AlertItem] = Field(default_factory=list)


class GenericEventPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    source: EventSource = EventSource.GENERIC
    event_type: str
    severity: Severity = Severity.MEDIUM
    resource_id: UUID | None = None
    resource_name: str | None = None
    resource_external_id: str | None = None
    scenario_id: str | None = None
    service: str | None = None
    message: str | None = None
    occurred_at: datetime | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    labels: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)


class NormalizedEventPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    source: EventSource
    event_type: str
    severity: Severity
    resource_id: UUID
    occurred_at: datetime
    fingerprint: str  # 64-char lowercase SHA-256
    dedup_window: datetime
    payload: dict[str, Any]
    raw_payload: dict[str, Any]
    status: str = "firing"
    scenario_id: str | None = None
    service: str | None = None
    actor: str = "system:ingestion"
