import asyncio
import json
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
import redis.asyncio as aioredis
import sqlalchemy as sa
from app.core.config import get_settings
from app.db.models import Event, Incident, IncidentEvent
from app.db.repositories.control_plane import unit_of_work
from app.main import app
from app.workers.incidentpublisher import IncidentPublisher
from httpx import ASGITransport, AsyncClient
from sharedmodels.enums import IncidentState

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "alertmanager"
AUTH_HEADER = {"Authorization": "Bearer alertmanager-secret-token"}


@pytest.mark.asyncio
async def test_alertmanager_authentication(session_factory):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Missing authentication
        res = await client.post("/api/v1/webhooks/alertmanager", json={})
        assert res.status_code == 401

        # 2. Invalid bearer token
        res = await client.post(
            "/api/v1/webhooks/alertmanager",
            json={},
            headers={"Authorization": "Bearer wrong-secret"},
        )
        assert res.status_code == 401

        # 3. Valid bearer token
        res = await client.post(
            "/api/v1/webhooks/alertmanager",
            json={},
            headers={"Authorization": "Bearer alertmanager-secret-token"},
        )
        assert res.status_code == 200

        # 4. Valid X-Webhook-Secret header
        res = await client.post(
            "/api/v1/webhooks/alertmanager",
            json={},
            headers={"X-Webhook-Secret": "alertmanager-secret-token"},
        )
        assert res.status_code == 200


@pytest.mark.asyncio
async def test_alertmanager_malformed_payload(session_factory):
    with (FIXTURES_DIR / "malformed_payload.json").open() as f:
        malformed = json.load(f)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/webhooks/alertmanager", json=malformed, headers=AUTH_HEADER
        )
        assert res.status_code == 422


@pytest.mark.asyncio
async def test_alertmanager_redaction_at_ingestion(session_factory):
    with (FIXTURES_DIR / "sensitive_data_alert.json").open() as f:
        payload = json.load(f)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/v1/webhooks/alertmanager", json=payload, headers=AUTH_HEADER)
        assert res.status_code == 200
        data = res.json()
        assert data["processed"] == 1
        event_id = UUID(data["event_ids"][0])

    # Verify directly in PostgreSQL that secrets are nowhere in persistent storage
    async with session_factory() as session:
        event = await session.get(Event, event_id)
        assert event is not None

        payload_str = json.dumps(event.payload)
        raw_str = json.dumps(event.raw_payload)

        # Credentials must NOT appear in payload or raw_payload
        assert "super_secret_password_123" not in payload_str
        assert "super_secret_password_123" not in raw_str
        assert "my_hidden_db_password" not in payload_str
        assert "my_hidden_db_password" not in raw_str
        assert "redis_secret_pass" not in payload_str
        assert "redis_secret_pass" not in raw_str
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in payload_str
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in raw_str

        # Redacted placeholders must appear
        assert "[REDACTED]" in payload_str
        assert "[REDACTED]" in raw_str


@pytest.mark.asyncio
async def test_alertmanager_replay_attack_deduplication(session_factory):
    with (FIXTURES_DIR / "single_firing.json").open() as f:
        payload = json.load(f)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # First delivery
        res1 = await client.post("/api/v1/webhooks/alertmanager", json=payload, headers=AUTH_HEADER)
        assert res1.status_code == 200
        data1 = res1.json()
        assert len(data1["created_incidents"]) == 1
        incident_id = data1["created_incidents"][0]

        # Replay attack: send identical payload immediately
        res2 = await client.post("/api/v1/webhooks/alertmanager", json=payload, headers=AUTH_HEADER)
        assert res2.status_code == 200
        data2 = res2.json()
        # Replay must NOT create another incident
        assert len(data2["created_incidents"]) == 0
        assert data2["correlated_incidents"] == [incident_id]

    # Verify database counts: exactly 1 event and 1 incident
    async with session_factory() as session:
        events = (
            await session.scalars(
                sa.select(Event).where(Event.payload["alertname"].as_string() == "ContainerDown")
            )
        ).all()
        assert len(events) == 1, "Duplicate event was not deduplicated"

        incidents = (
            await session.scalars(sa.select(Incident).where(Incident.id == UUID(incident_id)))
        ).all()
        assert len(incidents) == 1


