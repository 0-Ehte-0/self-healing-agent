from uuid import uuid4

import pytest
from app.db.models import User, UserSession
from app.main import app
from app.services.auth import get_current_session_and_user, require_csrf
from fastapi import status
from httpx import ASGITransport, AsyncClient
from sharedmodels.enums import UserRole


def make_mock_auth(role: UserRole):
    user = User(
        id=uuid4(),
        username=f"test-{role.value}",
        password_hash="mock",
        role=role,
        enabled=True,
    )
    session = UserSession(
        id=uuid4(),
        session_token="mock-session-token",
        user_id=user.id,
        csrf_token="mock-csrf-token",
        is_revoked=False,
    )
    return user, session


@pytest.mark.asyncio
async def test_viewer_role_access_boundary():
    user, session = make_mock_auth(UserRole.VIEWER)

    async def mock_get_auth():
        return user, session

    async def mock_csrf():
        return None

    app.dependency_overrides[get_current_session_and_user] = mock_get_auth
    app.dependency_overrides[require_csrf] = mock_csrf

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 1. Viewer can read automation status (requires viewer)
            res = await ac.get("/api/v1/automation")
            assert res.status_code == status.HTTP_200_OK

            # 2. Viewer cannot toggle automation mode (requires admin) -> 403
            res = await ac.post(
                "/api/v1/automation/mode", json={"mode": "AUTOMATIC", "reason": "test"}
            )
            assert res.status_code == status.HTTP_403_FORBIDDEN
            assert "requires 'admin' capability" in res.text

            # 3. Viewer cannot approve plans (requires approver) -> 403
            res = await ac.post(
                f"/api/v1/incidents/{uuid4()}/approval",
                json={
                    "decision": "APPROVE",
                    "expected_incident_version": 1,
                    "plan_version": 1,
                    "idempotency_key": "k1",
                },
            )
            assert res.status_code == status.HTTP_403_FORBIDDEN
            assert "requires 'approver' capability" in res.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_operator_role_access_boundary():
    user, session = make_mock_auth(UserRole.OPERATOR)

    async def mock_get_auth():
        return user, session

    async def mock_csrf():
        return None

    app.dependency_overrides[get_current_session_and_user] = mock_get_auth
    app.dependency_overrides[require_csrf] = mock_csrf

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 1. Operator can read automation status -> 200
            res = await ac.get("/api/v1/automation")
            assert res.status_code == status.HTTP_200_OK

            # 2. Operator cannot approve plans (requires approver) -> 403
            res = await ac.post(
                f"/api/v1/incidents/{uuid4()}/approval",
                json={
                    "decision": "APPROVE",
                    "expected_incident_version": 1,
                    "plan_version": 1,
                    "idempotency_key": "k2",
                },
            )
            assert res.status_code == status.HTTP_403_FORBIDDEN
            assert "requires 'approver' capability" in res.text

            # 3. Operator cannot toggle automation mode (requires admin) -> 403
            res = await ac.post(
                "/api/v1/automation/mode", json={"mode": "AUTOMATIC", "reason": "test"}
            )
            assert res.status_code == status.HTTP_403_FORBIDDEN
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_approver_role_access_boundary():
    user, session = make_mock_auth(UserRole.APPROVER)

    async def mock_get_auth():
        return user, session

    async def mock_csrf():
        return None

    app.dependency_overrides[get_current_session_and_user] = mock_get_auth
    app.dependency_overrides[require_csrf] = mock_csrf

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 1. Approver can read automation status -> 200
            res = await ac.get("/api/v1/automation")
            assert res.status_code == status.HTTP_200_OK

            # 2. Approver cannot toggle automation mode (requires admin) -> 403
            res = await ac.post(
                "/api/v1/automation/mode", json={"mode": "AUTOMATIC", "reason": "test"}
            )
            assert res.status_code == status.HTTP_403_FORBIDDEN
            assert "requires 'admin' capability" in res.text

            # 3. Approver passes role check for approval endpoint (it will proceed past 403 to DB/404)
            res = await ac.post(
                f"/api/v1/incidents/{uuid4()}/approval",
                json={
                    "decision": "APPROVE",
                    "expected_incident_version": 1,
                    "plan_version": 1,
                    "idempotency_key": "k3",
                },
            )
            # Role check passed! (Status is 404 Incident not found, NOT 403 Forbidden)
            assert res.status_code != status.HTTP_403_FORBIDDEN
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_admin_role_access():
    user, session = make_mock_auth(UserRole.ADMIN)

    async def mock_get_auth():
        return user, session

    async def mock_csrf():
        return None

    app.dependency_overrides[get_current_session_and_user] = mock_get_auth
    app.dependency_overrides[require_csrf] = mock_csrf

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 1. Admin can read automation status -> 200
            res = await ac.get("/api/v1/automation")
            assert res.status_code == status.HTTP_200_OK

            # 2. Admin passes role check for admin endpoints
            # (fails later on DB lookup, but not on 403 role authorization)
            res = await ac.post(
                "/api/v1/automation/mode", json={"mode": "AUTOMATIC", "reason": "test"}
            )
            assert res.status_code != status.HTTP_403_FORBIDDEN

            res = await ac.post(
                f"/api/v1/incidents/{uuid4()}/approval",
                json={
                    "decision": "APPROVE",
                    "expected_incident_version": 1,
                    "plan_version": 1,
                    "idempotency_key": "k4",
                },
            )
            assert res.status_code != status.HTTP_403_FORBIDDEN
    finally:
        app.dependency_overrides.clear()
