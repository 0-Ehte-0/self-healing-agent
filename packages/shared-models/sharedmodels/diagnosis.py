from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from sharedmodels.enums import RootCause


class DiagnosisSchema(BaseModel):
    """Pydantic representation of a structured diagnosis record."""

    id: UUID = Field(default_factory=uuid4)
    incident_id: UUID
    root_cause: RootCause | str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    rule_id: str
    rule_version: str
    contradictory_findings: list[str] = Field(default_factory=list)
    escalation_reason: str | None = None
    reasoning: dict[str, Any] = Field(default_factory=dict)
    is_actionable: bool = False
    parent_diagnosis_id: UUID | None = None
    actor: str
