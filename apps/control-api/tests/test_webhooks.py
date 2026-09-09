import pytest
from app.main import app
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_alertmanager_webhook_success() -> None:
    payload = {
        "version": "4",
        "groupKey": '{}:{alertname="HighCpuSaturation"}',
        "status": "firing",
        "receiver": "control-plane-webhook",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "HighCpuSaturation",
                    "severity": "warning",
                    "scenario_id": "SCN-002",
                },
                "annotations": {
                    "summary": "High CPU saturation detected",
                },
            }
        ],
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/webhooks/alertmanager", json=payload)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_alertmanager_webhook_empty_payload() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/webhooks/alertmanager", json={})
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