@pytest.mark.asyncio
async def test_alertmanager_alert_storm_concurrency_creates_exactly_one_incident(
    session_factory,
):
    """50 simultaneous requests hitting the webhook concurrently under an alert storm
    must produce EXACTLY ONE incident in DETECTED state.
    """
    scenario_id = f"SCN-STORM-{uuid4().hex[:6]}"

    def make_alert_payload(index: int):
        return {
            "version": "4",
            "status": "firing",
            "receiver": "control-plane-webhook",
            "commonLabels": {
                "alertname": "HighCpuSaturation",
                "scenario_id": scenario_id,
                "service": "demo-api",
                "severity": "warning",
            },
            "commonAnnotations": {"summary": "CPU storm detected"},
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "HighCpuSaturation",
                        "scenario_id": scenario_id,
                        "service": "demo-api",
                        "instance": f"demo-api:{index}",
                        "severity": "warning",
                    },
                    "annotations": {"summary": f"CPU saturation node {index}"},
                    "startsAt": "2026-09-09T18:30:00.000Z",
                    "fingerprint": f"fp_storm_{index:04d}",
                }
            ],
        }

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", timeout=30.0
    ) as client:
        # Fire 50 requests simultaneously
        tasks = [
            client.post(
                "/api/v1/webhooks/alertmanager",
                json=make_alert_payload(i),
                headers=AUTH_HEADER,
            )
            for i in range(50)
        ]
        responses = await asyncio.gather(*tasks)

    # Every request must succeed
    assert all(r.status_code == 200 for r in responses)

    # Aggregate created vs correlated incidents across all 50 responses
    all_created = set()
    all_correlated = set()
    for r in responses:
        body = r.json()
        all_created.update(body.get("created_incidents", []))
        all_correlated.update(body.get("correlated_incidents", []))

    # Exactly 1 unique incident ID was created across all 50 concurrent requests
    assert len(all_created) == 1, f"Expected 1 created incident, got: {all_created}"
    created_id = list(all_created)[0]

    # Verify database state
    async with session_factory() as session:
        incidents = (
            await session.scalars(
                sa.select(Incident).where(Incident.correlation_key.like(f"%:{scenario_id}"))
            )
        ).all()
        assert len(incidents) == 1, f"Expected 1 incident in database, found {len(incidents)}"
        incident = incidents[0]
        assert str(incident.id) == created_id
        assert incident.state == IncidentState.DETECTED

        # Verify that all 50 events are linked to this single incident
        linked_events = (
            await session.scalars(
                sa.select(IncidentEvent).where(IncidentEvent.incident_id == incident.id)
            )
        ).all()
        assert len(linked_events) == 50, f"Expected 50 linked events, found {len(linked_events)}"


