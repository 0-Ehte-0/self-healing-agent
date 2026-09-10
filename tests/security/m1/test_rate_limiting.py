import pytest
from app.core.security import rate_limiter
from app.main import app
from fastapi import status
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_login_rate_limiting_enforcement():
    # Reset limiter for clean state
    rate_limiter.reset()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # First 10 rapid attempts are allowed to attempt authentication (return 401 for bad creds)
        for i in range(10):
            res = await ac.post(
                "/api/v1/auth/login",
                json={"username": "target_user", "password": "wrong_password"},
            )
            assert res.status_code == status.HTTP_401_UNAUTHORIZED

        # The 11th request must be rate limited with 429 Too Many Requests
        res = await ac.post(
            "/api/v1/auth/login",
            json={"username": "target_user", "password": "wrong_password"},
        )
        assert res.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert "Too many login attempts" in res.text
