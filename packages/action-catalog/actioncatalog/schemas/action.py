from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sharedmodels.enums import RiskLevel


class ActionCategory(StrEnum):
    READ_ONLY = "READ_ONLY"
    WORKLOAD_MUTATION = "WORKLOAD_MUTATION"
    VERIFICATION_DECLARATION = "VERIFICATION_DECLARATION"
    OPERATOR_NOTIFICATION = "OPERATOR_NOTIFICATION"
    LOCAL_ESCALATION = "LOCAL_ESCALATION"


class ActionDefinition(BaseModel):
    """Metadata, safety properties, and parameter schema for a catalog action."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=128)
    version: str = Field(default="1.0", max_length=16)
    category: ActionCategory
    risk: RiskLevel
    timeout_seconds: int = Field(..., ge=1, le=300)
    retryable: bool
    verification_profile: str | None = None
    description: str
    parameters_schema: type[BaseModel] | dict[str, Any]
