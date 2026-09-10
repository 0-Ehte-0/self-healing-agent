from uuid import uuid4

import pytest
from app.db.models import User, UserSession
from app.main import app
from app.services.auth import get_current_session_and_user, require_csrf
from fastapi import Request, status
from httpx import ASGITransport, AsyncClient
from sharedmodels.enums import UserRole


@pytest.mark.asyncio
async def test_client_supplied_actor_headers_are_ignored():
    """Verifies that client cannot spoof their identity using X-Actor or app.actor headers."""
    real_user = User(
        id=uuid4(),
        username="authentic_operator",
        password_hash="mock",
        role=UserRole.OPERATOR,
        enabled=True,
    )
    session = UserSession(
        id=uuid4(),
        session_token="session-123",
        user_id=real_user.id,
        csrf_token="csrf-123",
        is_revoked=False,
    )

    captured_actor = None

    async def mock_auth(request: Request):
        nonlocal captured_actor
        request.state.actor = f"user:{real_user.username}"
        request.state.user = real_user
        request.state.session = session
        captured_actor = request.state.actor
        return real_user, session

    app.dependency_overrides[get_current_session_and_user] = mock_auth
    app.dependency_overrides[require_csrf] = lambda: None

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Client attempts to spoof their identity as an admin
            res = await ac.get(
                "/api/v1/automation",
                headers={
                    "X-Actor": "user:admin",
                    "app.actor": "system:root",
                },
            )
            assert res.status_code == status.HTTP_200_OK
            # Server-derived actor is strictly "user:authentic_operator", never spoofed!
            assert captured_actor == "user:authentic_operator"
    finally:
        app.dependency_overrides.clear()
