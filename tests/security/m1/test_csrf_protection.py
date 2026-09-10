from uuid import uuid4

import pytest
from app.db.models import User, UserSession
from app.main import app
from app.services.auth import get_current_session_and_user
from fastapi import status
from httpx import ASGITransport, AsyncClient
from sharedmodels.enums import UserRole


@pytest.fixture
def mock_admin_auth():
    user = User(
        id=uuid4(),
        username="admin-csrf",
        password_hash="mock",
        role=UserRole.ADMIN,
        enabled=True,
    )
    session = UserSession(
        id=uuid4(),
        session_token="valid-session-token",
        user_id=user.id,
        csrf_token="secret-csrf-token-12345",
        is_revoked=False,
    )
    return user, session


@pytest.mark.asyncio
async def test_csrf_header_missing_returns_403(mock_admin_auth):
    user, session = mock_admin_auth

    async def mock_auth():
        return user, session

    app.dependency_overrides[get_current_session_and_user] = mock_auth

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # POST request without X-CSRF-Token header
            res = await ac.post(
                "/api/v1/automation/mode",
                json={"mode": "DISABLED", "reason": "Testing CSRF missing"},
            )
            assert res.status_code == status.HTTP_403_FORBIDDEN
            assert "Invalid or missing CSRF token" in res.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_csrf_header_invalid_returns_403(mock_admin_auth):
    user, session = mock_admin_auth

    async def mock_auth():
        return user, session

    app.dependency_overrides[get_current_session_and_user] = mock_auth

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # POST request with wrong X-CSRF-Token header
            res = await ac.post(
                "/api/v1/automation/mode",
                json={"mode": "DISABLED", "reason": "Testing CSRF invalid"},
                headers={"X-CSRF-Token": "completely-wrong-token"},
            )
            assert res.status_code == status.HTTP_403_FORBIDDEN
            assert "Invalid or missing CSRF token" in res.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_csrf_header_valid_passes_check(mock_admin_auth):
    user, session = mock_admin_auth

    async def mock_auth():
        return user, session

    app.dependency_overrides[get_current_session_and_user] = mock_auth

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # POST request with valid X-CSRF-Token header
            res = await ac.post(
                "/api/v1/automation/mode",
                json={"mode": "DISABLED", "reason": "Testing CSRF valid"},
                headers={"X-CSRF-Token": session.csrf_token},
            )
            # Passes CSRF check! (Does not fail with 403 CSRF forbidden)
            assert res.status_code != status.HTTP_403_FORBIDDEN
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_requests_do_not_require_csrf(mock_admin_auth):
    user, session = mock_admin_auth

    async def mock_auth():
        return user, session

    app.dependency_overrides[get_current_session_and_user] = mock_auth

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # GET requests are safe and require no CSRF token
            res = await ac.get("/api/v1/automation")
            assert res.status_code == status.HTTP_200_OK
    finally:
        app.dependency_overrides.clear()
