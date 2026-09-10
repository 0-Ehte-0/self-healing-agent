from datetime import UTC, datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sharedmodels.enums import RootCause


class RuleEvaluationResult(BaseModel):
    """Result returned by an individual diagnostic rule."""

    rule_id: str
    rule_version: str
    matched: bool
    confidence: float = 0.0
    root_cause: RootCause | str = RootCause.INSUFFICIENT_EVIDENCE
    is_actionable: bool = False
    contributing_evidence_ids: list[str] = Field(default_factory=list)
    contradictory_findings: list[str] = Field(default_factory=list)
    escalation_reason: str | None = None
    reasoning: dict[str, Any] = Field(default_factory=dict)


class ObservationBundle(BaseModel):
    """Structured observations passed into deterministic diagnostic rules."""

    resource_id: UUID
    service_name: str
    container_id: str | None = None
    binding_generation: int | None = None
    container_status: str = "unknown"  # "running", "exited", "dead", "not_found", "unknown"
    exit_code: int | None = None
    health_status: str | None = None
    up_metric: float | None = None
    normalized_cpu: float | None = None
    raw_cpu_seconds_rate: float | None = None
    cpu_budget_cores: float = 1.0
    readiness_success_ratio: float | None = None
    latest_readiness_code: int | None = None
    latest_readiness_latency_sec: float | None = None
    p95_latency_sec: float | None = None
    error_rate: float | None = None
    firing_alerts: list[str] = Field(default_factory=list)
    redis_connected: bool | None = None
    db_connections_active: int | None = None
    recent_log_errors: list[str] = Field(default_factory=list)
    evidence_id_map: dict[str, str] = Field(default_factory=dict)  # signal name -> evidence_item_id
    completeness: dict[str, str] = Field(default_factory=dict)
    freshness_seconds: float | None = None
    is_telemetry_unavailable: bool = False
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
