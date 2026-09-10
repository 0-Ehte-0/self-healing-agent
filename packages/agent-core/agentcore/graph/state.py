from typing import Any

from typing_extensions import TypedDict


class IncidentGraphState(TypedDict, total=False):
    """Small canonical workflow state tracked across LangGraph checkpoints.

    Per Section 6 Step 2: Keep graph state small: incident ID/version, current plan/diagnosis IDs,
    retry count, wait reason, and checkpoint metadata. Large evidence remains in PostgreSQL.
    """

    incident_id: str
    version: int
    correlation_key: str
    resource_id: str
    current_diagnosis_id: str | None
    parent_diagnosis_id: str | None
    current_plan_id: str | None
    attempts: int
    retry_limit: int
    approval_required: bool
    is_approved: bool | None
    approval_expired: bool | None
    is_resumed_from_approval: bool | None
    wait_reason: str | None
    wait_until: str | None
    status: str
    evidence_ids: list[str] | None
    is_actionable: bool | None
    last_error: str | None
    escalation_reason: str | None
    verification_passed: bool | None
    retry_eligible: bool | None
    next_eligible_at: str | None
    target_container_id: str | None
    container_id: str | None
    binding_generation: int | None
    service_name: str | None
    root_cause: str | None
    scenario_id: str | None
    verification_profile: str | None
    plan_version: int | None
    content_hash: str | None
    action: str | None
    risk: str | None
    confidence: float | None
    policy_decision: str | None
    policy_decision_id: str | None
    defer_until: str | None
    current_step_id: str | None
    execution_id: str | None
    current_execution_id: str | None
    attribution: str | None
    observation_bundle: Any | None
