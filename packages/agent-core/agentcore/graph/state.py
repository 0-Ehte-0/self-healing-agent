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
    current_plan_id: str | None
    attempts: int
    retry_limit: int
    approval_required: bool
    is_approved: bool | None
    wait_reason: str | None
    wait_until: str | None
    status: str
    evidence_ids: list[str] | None
    is_actionable: bool | None
    last_error: str | None
    verification_passed: bool | None
    retry_eligible: bool | None
