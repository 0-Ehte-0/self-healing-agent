from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class RecoveryAttribution(StrEnum):
    AGENT_HEALED = "AGENT_HEALED"
    EXTERNALLY_RECOVERED = "EXTERNALLY_RECOVERED"
    INCONCLUSIVE = "INCONCLUSIVE"


class CheckObservation(BaseModel):
    """Observation detail for a single verification check."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=128)
    observed_at: datetime
    query_interval: str | None = Field(default=None, max_length=32)
    value: float | str | bool | None = None
    unit: str | None = Field(default=None, max_length=32)
    threshold: float | str | bool | None = None
    status: CheckStatus
    reason: str = Field(..., min_length=1)
    freshness_seconds: float | None = None


class EvaluationSample(BaseModel):
    """Full snapshot of an evaluation loop sample across all verification checks."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    sample_index: int = Field(..., ge=0)
    elapsed_healthy_seconds: float = Field(..., ge=0.0)
    readiness_consecutive_successes: int = Field(..., ge=0)
    all_passed: bool
    health_score: float = Field(..., ge=0.0, le=1.0)
    checks: dict[str, CheckObservation]


class VerificationProfile(BaseModel):
    """Versioned verification profile specifying telemetry requirements."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(..., min_length=1, max_length=64)
    version: str = Field(default="1.0", max_length=32)
    description: str = Field(default="", max_length=256)
    evaluation_interval_seconds: int = Field(default=15, ge=1)
    stabilization_window_seconds: int = Field(default=90, ge=1)
    min_consecutive_passing_samples: int = Field(default=7, ge=1)
    max_verification_duration_seconds: int = Field(default=300, ge=1)
    sample_freshness_limit_seconds: int = Field(default=30, ge=1)
    readiness: dict[str, Any] = Field(default_factory=dict)
    traffic: dict[str, Any] = Field(default_factory=dict)
    cpu: dict[str, Any] = Field(default_factory=dict)
    alerts: dict[str, Any] = Field(default_factory=dict)
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "alerts": 0.25,
            "error_rate": 0.25,
            "latency": 0.25,
            "readiness": 0.25,
        }
    )


class VerificationVerdict(BaseModel):
    """Final outcome of an independent verification evaluation session."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    status: str  # "RESOLVED", "RETRY_ELIGIBLE", "ESCALATED", "EXTERNALLY_RECOVERED", "INCONCLUSIVE"
    attribution: RecoveryAttribution = RecoveryAttribution.AGENT_HEALED
    profile_id: str = Field(..., min_length=1, max_length=64)
    profile_version: str = Field(default="1.0", max_length=32)
    window_start: datetime
    window_end: datetime
    total_evaluations: int = Field(default=0, ge=0)
    consecutive_healthy_evaluations: int = Field(default=0, ge=0)
    elapsed_healthy_seconds: float = Field(default=0.0, ge=0.0)
    warm_up_duration_seconds: float | None = Field(default=None, ge=0.0)
    stabilization_resets: int = Field(default=0, ge=0)
    health_score: float = Field(default=0.0, ge=0.0, le=1.0)
    checks: dict[str, Any] = Field(default_factory=dict)
    samples: list[EvaluationSample] = Field(default_factory=list)
    failure_reason: str | None = None
