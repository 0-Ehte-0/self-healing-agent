from collections.abc import AsyncGenerator
from typing import Annotated

from app.core.security import constant_time_compare
from app.db.models import User, UserSession
from app.db.repositories.control_plane import unit_of_work
from app.db.session import AsyncSessionLocal
from app.services.auth.session_service import SessionService
from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sharedmodels.enums import UserRole

SESSION_COOKIE_NAME = "sh_session_id"

ROLE_RANKS: dict[UserRole, int] = {
    UserRole.VIEWER: 0,
    UserRole.OPERATOR: 1,
    UserRole.APPROVER: 2,
    UserRole.ADMIN: 3,
}


async def get_current_session_and_user(
    request: Request,
    sh_session_id: Annotated[str | None, Cookie()] = None,
) -> tuple[User, UserSession]:
    """Authenticates the current session from HttpOnly cookie and loads the user."""
    # Never accept spoofed actor headers from client
    if "x-actor" in request.headers or "app.actor" in request.headers:
        # Ignore client-supplied actor; server strictly derives it from session
        pass

    if not sh_session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please log in.",
        )

    async with unit_of_work(AsyncSessionLocal, actor="system:auth") as repo:
        session = await SessionService.validate_session(repo, sh_session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired or invalid. Please log in again.",
            )

        user = await repo.session.get(User, session.user_id)
        if user is None or not user.enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is disabled or inaccessible.",
            )

        # Attach server-derived actor to request state
        request.state.actor = f"user:{user.username}"
        request.state.user = user
        request.state.session = session
        return user, session


async def get_current_user(
    auth: Annotated[tuple[User, UserSession], Depends(get_current_session_and_user)],
) -> User:
    return auth[0]


async def require_csrf(
    request: Request,
    auth: Annotated[tuple[User, UserSession], Depends(get_current_session_and_user)],
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> None:
    """Enforces double-submit / session-bound CSRF token check on unsafe requests."""
    if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
        _, session = auth
        if not x_csrf_token or not constant_time_compare(x_csrf_token, session.csrf_token):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid or missing CSRF token.",
            )


def require_role(minimum_role: UserRole):
    """Factory creating role requirement dependencies according to role hierarchy."""

    async def role_checker(
        user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        user_rank = ROLE_RANKS.get(user.role, -1)
        required_rank = ROLE_RANKS.get(minimum_role, 999)
        if user_rank < required_rank:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation requires '{minimum_role.value}' capability. Current role: '{user.role.value}'.",
            )
        return user

    return role_checker


require_viewer = require_role(UserRole.VIEWER)
require_operator = require_role(UserRole.OPERATOR)
require_approver = require_role(UserRole.APPROVER)
require_admin = require_role(UserRole.ADMIN)
