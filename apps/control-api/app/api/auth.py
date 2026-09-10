from typing import Annotated, Any

import sqlalchemy as sa
from app.core.config import get_settings
from app.core.security import rate_limiter, verify_password
from app.db.models import User, UserSession
from app.db.repositories.control_plane import unit_of_work
from app.db.session import AsyncSessionLocal
from app.services.auth import (
    SESSION_COOKIE_NAME,
    SessionService,
    get_current_session_and_user,
)
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
settings = get_settings()


class LoginRequest(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: str
    username: str
    role: str
    enabled: bool


class LoginResponse(BaseModel):
    user: UserResponse
    csrf_token: str
    message: str = "Login successful"


@router.post("/login", response_model=LoginResponse)
async def login(
    req: LoginRequest,
    request: Request,
    response: Response,
) -> LoginResponse:
    # 1. Rate limiting: max 10 requests per minute per client IP or username
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"login:{client_ip}:{req.username}"
    if not rate_limiter.is_allowed(rate_key, max_requests=10, window_seconds=60.0):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please wait 60 seconds before trying again.",
        )

    # 2. Authenticate against database
    async with unit_of_work(AsyncSessionLocal, actor="system:login") as repo:
        user = await repo.session.scalar(sa.select(User).where(User.username == req.username))
        if user is None or not verify_password(req.password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password.",
            )

        if not user.enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is disabled.",
            )

        # 3. Create session
        user_agent = request.headers.get("user-agent")
        _, session_token, csrf_token = await SessionService.create_user_session(
            repo=repo,
            user_id=user.id,
            ip_address=client_ip,
            user_agent=user_agent,
        )

        # 4. Set HttpOnly session cookie
        secure_cookie = settings.ENV == "production"
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session_token,
            httponly=True,
            secure=secure_cookie,
            samesite="lax",
            path="/",
            max_age=8 * 3600,
        )

        # Non-cacheable authenticated response
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"

        return LoginResponse(
            user=UserResponse(
                id=str(user.id),
                username=user.username,
                role=user.role.value,
                enabled=user.enabled,
            ),
            csrf_token=csrf_token,
        )


@router.post("/logout")
async def logout(
    response: Response,
    auth: Annotated[tuple[User, UserSession], Depends(get_current_session_and_user)],
) -> dict[str, str]:
    user, session = auth
    async with unit_of_work(AsyncSessionLocal, actor=f"user:{user.username}") as repo:
        await SessionService.revoke_session(repo, session.session_token)

    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return {"message": "Logged out successfully"}


@router.get("/me")
async def get_me(
    response: Response,
    auth: Annotated[tuple[User, UserSession], Depends(get_current_session_and_user)],
) -> dict[str, Any]:
    user, session = auth
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return {
        "id": str(user.id),
        "username": user.username,
        "role": user.role.value,
        "enabled": user.enabled,
        "csrf_token": session.csrf_token,
    }
