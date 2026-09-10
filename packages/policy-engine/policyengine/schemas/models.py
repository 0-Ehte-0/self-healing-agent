from datetime import UTC, datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PolicyDecisionOutcome(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DEFER = "DEFER"


class AutomationMode(StrEnum):
    DISABLED = "DISABLED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    AUTOMATIC = "AUTOMATIC"


class RuleEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rule_name: str
    passed: bool
    observed_value: Any = None
    threshold: Any = None
    reason_code: str
    message: str


class PolicyRuleSet(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = "m1_default_policy"
    version: int = 2
    auto_approval_confidence_threshold: float = 0.85
    min_approval_confidence_threshold: float = 0.60
    retry_limit: int = 2
    cooldown_seconds: int = 600
    allowed_environments: list[str] = Field(default_factory=lambda: ["local"])
    allowed_actions: list[str] = Field(
        default_factory=lambda: [
            "restart_container",
            "inspect_container",
            "wait_for_stabilization",
            "notify_operator",
            "open_incident_ticket",
        ]
    )
    allowed_targets: list[str] = Field(default_factory=lambda: ["demo-api"])
    require_low_risk_for_approval: bool = True
    evidence_freshness_limit_seconds: int = 300


class EvaluationContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    incident_id: UUID
    plan_id: UUID
    plan_version: int
    content_hash: str
    container_id: str
    binding_generation: int
    service_name: str
    environment: str = "local"
    action: str = "restart_container"
    risk: str = "LOW"
    confidence: float = 0.0
    root_cause: str = "UNKNOWN"
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    attempts: int = 0
    retry_limit: int = 2
    last_execution_time: datetime | None = None
    is_resource_locked: bool = False
    lock_owner: str | None = None
    approval_grant: dict[str, Any] | None = None
    automation_mode: AutomationMode = AutomationMode.APPROVAL_REQUIRED
    emergency_stopped_resources: list[str] = Field(default_factory=list)
    current_time: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PolicyEvaluationDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decision: PolicyDecisionOutcome
    policy_name: str
    policy_version: int
    plan_id: UUID
    plan_version: int
    content_hash: str
    container_id: str
    binding_generation: int
    evaluated_facts: dict[str, Any]
    rule_results: list[RuleEvaluationResult]
    reason_codes: list[str]
    defer_until: datetime | None = None
    wait_reason: str | None = None
