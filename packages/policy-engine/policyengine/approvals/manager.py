from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ApprovalSubmission(BaseModel):
    model_config = ConfigDict(extra="ignore")

    incident_id: UUID
    decision: str  # "APPROVE" or "REJECT"
    expected_incident_version: int
    plan_version: int
    content_hash: str | None = None
    rejection_reason: str | None = None
    idempotency_key: str


class ApprovalGrantDetails(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    plan_id: UUID
    plan_version: int
    content_hash: str
    approver_id: UUID
    approver_role: str
    decision: str
    rejection_reason: str | None = None
    created_at: datetime
    expires_at: datetime


class ApprovalLifecycleManager:
    """Manages validation, expiry timing, and consistency of human approvals.

    Guarantees:
    - 30-minute approval request deadline.
    - 30-minute validity deadline for granted approvals.
    - Non-empty rejection reason required for REJECT decisions.
    - Idempotent decision recording matching plan version and content hash.
    """

    REQUEST_TTL_MINUTES: int = 30
    GRANT_TTL_MINUTES: int = 30

    @classmethod
    def calculate_request_deadline(cls, from_time: datetime | None = None) -> datetime:
        base = from_time or datetime.now(UTC)
        return base + timedelta(minutes=cls.REQUEST_TTL_MINUTES)

    @classmethod
    def calculate_grant_expiry(cls, from_time: datetime | None = None) -> datetime:
        base = from_time or datetime.now(UTC)
        return base + timedelta(minutes=cls.GRANT_TTL_MINUTES)

    @classmethod
    def validate_submission(cls, submission: ApprovalSubmission) -> None:
        dec = submission.decision.upper()
        if dec not in {"APPROVE", "REJECT"}:
            raise ValueError(f"Decision must be 'APPROVE' or 'REJECT', got '{submission.decision}'")

        if dec == "REJECT":
            if not submission.rejection_reason or not submission.rejection_reason.strip():
                raise ValueError(
                    "A non-empty rejection reason is strictly required when rejecting a plan"
                )

        if submission.expected_incident_version < 1:
            raise ValueError("expected_incident_version must be >= 1")

        if submission.plan_version < 1:
            raise ValueError("plan_version must be >= 1")

        if not submission.idempotency_key or not submission.idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string")
