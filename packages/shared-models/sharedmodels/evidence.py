from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from sharedmodels.enums import EvidenceKind


class EvidenceItemSchema(BaseModel):
    """Pydantic representation of an immutable evidence record."""

    id: UUID = Field(default_factory=uuid4)
    incident_id: UUID
    kind: EvidenceKind | str
    source: str
    observed_at: datetime
    unit: str | None = None
    binding_generation: int | None = None
    content: dict[str, Any] = Field(default_factory=dict)
    sha256: str
    actor: str


class EvidenceBundle(BaseModel):
    """Container of evidence collected across approved telemetry sources."""

    incident_id: UUID
    resource_id: UUID
    collected_at: datetime
    collection_window_seconds: int = 900
    items: list[EvidenceItemSchema] = Field(default_factory=list)
    freshness_seconds: float | None = None
    completeness: dict[str, str] = Field(default_factory=dict)
    deployment_history_status: str = "UNAVAILABLE"
    is_truncated: bool = False
    redaction_applied: bool = True
