from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from sharedmodels.enums import ExecutionStatus


class ExecutionContract(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    id: UUID
    incident_id: UUID
    plan_id: UUID | None = None
    step_id: UUID
    resource_id: UUID
    attempt_number: int = 1
    container_id: str | None = None
    binding_generation: int | None = None
    lock_token: UUID | None = None
    idempotency_key: str
    status: ExecutionStatus
    actor: str
    pre_state: dict = {}
    result: dict = {}
    uncertainty_reason: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
