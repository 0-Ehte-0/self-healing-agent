from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import sqlalchemy as sa
from agent_worker.consumer import IncidentStreamConsumer
from agentcore.graph.state import IncidentGraphState
from agentcore.runtime.runner import WorkflowRunner
from app.db.models import Incident, Resource
from app.db.repositories.control_plane import unit_of_work
from sharedmodels.enums import IncidentState as S
from sharedmodels.enums import Severity


@pytest.mark.asyncio
async def test_stream_deduplication_by_incident_and_version(session_factory):
    """Verifies that duplicate stream messages for the same (incident_id, version) do not duplicate node runs."""
    async with unit_of_work(session_factory, actor="test:dedup") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        assert res is not None

        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-dedup-{uuid4().hex}",
            severity=Severity.HIGH,
        )
        incident_id = inc.id
        version = inc.version

    run_mock = AsyncMock(return_value={"status": "DIAGNOSED", "version": version + 1})
    runner = WorkflowRunner(session_factory=session_factory, worker_id="test-worker-dedup")
    runner.run_incident = run_mock

    consumer = IncidentStreamConsumer(
        runner=runner,
        redis_url="redis://localhost:6379/0",
        stream_key=f"test:stream:dedup:{uuid4().hex[:8]}",
        group_name="test-dedup-group",
        consumer_name="test-consumer-1",
    )

    redis_client = AsyncMock()
    redis_client.xack = AsyncMock()

    fields = {
        "event_type": "incident.detected",
        "incident_id": str(incident_id),
        "version": str(version),
        "resource_id": str(res.id),
    }

    # 1. First message delivery
    await consumer._process_message(redis_client, "1-0", fields)
    assert run_mock.await_count == 1
    assert redis_client.xack.await_count == 1

    # 2. Duplicate message delivery with same version
    await consumer._process_message(redis_client, "2-0", fields)
    # Runner must NOT be invoked again
    assert run_mock.await_count == 1
    # Message should still be acknowledged
    assert redis_client.xack.await_count == 2

    # 3. Third duplicate message delivery
    await consumer._process_message(redis_client, "3-0", fields)
    assert run_mock.await_count == 1
    assert redis_client.xack.await_count == 3

    # 4. Message delivery for newer version (version 2)
    fields_v2 = {**fields, "version": str(version + 1)}
    await consumer._process_message(redis_client, "4-0", fields_v2)
    assert run_mock.await_count == 2
    assert redis_client.xack.await_count == 4
