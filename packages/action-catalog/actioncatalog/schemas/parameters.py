import re
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sharedmodels.enums import Severity

SHELL_INJECTION_PATTERN = re.compile(
    r"[;&|`$><\n\r]|\b(sudo|rm|exec|sh|bash|powershell|cmd)\b",
    re.IGNORECASE,
)


def validate_no_shell(value: str) -> str:
    if SHELL_INJECTION_PATTERN.search(value):
        raise ValueError(
            f"Shell metacharacters and commands are strictly forbidden in parameter: {value!r}"
        )
    return value


SafeString = Annotated[str, Field(min_length=1, max_length=1024)]


class InspectContainerParams(BaseModel):
    """Parameters for inspect_container action."""

    model_config = ConfigDict(extra="forbid")

    container_id: str = Field(
        ...,
        pattern=r"^[a-fA-F0-9]{12,64}$",
        description="Exact Docker container hex ID",
    )
    resource_id: UUID
    binding_generation: int | None = Field(default=None, ge=1)


class RestartContainerParams(BaseModel):
    """Parameters for restart_container action."""

    model_config = ConfigDict(extra="forbid")

    container_id: str = Field(
        ...,
        pattern=r"^[a-fA-F0-9]{12,64}$",
        description="Exact Docker container hex ID",
    )
    resource_id: UUID
    service_name: str = Field(
        default="demo-api",
        description="Allowlisted service name (only 'demo-api' permitted in M1)",
    )
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    binding_generation: int | None = Field(default=None, ge=1)

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, v: str) -> str:
        v = validate_no_shell(v)
        if v != "demo-api":
            raise ValueError(
                f"Service '{v}' is not allowlisted for restart in M1. Only 'demo-api' is permitted."
            )
        return v


class WaitForStabilizationParams(BaseModel):
    """Parameters for wait_for_stabilization action."""

    model_config = ConfigDict(extra="forbid")

    resource_id: UUID
    duration_seconds: int = Field(
        default=90,
        ge=1,
        le=300,
        description="Stabilization duration in seconds (standard 90s for M1)",
    )
    verification_profile: str = Field(
        default="m1_default_restart_profile",
        max_length=64,
        description="Target verification profile name",
    )

    @field_validator("verification_profile")
    @classmethod
    def validate_verification_profile(cls, v: str) -> str:
        return validate_no_shell(v)


class NotifyOperatorParams(BaseModel):
    """Parameters for notify_operator action."""

    model_config = ConfigDict(extra="forbid")

    incident_id: UUID
    reason: str = Field(..., min_length=1, max_length=256)
    severity: Severity = Field(default=Severity.MEDIUM)
    message: str = Field(..., min_length=1, max_length=4096)

    @field_validator("reason", "message")
    @classmethod
    def validate_strings(cls, v: str) -> str:
        return validate_no_shell(v)


class OpenIncidentTicketParams(BaseModel):
    """Parameters for open_incident_ticket action."""

    model_config = ConfigDict(extra="forbid")

    incident_id: UUID
    title: str = Field(..., min_length=1, max_length=256)
    summary: str = Field(..., min_length=1, max_length=4096)
    root_cause: str = Field(..., min_length=1, max_length=128)
    escalation_reason: str = Field(..., min_length=1, max_length=2048)

    @field_validator("title", "summary", "root_cause", "escalation_reason")
    @classmethod
    def validate_strings(cls, v: str) -> str:
        return validate_no_shell(v)
