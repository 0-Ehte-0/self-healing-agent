from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from demo_api.db import get_db
from demo_api.main import app
from demo_api.redis import get_redis
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
def reset_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health_live_ok():
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None

    app.dependency_overrides[get_redis] = lambda: mock_redis

    with patch("demo_api.main.redis_client", mock_redis):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            res = await ac.get("/health/live")
            assert res.status_code == 200
            assert res.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_jobs_creation_flow():
    mock_db = AsyncMock()
    mock_db.add = MagicMock()  # Synchronous session method
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None

    async def override_get_db():
        yield mock_db

    async def override_get_redis():
        yield mock_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    with patch("demo_api.main.redis_client", mock_redis):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            res = await ac.post(
                "/jobs",
                json={"payload": "test-data"},
                headers={"X-Scenario-ID": "SCN-000"},
            )
            assert res.status_code == 200
            assert res.json()["status"] == "QUEUED"
            assert res.headers.get("X-Scenario-ID") == "SCN-000"
            mock_db.add.assert_called_once()
            mock_redis.xadd.assert_awaited_once()
