from typing import Annotated, Any, Literal

from app.db.models import User, UserSession
from app.db.repositories.control_plane import unit_of_work
from app.db.session import AsyncSessionLocal
from app.services.auth import (
    get_current_session_and_user,
    require_admin,
    require_csrf,
    require_viewer,
)
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/automation", tags=["automation"])


class SetModeRequest(BaseModel):
    mode: Literal["DISABLED", "APPROVAL_REQUIRED", "AUTOMATIC"]
    reason: str = Field(min_length=1)


class EmergencyStopRequest(BaseModel):
    resource_identifier: str = Field(min_length=1)
    action: Literal["STOP", "RELEASE"]
    reason: str = Field(min_length=1)


class AutomationStatusResponse(BaseModel):
    mode: str
    emergency_stopped_resources: list[str]
    updated_at: str | None
    updated_by: str
    reason: str


@router.get("", response_model=AutomationStatusResponse)
async def get_automation_status(
    response: Response,
    _auth: Annotated[tuple[User, UserSession], Depends(get_current_session_and_user)],
    _role: Annotated[User, Depends(require_viewer)],
) -> AutomationStatusResponse:
    async with unit_of_work(AsyncSessionLocal, actor="system:read") as repo:
        control = await repo.get_automation_controls()
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return AutomationStatusResponse(
            mode=control.mode,
            emergency_stopped_resources=control.emergency_stopped_resources or [],
            updated_at=control.updated_at.isoformat() if control.updated_at else None,
            updated_by=control.updated_by,
            reason=control.reason,
        )


@router.post("/mode", response_model=AutomationStatusResponse)
async def update_automation_mode(
    body: SetModeRequest,
    response: Response,
    auth: Annotated[tuple[User, UserSession], Depends(get_current_session_and_user)],
    _csrf: Annotated[None, Depends(require_csrf)],
    _admin: Annotated[User, Depends(require_admin)],
) -> AutomationStatusResponse:
    user, _ = auth
    actor_str = f"user:{user.username}"

    async with unit_of_work(AsyncSessionLocal, actor=actor_str) as repo:
        control = await repo.update_automation_controls(
            mode=body.mode,
            reason=body.reason,
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return AutomationStatusResponse(
            mode=control.mode,
            emergency_stopped_resources=control.emergency_stopped_resources or [],
            updated_at=control.updated_at.isoformat() if control.updated_at else None,
            updated_by=control.updated_by,
            reason=control.reason,
        )


@router.post("/emergency-stop", response_model=AutomationStatusResponse)
async def manage_emergency_stop(
    body: EmergencyStopRequest,
    response: Response,
    auth: Annotated[tuple[User, UserSession], Depends(get_current_session_and_user)],
    _csrf: Annotated[None, Depends(require_csrf)],
    _admin: Annotated[User, Depends(require_admin)],
) -> AutomationStatusResponse:
    user, _ = auth
    actor_str = f"user:{user.username}"

    async with unit_of_work(AsyncSessionLocal, actor=actor_str) as repo:
        control = await repo.get_automation_controls()
        current_stops = set(control.emergency_stopped_resources or [])

        if body.action == "STOP":
            current_stops.add(body.resource_identifier)
        else:
            current_stops.discard(body.resource_identifier)

        updated_control = await repo.update_automation_controls(
            mode=control.mode,
            emergency_stopped_resources=sorted(current_stops),
            reason=f"Emergency stop {body.action} for '{body.resource_identifier}': {body.reason}",
        )

        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return AutomationStatusResponse(
            mode=updated_control.mode,
            emergency_stopped_resources=updated_control.emergency_stopped_resources or [],
            updated_at=updated_control.updated_at.isoformat()
            if updated_control.updated_at
            else None,
            updated_by=updated_control.updated_by,
            reason=updated_control.reason,
        )
