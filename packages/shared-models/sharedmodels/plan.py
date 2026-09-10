from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from sharedmodels.enums import RiskLevel


class TargetBinding(BaseModel):
    """Immutable binding between a logical resource and an exact Docker container."""

    model_config = ConfigDict(extra="forbid")

    resource_id: UUID
    container_id: str = Field(
        ...,
        pattern=r"^[a-fA-F0-9]{12,64}$",
        description="Exact Docker container hex ID (12 to 64 hex characters)",
    )
    service_name: str = Field(..., min_length=1, max_length=128)
    binding_generation: int = Field(default=1, ge=1)


class RemediationStepSchema(BaseModel):
    """Pydantic representation of an ordered remediation step in a plan."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    plan_id: UUID | None = None
    resource_id: UUID
    position: int = Field(..., ge=0)
    action: str = Field(..., min_length=1, max_length=128)
    action_schema_version: str = Field(default="1.0", max_length=16)
    parameters: dict[str, Any] = Field(default_factory=dict)
    verification: dict[str, Any] = Field(default_factory=dict)


class RemediationPlanSchema(BaseModel):
    """Pydantic representation of a complete, typed, versioned remediation plan."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    incident_id: UUID
    diagnosis_id: UUID
    version: int = Field(default=1, ge=1)
    risk: RiskLevel = Field(default=RiskLevel.LOW)
    target_binding: TargetBinding
    steps: list[RemediationStepSchema] = Field(default_factory=list)
    verification_profile: str | None = Field(default="m1_default_restart_profile", max_length=64)
    content_hash: str = Field(
        ...,
        pattern=r"^[a-fA-F0-9]{64}$",
        description="SHA-256 digest of canonical plan contents",
    )
    approved: bool = False
    actor: str = Field(..., min_length=1, max_length=128)
    created_at: datetime | None = None


class DryRunSchema(BaseModel):
    """Pydantic representation of a dry-run execution record."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    incident_id: UUID
    plan_id: UUID
    plan_version: int = Field(..., ge=1)
    content_hash: str = Field(..., pattern=r"^[a-fA-F0-9]{64}$")
    policy_evaluation: dict[str, Any] = Field(default_factory=dict)
    validation_result: dict[str, Any] = Field(default_factory=dict)
    simulated_steps: list[dict[str, Any]] = Field(default_factory=list)
    actor: str = Field(..., min_length=1, max_length=128)
    created_at: datetime | None = None