@pytest.mark.asyncio
async def test_alertmanager_resolved_alert_does_not_resurrect_incident(session_factory):
    """Resolved alerts must NOT resurrect closed/terminal incidents into DETECTED."""
    scenario_id = f"SCN-RES-{uuid4().hex[:6]}"

    firing_payload = {
        "version": "4",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "ContainerDown",
                    "scenario_id": scenario_id,
                    "service": "demo-api",
                    "severity": "critical",
                },
                "annotations": {"summary": "Container down"},
                "startsAt": "2026-09-09T18:40:00.000Z",
            }
        ],
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/webhooks/alertmanager", json=firing_payload, headers=AUTH_HEADER
        )
        assert res.status_code == 200
        incident_id = UUID(res.json()["created_incidents"][0])

    # Now transition incident through lifecycle to RESOLVED
    async with unit_of_work(session_factory, actor="test:resolver") as repo:
        inc = await repo.transition(incident_id, expected_version=1, target=IncidentState.TRIAGED)
        inc = await repo.transition(incident_id, expected_version=2, target=IncidentState.DIAGNOSED)
        inc = await repo.transition(incident_id, expected_version=3, target=IncidentState.PLANNED)
        inc = await repo.transition(incident_id, expected_version=4, target=IncidentState.EXECUTING)
        inc = await repo.transition(incident_id, expected_version=5, target=IncidentState.VERIFYING)
        inc = await repo.transition(incident_id, expected_version=6, target=IncidentState.RESOLVED)
        assert inc.state == IncidentState.RESOLVED

    # Now send resolved webhook notification
    resolved_payload = {
        "version": "4",
        "status": "resolved",
        "alerts": [
            {
                "status": "resolved",
                "labels": {
                    "alertname": "ContainerDown",
                    "scenario_id": scenario_id,
                    "service": "demo-api",
                    "severity": "critical",
                },
                "annotations": {"summary": "Container down resolved"},
                "endsAt": "2026-09-09T18:45:00.000Z",
            }
        ],
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/webhooks/alertmanager", json=resolved_payload, headers=AUTH_HEADER
        )
        assert res.status_code == 200
        data = res.json()
        # Must NOT create a new incident
        assert len(data["created_incidents"]) == 0

    # Ensure in DB that no incident is in DETECTED for this scenario
    async with session_factory() as session:
        detected = (
            await session.scalars(
                sa.select(Incident).where(
                    Incident.correlation_key.like(f"%:{scenario_id}"),
                    Incident.state == IncidentState.DETECTED,
                )
            )
        ).all()
        assert len(detected) == 0, "Resolved notification resurrected an incident in DETECTED!"


@pytest.mark.asyncio
async def test_redis_stream_publishing_and_dlq(session_factory):
    settings = get_settings()
    test_stream = f"test:stream:{uuid4().hex[:8]}"
    test_dlq = f"test:dlq:{uuid4().hex[:8]}"

    publisher = IncidentPublisher(stream_key=test_stream, dlq_key=test_dlq)
    r = await publisher.get_redis()

    try:
        # Create dummy incident to test stream publishing
        async with session_factory() as session:
            inc = await session.scalar(sa.select(Incident).limit(1))
            if inc is None:
                raise AssertionError("No incident found in database for testing")

            # 1. Normal publish
            msg_id = await publisher.publish_incident_created(inc, event_ids=[uuid4()])
            assert msg_id is not None

            # Read back from Redis Stream
            raw_entries = await r.xread({test_stream: "0-0"}, count=10)
            if not raw_entries:
                raise AssertionError(f"Expected entries in stream {test_stream}")

            # Cast away the union type so Pyrefly knows this is a non-null nested list
            stream_entries = cast(list[tuple[Any, list[tuple[Any, dict[str, Any]]]]], raw_entries)
            stream_name, messages = stream_entries[0]
            assert stream_name == test_stream
            assert len(messages) == 1

            fields = messages[0][1]
            assert fields["event_type"] == "incident.detected"
            assert fields["incident_id"] == str(inc.id)
            assert fields["state"] == inc.state.value

        # 2. DLQ fallback when stream publish fails
        # Simulate failure by attempting publish with invalid stream type
        await r.set(test_stream, "not-a-stream-value")
        dlq_id = await publisher.publish_with_retry(
            stream_key=test_stream,
            fields={"event_type": "incident.failed_publish"},
            max_retries=2,
            dlq_key=test_dlq,
        )
        assert dlq_id is not None

        # Verify DLQ received the failed message
        raw_dlq = await r.xread({test_dlq: "0-0"}, count=10)
        if not raw_dlq:
            raise AssertionError(f"Expected entries in DLQ {test_dlq}")

        dlq_entries = cast(list[tuple[Any, list[tuple[Any, dict[str, Any]]]]], raw_dlq)
        dlq_name, dlq_msgs = dlq_entries[0]
        assert dlq_name == test_dlq
        assert len(dlq_msgs) > 0

        dlq_fields = dlq_msgs[0][1]
        assert "target_stream" in dlq_fields
        assert dlq_fields["target_stream"] == test_stream

    finally:
        await r.delete(test_stream, test_dlq)
        await publisher.close()
