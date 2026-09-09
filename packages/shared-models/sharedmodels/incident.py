from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sharedmodels.enums import IncidentState, Severity


class IncidentContract(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    id: UUID
    resource_id: UUID
    correlation_key: str
    state: IncidentState
    severity: Severity
    version: int = Field(ge=1)
    attempts: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
