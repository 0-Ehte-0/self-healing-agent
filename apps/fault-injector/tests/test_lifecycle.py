import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from fault_injector.main import app, ACTIVE_FAULTS, FAULT_METADATA, FAULT_TIMERS


@pytest.fixture(autouse=True)
async def cleanup_faults():
    yield
    for task in FAULT_TIMERS.values():
        task.cancel()
    ACTIVE_FAULTS.clear()
    FAULT_METADATA.clear()
    FAULT_TIMERS.clear()


@pytest.mark.asyncio
async def test_fault_lifecycle_inject_and_clear():
    headers = {"X-Fault-Token": "injector-secret-token"}

    with patch(
        "fault_injector.faults.cpustress.CpuStressFault.inject",
        new_callable=AsyncMock,
    ) as mock_inject, \
         patch(
             "fault_injector.faults.cpustress.CpuStressFault.clear",
             new_callable=AsyncMock,
         ) as mock_clear:

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            inject_resp = await ac.post(
                "/faults/SCN-002/inject?ttl_seconds=300",
                headers=headers,
            )
            assert inject_resp.status_code == 200
            assert inject_resp.json()["status"] == "injected"
            assert mock_inject.await_count == 1

            active_resp = await ac.get("/faults/active", headers=headers)
            assert active_resp.status_code == 200
            active_list = active_resp.json()
            assert len(active_list) == 1
            assert active_list[0]["scenario_id"] == "SCN-002"

            clear_resp = await ac.post("/faults/SCN-002/clear", headers=headers)
            assert clear_resp.status_code == 200
            assert clear_resp.json()["status"] == "cleared"
            assert mock_clear.await_count == 1

            final_active = await ac.get("/faults/active", headers=headers)
            assert len(final_active.json()) == 0


@pytest.mark.asyncio
async def test_fault_auto_expiry():
    headers = {"X-Fault-Token": "injector-secret-token"}

    with patch(
        "fault_injector.faults.latency.LatencyFault.inject",
        new_callable=AsyncMock,
    ), \
         patch(
             "fault_injector.faults.latency.LatencyFault.clear",
             new_callable=AsyncMock,
         ) as mock_clear:

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            resp = await ac.post(
                "/faults/SCN-004/inject?ttl_seconds=1",
                headers=headers,
            )
            assert resp.status_code == 200

            assert len((await ac.get("/faults/active", headers=headers)).json()) == 1

            await asyncio.sleep(1.2)

            assert len((await ac.get("/faults/active", headers=headers)).json()) == 0
            assert mock_clear.await_count == 1