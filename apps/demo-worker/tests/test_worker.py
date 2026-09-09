import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from demo_worker.main import (
    WORKER_QUEUE_DEPTH,
    consume_stream_events,
    get_stream_backlog,
    handle_event,
)


@pytest.mark.asyncio
async def test_handle_event():
    await handle_event("1000-0", {"job_id": "abc-123", "payload": "task"})


@pytest.mark.asyncio
async def test_get_stream_backlog_via_xinfo_groups():
    mock_redis = AsyncMock()
    mock_redis.xinfo_groups.return_value = [
        {"name": "demo-worker-group", "lag": 42, "pending": 8},
        {"name": "other-group", "lag": 0, "pending": 0},
    ]

    backlog = await get_stream_backlog(mock_redis, "demo:jobs", "demo-worker-group")
    assert backlog == 50


@pytest.mark.asyncio
async def test_get_stream_backlog_fallback_to_xpending():
    mock_redis = AsyncMock()
    mock_redis.xinfo_groups.side_effect = RuntimeError("xinfo_groups not supported")
    mock_redis.xpending.return_value = {"pending": 15, "min": "1-0", "max": "2-0", "consumers": []}

    backlog = await get_stream_backlog(mock_redis, "demo:jobs", "demo-worker-group")
    assert backlog == 15


@pytest.mark.asyncio
async def test_consume_stream_events_single_batch():
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None
    mock_redis.xinfo_groups.return_value = [{"name": "demo-worker-group", "lag": 5, "pending": 2}]
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
        assert WORKER_QUEUE_DEPTH._value.get() == 7
