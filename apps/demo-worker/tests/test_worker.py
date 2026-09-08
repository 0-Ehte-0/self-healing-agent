import asyncio
from unittest.mock import AsyncMock, patch
import pytest
from demo_worker.main import consume_stream_events, handle_event


@pytest.mark.asyncio
async def test_handle_event():
    await handle_event("1000-0", {"job_id": "abc-123", "payload": "task"})


@pytest.mark.asyncio
async def test_consume_stream_events_single_batch():
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None
    mock_redis.xlen.return_value = 1
    mock_redis.xreadgroup.side_effect = [
        [("demo:jobs", [("1000-0", {"job_id": "abc-123", "payload": "task"})])],
        asyncio.CancelledError(),
    ]

    with patch("demo_worker.main.handle_event", new_callable=AsyncMock) as mock_handler:
        await consume_stream_events(mock_redis)

        mock_handler.assert_awaited_once_with(
            "1000-0",
            {"job_id": "abc-123", "payload": "task"},
        )
        mock_redis.xack.assert_awaited_once_with(
            "demo:jobs",
            "demo-worker-group",
            "1000-0",
        )