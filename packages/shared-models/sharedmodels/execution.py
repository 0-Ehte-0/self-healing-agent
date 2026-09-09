from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from sharedmodels.enums import ExecutionStatus


class ExecutionContract(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    id: UUID
    incident_id: UUID
    step_id: UUID
    resource_id: UUID
    idempotency_key: str
    status: ExecutionStatus
    actor: str
    created_at: datetime
