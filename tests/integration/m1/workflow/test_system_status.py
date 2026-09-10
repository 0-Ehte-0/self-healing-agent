import httpx
import pytest
from app.core.config import get_settings
from app.db.repositories.control_plane import unit_of_work
from app.main import app


@pytest.mark.asyncio
async def test_system_status_authentication_and_metrics(session_factory):
    """Verifies that GET /api/v1/system/status requires secret Bearer auth and reports live status."""
    settings = get_settings()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # 1. Reject unauthenticated request
        res_no_auth = await client.get("/api/v1/system/status")
        assert res_no_auth.status_code == 401

        # 2. Reject incorrect token
        res_bad_auth = await client.get(
            "/api/v1/system/status",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert res_bad_auth.status_code == 401

        # 3. Accept valid token
        auth_headers = {"Authorization": f"Bearer {settings.SYSTEM_STATUS_SECRET}"}
        res_valid = await client.get("/api/v1/system/status", headers=auth_headers)
        assert res_valid.status_code == 200
        data = res_valid.json()
        assert "status" in data
        assert "database_connected" in data
        assert "redis_connected" in data
        assert "outbox_backlog" in data
        assert "worker_heartbeats" in data

        # 4. Insert heartbeat into PostgreSQL and verify it is returned
        async with unit_of_work(session_factory, actor="test:sys") as repo:
            await repo.record_worker_heartbeat(
                worker_id="worker-test-sys-status",
                status="HEALTHY",
                metadata={"test_run": True},
            )

        res_with_hb = await client.get("/api/v1/system/status", headers=auth_headers)
        assert res_with_hb.status_code == 200
        hb_data = res_with_hb.json()
        worker_ids = [w["worker_id"] for w in hb_data["worker_heartbeats"]]
        assert "worker-test-sys-status" in worker_ids
