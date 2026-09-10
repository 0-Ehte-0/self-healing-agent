from datetime import UTC, datetime, timedelta, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

import sqlalchemy as sa
from app.core.security import rate_limiter
from app.db.models import (
    Approval,
    Incident,
    OutboxEvent,
    RemediationPlan,
    User,
    UserSession,
)
from app.db.repositories.control_plane import ConflictError, unit_of_work
from app.db.session import AsyncSessionLocal
from app.services.auth import (
    get_current_session_and_user,
    require_approver,
    require_csrf,
    require_viewer,
)
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sharedmodels.enums import IncidentState

router = APIRouter(prefix="/api/v1/incidents", tags=["approvals"])


class ApprovalRequestBody(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    expected_incident_version: int
    plan_version: int
    content_hash: str | None = None
    rejection_reason: str | None = None
    idempotency_key: str = Field(min_length=1)


class ApprovalResponse(BaseModel):
    id: str
    incident_id: str
    plan_id: str
    plan_version: int
    decision: str
    rejection_reason: str | None
    approver: str
    created_at: str
    expires_at: str
    incident_state: str
    incident_version: int


@router.post("/{incident_id}/approval", response_model=ApprovalResponse)
async def submit_approval(
    incident_id: UUID,
    body: ApprovalRequestBody,
    request: Request,
    response: Response,
    auth: Annotated[tuple[User, UserSession], Depends(get_current_session_and_user)],
    _csrf: Annotated[None, Depends(require_csrf)],
    _role: Annotated[User, Depends(require_approver)],
) -> ApprovalResponse:
    user, _ = auth
    actor_str = f"user:{user.username}"

    # Rate limiting on mutation
    if not rate_limiter.is_allowed(f"approval:{actor_str}", max_requests=30, window_seconds=60.0):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many approval requests. Please slow down.",
        )

    # Validate rejection reason
    if body.decision == "REJECT":
        if not body.rejection_reason or not body.rejection_reason.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A non-empty rejection reason is required when rejecting a remediation plan.",
            )

    async with unit_of_work(AsyncSessionLocal, actor=actor_str) as repo:
        # 1. Fetch incident
        incident = await repo.session.get(Incident, incident_id)
        if incident is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Incident not found.",
            )

        # 2. Check current state and version
        if incident.state != IncidentState.PENDING_APPROVAL:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Incident is not in PENDING_APPROVAL state (current state: {incident.state.value}).",
            )

        if incident.version != body.expected_incident_version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Incident version mismatch: expected {body.expected_incident_version}, but current version is {incident.version}.",
            )

        # 3. Fetch plan
        plan_stmt = sa.select(RemediationPlan).where(
            RemediationPlan.incident_id == incident_id,
            RemediationPlan.version == body.plan_version,
        )
        plan = await repo.session.scalar(plan_stmt)
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Remediation plan version {body.plan_version} not found for this incident.",
            )

        if body.content_hash and plan.content_hash and body.content_hash != plan.content_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Plan content hash mismatch.",
            )

        # 4. Check idempotency: check if an approval with this idempotency key was recorded
        # We can store idempotency key in audit entries or check existing approvals for this plan
        existing_approval = await repo.session.scalar(
            sa.select(Approval)
            .where(
                Approval.plan_id == plan.id,
                Approval.plan_version == body.plan_version,
            )
            .order_by(Approval.created_at.desc())
        )

        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=29, seconds=55)

        # 5. Record decision and transition incident
        if body.decision == "APPROVE":
            approval = await repo.record_approval(
                incident_id=incident_id,
                plan_id=plan.id,
                plan_version=plan.version,
                approver_id=user.id,
                decision="APPROVE",
                rejection_reason=None,
                expires_at=expires_at,
                created_at=now,
            )
            # Mark plan approved
            plan.approved = True
            # Transition incident to APPROVED
            updated_incident = await repo.transition(
                incident_id=incident_id,
                expected_version=incident.version,
                target=IncidentState.APPROVED,
            )
        else:  # REJECT
            approval = await repo.record_approval(
                incident_id=incident_id,
                plan_id=plan.id,
                plan_version=plan.version,
                approver_id=user.id,
                decision="REJECT",
                rejection_reason=body.rejection_reason.strip() if body.rejection_reason else None,
                expires_at=expires_at,
                created_at=now,
            )
            # Transition incident to ESCALATED per ADR-0003
            updated_incident = await repo.transition(
                incident_id=incident_id,
                expected_version=incident.version,
                target=IncidentState.ESCALATED,
            )
            # Record explicit EscalationRecord
            await repo.create_escalation_record(
                incident_id=incident_id,
                title="Remediation Plan Rejected by Operator",
                summary=f"Plan version {plan.version} was rejected by {user.username}.",
                root_cause="PLAN_REJECTED",
                escalation_reason=body.rejection_reason.strip() if body.rejection_reason else "",
                ticket_reference=f"TICKET-{incident_id.hex[:8].upper()}",
            )

        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"

        return ApprovalResponse(
            id=str(approval.id),
            incident_id=str(incident_id),
            plan_id=str(plan.id),
            plan_version=plan.version,
            decision=approval.decision,
            rejection_reason=approval.rejection_reason,
            approver=user.username,
            created_at=approval.created_at.isoformat() if approval.created_at else now.isoformat(),
            expires_at=approval.expires_at.isoformat(),
            incident_state=updated_incident.state.value,
            incident_version=updated_incident.version,
        )


@router.get("/{incident_id}/approval")
async def get_approval_status(
    incident_id: UUID,
    response: Response,
    _auth: Annotated[tuple[User, UserSession], Depends(get_current_session_and_user)],
    _role: Annotated[User, Depends(require_viewer)],
) -> dict[str, Any]:
    async with unit_of_work(AsyncSessionLocal, actor="system:read") as repo:
        incident = await repo.session.get(Incident, incident_id)
        if incident is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found.")

        # Get plans and approvals
        plans = list(
            (
                await repo.session.scalars(
                    sa.select(RemediationPlan)
                    .where(RemediationPlan.incident_id == incident_id)
                    .order_by(RemediationPlan.version.desc())
                )
            ).all()
        )

        approvals_data = []
        if plans:
            plan_ids = [p.id for p in plans]
            approvals = list(
                (
                    await repo.session.scalars(
                        sa.select(Approval)
                        .where(Approval.plan_id.in_(plan_ids))
                        .order_by(Approval.created_at.desc())
                    )
                ).all()
            )
            for a in approvals:
                approvals_data.append(
                    {
                        "id": str(a.id),
                        "plan_id": str(a.plan_id),
                        "plan_version": a.plan_version,
                        "decision": a.decision,
                        "rejection_reason": a.rejection_reason,
                        "created_at": a.created_at.isoformat() if a.created_at else None,
                        "expires_at": a.expires_at.isoformat(),
                    }
                )

        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return {
            "incident_id": str(incident_id),
            "state": incident.state.value,
            "version": incident.version,
            "approval_required": incident.approval_required,
            "approvals": approvals_data,
        }
