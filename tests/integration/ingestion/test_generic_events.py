import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from app.db.models import Event, Incident, IncidentEvent
from app.main import app
from httpx import ASGITransport, AsyncClient
from sharedmodels.enums import IncidentState

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "generic-events"
AUTH_HEADER = {"Authorization": "Bearer generic-secret-token"}


@pytest.mark.asyncio
async def test_generic_event_authentication(session_factory):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Missing authentication
        res = await client.post("/api/v1/events/generic", json={})
        assert res.status_code == 401

        # 2. Invalid secret
        res = await client.post(
            "/api/v1/events/generic",
            json={},
            headers={"Authorization": "Bearer bad-token"},
        )
        assert res.status_code == 401

        # 3. Valid bearer token
        with (FIXTURES_DIR / "health_failure.json").open() as f:
            payload = json.load(f)

        res = await client.post(
            "/api/v1/events/generic",
            json=payload,
            headers={"Authorization": "Bearer generic-secret-token"},
        )
        assert res.status_code == 200

        # 4. Valid X-API-Key header
        res = await client.post(
            "/api/v1/events/generic",
            json=payload,
            headers={"X-API-Key": "generic-secret-token"},
        )
        assert res.status_code == 200


@pytest.mark.asyncio
async def test_generic_event_malformed_payload(session_factory):
    with (FIXTURES_DIR / "malformed_generic_event.json").open() as f:
        malformed = json.load(f)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/v1/events/generic", json=malformed, headers=AUTH_HEADER)
        assert res.status_code == 422


@pytest.mark.asyncio
async def test_generic_event_redaction_at_ingestion(session_factory):
    with (FIXTURES_DIR / "sensitive_generic_event.json").open() as f:
        payload = json.load(f)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/v1/events/generic", json=payload, headers=AUTH_HEADER)
        assert res.status_code == 200
        event_id = UUID(res.json()["event_id"])

    async with session_factory() as session:
        event = await session.get(Event, event_id)
        assert event is not None

        payload_str = json.dumps(event.payload)
        raw_str = json.dumps(event.raw_payload)

        # Ensure secrets are scrubbed
        assert "super_secret_password" not in payload_str
        assert "super_secret_password" not in raw_str
        assert "secret_token_12345" not in payload_str
        assert "secret_token_12345" not in raw_str
        assert "sensitive_auth_token_9999" not in payload_str
        assert "sensitive_auth_token_9999" not in raw_str

        assert "[REDACTED]" in payload_str
        assert "[REDACTED]" in raw_str


@pytest.mark.asyncio
async def test_generic_event_active_incident_correlation(session_factory):
    scenario_id = f"SCN-GEN-{uuid4().hex[:6]}"

    event1 = {
        "source": "GENERIC",
        "event_type": "WorkerHeartbeatMissed",
        "severity": "HIGH",
        "service": "demo-worker",
        "scenario_id": scenario_id,
        "message": "Heartbeat missed",
    }
    event2 = {
        "source": "GENERIC",
        "event_type": "QueueDepthSpike",
        "severity": "HIGH",
        "service": "demo-worker",
        "scenario_id": scenario_id,
        "message": "Queue depth backlog exceeded threshold",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # First event initializes incident in DETECTED
        res1 = await client.post("/api/v1/events/generic", json=event1, headers=AUTH_HEADER)
        assert res1.status_code == 200
        body1 = res1.json()
        assert body1["was_created"] is True
        assert body1["action"] == "incident_created"
        incident_id = body1["incident_id"]

        # Second event correlates to the same active incident
        res2 = await client.post("/api/v1/events/generic", json=event2, headers=AUTH_HEADER)
        assert res2.status_code == 200
        body2 = res2.json()
        assert body2["was_created"] is False
        assert body2["action"] == "incident_correlated"
        assert body2["incident_id"] == incident_id

    # Verify database
    async with session_factory() as session:
        incidents = (
            await session.scalars(
                sa.select(Incident).where(Incident.correlation_key.like(f"%:{scenario_id}"))
            )
        ).all()
        assert len(incidents) == 1
        incident = incidents[0]
        assert str(incident.id) == incident_id
        assert incident.state == IncidentState.DETECTED

        linked = (
            await session.scalars(
                sa.select(IncidentEvent).where(IncidentEvent.incident_id == incident.id)
            )
        ).all()
        assert len(linked) == 2
