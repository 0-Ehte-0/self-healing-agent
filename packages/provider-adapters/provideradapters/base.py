from abc import ABC, abstractmethod
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ExecutionOutcomeStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"


class ProviderAdapterError(Exception):
    """Base exception for provider adapter failures."""


class PrecheckFailedError(ProviderAdapterError):
    """Raised when adapter precheck fails prior to dispatch."""


class TargetRecreatedError(PrecheckFailedError):
    """Raised when target container was recreated (Docker ID or generation changed)."""


class UncertainOutcomeError(ProviderAdapterError):
    """Raised when execution outcome cannot be deterministically proven."""


class DockerDaemonUnreachableError(ProviderAdapterError):
    """Raised when the Docker daemon is unreachable."""


class ContainerNotFoundError(ProviderAdapterError):
    """Raised when the specified container does not exist."""


class DockerTimeoutError(ProviderAdapterError):
    """Raised when a Docker operation times out."""


class ContainerSnapshot(BaseModel):
    """Bounded, redacted representation of container inspect state (omits env vars and secrets)."""

    model_config = ConfigDict(extra="forbid")

    container_id: str
    name: str
    status: str
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    restart_count: int = 0
    image: str | None = None
    health: str | None = None
    labels: dict[str, Any] = Field(default_factory=dict)


class ExecutionIntent(BaseModel):
    """Structured execution intent capturing complete target identity and pre-action context."""

    model_config = ConfigDict(extra="forbid")

    incident_id: UUID
    plan_id: UUID
    plan_version: int
    step_id: UUID
    attempt_number: int
    resource_id: UUID
    container_id: str
    binding_generation: int
    service_name: str = "demo-api"
    timeout_seconds: int = 30
    idempotency_key: str
    pre_state: dict[str, Any] = Field(default_factory=dict)


class ExecutionOutcome(BaseModel):
    """Authoritative outcome of an execution attempt."""

    model_config = ConfigDict(extra="forbid")

    status: ExecutionOutcomeStatus
    execution_id: UUID | None = None
    idempotency_key: str
    duration_ms: int = 0
    post_state: dict[str, Any] = Field(default_factory=dict)
    reconciled: bool = False
    uncertainty_reason: str | None = None
    error: str | None = None


class ReconciliationOutcome(BaseModel):
    """Result of reconciling an indeterminate execution against the infrastructure provider."""

    model_config = ConfigDict(extra="forbid")

    reconciled_success: bool
    uncertain: bool
    reason: str
    post_snapshot: ContainerSnapshot | None = None


class BaseProviderAdapter(ABC):
    """Abstract interface for restricted execution adapters."""

    @abstractmethod
    async def precheck(self, intent: ExecutionIntent) -> dict[str, Any]:
        """Validates target state, bindings, and policies before dispatch."""
        ...

    @abstractmethod
    async def execute(self, intent: ExecutionIntent) -> ExecutionOutcome:
        """Executes the mutation under a held resource lock and captures outcome."""
        ...

    @abstractmethod
    async def reconcile(self, intent: ExecutionIntent) -> ReconciliationOutcome:
        """Reconciles uncertain outcome after timeout, connection drop, or worker restart."""
        ...
