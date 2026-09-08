import pytest
from fault_injector.main import (
    ACTIVE_FAULTS,
    FAULT_METADATA,
    FAULT_REGISTRY,
    FAULT_TIMERS,
    app,
)
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
async def reset_fault_state():
    yield
    for task in list(FAULT_TIMERS.values()):
        task.cancel()
    ACTIVE_FAULTS.clear()
    FAULT_METADATA.clear()
    FAULT_TIMERS.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_id", list(FAULT_REGISTRY.keys()))
async def test_full_scenario_lifecycle_all_ten(scenario_id: str, monkeypatch):
    fault_cls = FAULT_REGISTRY[scenario_id]

    async def _async_noop(self):
        return None

    monkeypatch.setattr(fault_cls, "inject", _async_noop)
    monkeypatch.setattr(fault_cls, "clear", _async_noop)

    headers = {"X-Fault-Token": "injector-secret-token"}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        # 1. Trigger injection
        inject_resp = await ac.post(
            f"/faults/{scenario_id}/inject?ttl_seconds=300",
            headers=headers,
        )
        assert inject_resp.status_code == 200, f"Failed injecting {scenario_id}"
        data = inject_resp.json()
        assert data["status"] == "injected"
        assert data["scenario_id"] == scenario_id
        assert data["ttl_seconds"] == 300

        # 2. Check active listing
        active_resp = await ac.get("/faults/active", headers=headers)
        assert active_resp.status_code == 200
        active_ids = [item["scenario_id"] for item in active_resp.json()]
        assert scenario_id in active_ids

        # 3. Reject duplicate injection
        dup_resp = await ac.post(f"/faults/{scenario_id}/inject", headers=headers)
        assert dup_resp.status_code == 200
        assert dup_resp.json()["status"] == "already_active"

        # 4. Clear fault
        clear_resp = await ac.post(f"/faults/{scenario_id}/clear", headers=headers)
        assert clear_resp.status_code == 200
        assert clear_resp.json()["status"] == "cleared"

        # 5. Verify emptied state
        post_clear_resp = await ac.get("/faults/active", headers=headers)
        remaining_ids = [item["scenario_id"] for item in post_clear_resp.json()]
        assert scenario_id not in remaining_ids
