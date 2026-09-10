from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from app.core.security import hash_password
from app.db.models import User, UserSession
from app.main import app
from app.services.auth import SESSION_COOKIE_NAME, get_current_session_and_user
from fastapi import status
from httpx import ASGITransport, AsyncClient
from sharedmodels.enums import UserRole


@pytest.mark.asyncio
async def test_session_expiry_and_invalidation():
    user = User(
        id=uuid4(),
        username="session_test_user",
        password_hash="mock",
        role=UserRole.OPERATOR,
        enabled=True,
    )
    # Expired session
    expired_session = UserSession(
        id=uuid4(),
        session_token="expired-token",
        user_id=user.id,
        csrf_token="csrf-1",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
        is_revoked=False,
    )

    # When accessing with expired token
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        ac.cookies.set(SESSION_COOKIE_NAME, expired_session.session_token)
        res = await ac.get("/api/v1/auth/me")
        assert res.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_auth_me_returns_profile_and_csrf():
    user = User(
        id=uuid4(),
        username="me_user",
        password_hash="mock",
        role=UserRole.APPROVER,
        enabled=True,
    )
    session = UserSession(
        id=uuid4(),
        session_token="valid-token",
        user_id=user.id,
        csrf_token="my-csrf-token",
        expires_at=datetime.now(UTC) + timedelta(hours=4),
        is_revoked=False,
    )

    async def mock_auth():
        return user, session

    app.dependency_overrides[get_current_session_and_user] = mock_auth

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get("/api/v1/auth/me")
            assert res.status_code == status.HTTP_200_OK
            data = res.json()
            assert data["username"] == "me_user"
            assert data["role"] == "approver"
            assert data["csrf_token"] == "my-csrf-token"
            assert res.headers.get("Cache-Control") == "no-store, no-cache, must-revalidate"
    finally:
        app.dependency_overrides.clear()
