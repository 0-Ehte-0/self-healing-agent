from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fault_injector.faults.baddeployment import BadDeploymentFault
from fault_injector.faults.cpustress import CpuStressFault
from fault_injector.faults.errorrate import ElevatedErrorRateFault
from fault_injector.faults.healthhang import HealthHangFault
from fault_injector.faults.latency import LatencyFault
from fault_injector.faults.memorypressure import MemoryPressureFault
from fault_injector.faults.workerpause import WorkerPauseFault


@pytest.mark.asyncio
async def test_cpu_stress_execution():
    fault = CpuStressFault(scenario_id="SCN-002")
    assert not fault.is_active()

    mock_post = AsyncMock()
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = {"status": "started"}
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    with patch("httpx.AsyncClient.post", mock_post):
        await fault.inject()
        assert fault.is_active()
        mock_post.assert_awaited_once_with(
            "/_faults/cpu/inject?cores=1",
            headers={"X-Fault-Token": "injector-secret-token"},
        )

        mock_post.reset_mock()
        await fault.clear()
        assert not fault.is_active()
        mock_post.assert_awaited_once_with(
            "/_faults/cpu/clear",
            headers={"X-Fault-Token": "injector-secret-token"},
        )


@pytest.mark.asyncio
async def test_health_hang_execution():
    fault = HealthHangFault(scenario_id="SCN-003")
    assert not fault.is_active()

    mock_post = AsyncMock()
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = {"status": "injected"}
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    mock_redis = AsyncMock()
    with (
        patch("httpx.AsyncClient.post", mock_post),
        patch("redis.asyncio.Redis.from_url", return_value=mock_redis),
    ):
        await fault.inject()
        assert fault.is_active()
        mock_post.assert_awaited_once_with(
            "/_faults/hang/inject",
            headers={"X-Fault-Token": "injector-secret-token"},
        )

        mock_post.reset_mock()
        await fault.clear()
        assert not fault.is_active()
        mock_post.assert_awaited_once_with(
            "/_faults/hang/clear",
            headers={"X-Fault-Token": "injector-secret-token"},
        )
        mock_redis.delete.assert_awaited_with("fault:health_hang")


@pytest.mark.asyncio
async def test_memory_pressure_execution():
    fault = MemoryPressureFault(scenario_id="SCN-005", megabytes=20)
    await fault.inject()
    assert fault.is_active()
    assert len(fault._allocated_chunks) == 2

    await fault.clear()
    assert not fault.is_active()
    assert len(fault._allocated_chunks) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault_class,scenario_id,expected_key",
    [
        (LatencyFault, "SCN-004", "fault:latency"),
        (WorkerPauseFault, "SCN-008", "fault:worker_pause"),
        (ElevatedErrorRateFault, "SCN-009", "fault:error_rate"),
        (BadDeploymentFault, "SCN-010", "fault:bad_deployment"),
    ],
)
async def test_redis_backed_faults(fault_class, scenario_id, expected_key):
    mock_redis = AsyncMock()

    with patch("redis.asyncio.Redis.from_url", return_value=mock_redis):
        fault = fault_class(scenario_id=scenario_id)

        await fault.inject()
        assert fault.is_active()
        mock_redis.set.assert_awaited()
        call_key = mock_redis.set.await_args[0][0]
        assert call_key == expected_key

        await fault.clear()
        assert not fault.is_active()
        mock_redis.delete.assert_awaited_with(expected_key)
