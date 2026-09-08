import pytest
from fault_injector.main import app
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_unauthenticated_request_rejected():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        response = await ac.post("/faults/SCN-002/inject")
        assert response.status_code == 422

        response_bad_token = await ac.post(
            "/faults/SCN-002/inject",
            headers={"X-Fault-Token": "wrong-token"},
        )
        assert response_bad_token.status_code == 401
        assert response_bad_token.json()["detail"] == "Invalid or missing authentication secret"


@pytest.mark.asyncio
async def test_authenticated_request_accepted():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        response = await ac.get(
            "/faults/active",
            headers={"X-Fault-Token": "injector-secret-token"},
        )
        assert response.status_code == 200
        assert isinstance(response.json(), list)
