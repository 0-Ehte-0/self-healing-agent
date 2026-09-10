import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import redis.asyncio as aioredis
import sqlalchemy as sa
from app.core.config import get_settings
from app.db.models import Incident, OutboxEvent, Resource
from app.db.repositories.control_plane import unit_of_work
from app.services.correlation.engine import CorrelationEngine
from app.services.ingestion.schemas import NormalizedEventPayload
from app.services.outbox.service import OutboxService
from app.workers.incidentpublisher import IncidentPublisher
from sharedmodels.enums import EventSource, Severity
from sharedmodels.enums import IncidentState as S


@pytest.mark.asyncio
async def test_incident_creation_and_approval_outbox_durability(session_factory):
    """Verifies that incident creation and approval transitions write durable outbox events."""
    settings = get_settings()

    async with unit_of_work(session_factory, actor="test:outbox") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        assert res is not None

        # 1. Incident creation via CorrelationEngine
        engine = CorrelationEngine(repo)
        now = datetime.now(UTC)
        norm_payload = NormalizedEventPayload(
            source=EventSource.ALERTMANAGER,
            resource_id=res.id,
            event_type="ContainerStopped",
            severity=Severity.HIGH,
            occurred_at=now,
            dedup_window=now,
            fingerprint=uuid4().hex + uuid4().hex,
            payload={"status": "firing"},
            raw_payload={"status": "firing"},
        )
        ingest_res = await engine.ingest_event(norm_payload)
        assert ingest_res.was_created is True
        incident_id = ingest_res.incident.id

        # Verify incident.detected was added to outbox in the same transaction
        outbox_detected = await repo.session.scalar(
            sa.select(OutboxEvent).where(
                OutboxEvent.aggregate_id == incident_id,
                OutboxEvent.event_type == "incident.detected",
            )
        )
        assert outbox_detected is not None
        assert outbox_detected.status == "PENDING"
        assert outbox_detected.aggregate_version == 1

        # 2. Advance an incident with approval_required=True to APPROVED
        approval_inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-approval-outbox-{uuid4().hex}",
            severity=Severity.HIGH,
            approval_required=True,
        )
        await repo.transition(approval_inc.id, 1, S.TRIAGED)
        await repo.transition(approval_inc.id, 2, S.DIAGNOSED)
        await repo.transition(approval_inc.id, 3, S.PLANNED)
        await repo.transition(approval_inc.id, 4, S.PENDING_APPROVAL)
        approved_inc = await repo.transition(approval_inc.id, 5, S.APPROVED)
        assert approved_inc.state == S.APPROVED

        # Verify incident.approved was added to outbox in the exact same transaction
        outbox_approved = await repo.session.scalar(
            sa.select(OutboxEvent).where(
                OutboxEvent.aggregate_id == approval_inc.id,
                OutboxEvent.event_type == "incident.approved",
            )
        )
        assert outbox_approved is not None
        assert outbox_approved.status == "PENDING"
        assert outbox_approved.aggregate_version == 6

    # 3. Sweep and publish with OutboxService to live Redis
    publisher = IncidentPublisher(stream_key="test:stream:outbox_durability")
    outbox_svc = OutboxService(session_factory=session_factory, publisher=publisher)

    try:
        published_count = await outbox_svc.sweep_and_publish(batch_size=10)
        assert published_count >= 2

        # Verify records are now marked PUBLISHED
        async with unit_of_work(session_factory, actor="test:verify") as repo:
            events = list(
                (
                    await repo.session.scalars(
                        sa.select(OutboxEvent).where(OutboxEvent.aggregate_id == incident_id)
                    )
                ).all()
            )
            for ev in events:
                assert ev.status == "PUBLISHED"
                assert ev.published_at is not None

        # Verify Redis stream contains the messages
        redis_client = await publisher.get_redis()
        messages = await redis_client.xrange("test:stream:outbox_durability")
        assert len(messages) >= 2
        types = [m[1]["event_type"] for m in messages]
        assert "incident.detected" in types
        assert "incident.approved" in types

    finally:
        await publisher.close()


@pytest.mark.asyncio
async def test_outbox_durability_when_redis_is_unavailable(session_factory):
    """Verifies that DB transactions succeed even when Redis publishing fails."""
    # Point publisher to an invalid / unreachable Redis port
    failing_publisher = IncidentPublisher(
        redis_client=aioredis.from_url("redis://127.0.0.1:59999/0", decode_responses=True),
        stream_key="test:unreachable",
    )

    async with unit_of_work(session_factory, actor="test:offline_redis") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        assert res is not None

        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-redis-down-{uuid4().hex}",
            severity=Severity.CRITICAL,
        )
        # Add outbox event directly in transaction
        await repo.add_outbox_event(
            event_type="incident.detected",
            aggregate_id=inc.id,
            aggregate_version=inc.version,
            payload={"incident_id": str(inc.id)},
        )

    # Now attempt to sweep with failing publisher: outbox marks FAILED or retains PENDING
    outbox_svc = OutboxService(session_factory=session_factory, publisher=failing_publisher)
    try:
        await outbox_svc.sweep_and_publish(batch_size=1)
    except Exception:
        pass
    finally:
        await failing_publisher.close()

    # Verify event remains durable in database
    async with unit_of_work(session_factory, actor="test:verify") as repo:
        ev = await repo.session.scalar(
            sa.select(OutboxEvent).where(OutboxEvent.aggregate_id == inc.id)
        )
        assert ev is not None
        assert ev.status in ("PENDING", "FAILED")
        assert ev.published_at is None
