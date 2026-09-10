from datetime import UTC, datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import sqlalchemy as sa
from app.db.models import EvidenceItem, Incident, Resource
from app.db.repositories.control_plane import unit_of_work
from diagnosis.evidence.collector import EvidenceCollector
from diagnosis.evidence.container_reader import MockContainerReader
from sharedmodels.enums import EvidenceKind, Severity
from telemetryclient.loki import LokiClient
from telemetryclient.prometheus import PrometheusClient
from telemetryclient.schemas import (
    ContainerInspectionResult,
    LogEntry,
    LogQueryResult,
    MetricQueryResult,
    MetricSample,
    MetricValue,
)


@pytest.mark.asyncio
async def test_evidence_collection_end_to_end(session_factory):
    """Verifies bounded collection, units, redaction, SHA-256, and PostgreSQL storage."""
    async with unit_of_work(session_factory, actor="test:collector") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        assert res is not None

        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-evidence-coll-{uuid4().hex[:8]}",
            severity=Severity.HIGH,
        )

        # Mock telemetry clients
        mock_prom = PrometheusClient()
        mock_prom.query_instant = AsyncMock(
            return_value=MetricQueryResult(
                query='up{job="demo-api"}',
                status="success",
                samples=[
                    MetricSample(
                        metric={"job": "demo-api"},
                        values=[MetricValue(timestamp=datetime.now(UTC), value=1.0)],
                        latest_value=1.0,
                    )
                ],
                freshness_seconds=5.0,
            )
        )
        mock_prom.get_active_alerts = AsyncMock(return_value=[])

        mock_loki = LokiClient()
        mock_loki.query_range = AsyncMock(
            return_value=LogQueryResult(
                query='{job="demo-api"}',
                status="success",
                entries=[
                    LogEntry(
                        timestamp=datetime.now(UTC),
                        line="Authorization: Bearer my_secret_token_123 received",
                    )
                ],
                total_lines=1,
            )
        )

        mock_reader = MockContainerReader()
        mock_reader.set_state(
            "c1_test",
            ContainerInspectionResult(
                container_id="c1_test",
                name="demo-api",
                state="running",
                exit_code=None,
                health_status="healthy",
                binding_generation=1,
                status="success",
            ),
        )

        collector = EvidenceCollector(
            prometheus_client=mock_prom,
            loki_client=mock_loki,
            container_reader=mock_reader,
        )

        bundle = await collector.collect(
            incident_id=inc.id,
            resource_id=res.id,
            target_service="demo-api",
            target_container_id="c1_test",
            binding_generation=1,
            session=repo.session,
            actor="test:collector",
        )

        assert bundle.incident_id == inc.id
        assert bundle.resource_id == res.id
        assert len(bundle.items) > 0
        assert bundle.completeness["docker"] == "COMPLETE"
        assert bundle.completeness["prometheus"] == "COMPLETE"
        assert bundle.completeness["loki"] == "COMPLETE"

        # Verify unit preservation
        container_item = next(i for i in bundle.items if i.kind == EvidenceKind.CONTAINER_STATE)
        assert container_item.binding_generation == 1
        assert container_item.content["state"] == "running"

        cpu_item = next(i for i in bundle.items if "cpu_rate" in i.source)
        assert cpu_item.unit == "cores"

        log_item = next(i for i in bundle.items if i.kind == EvidenceKind.LOGS)
        assert log_item.unit == "lines"
        # Verify redaction occurred
        assert "my_secret_token_123" not in str(log_item.content)
        assert "Bearer [REDACTED]" in str(log_item.content)

        # Verify SHA-256 digest is present and valid 64-char hex
        for item in bundle.items:
            assert len(item.sha256) == 64
            assert all(c in "0123456789abcdef" for c in item.sha256)

        # Persist items to database
        for item in bundle.items:
            db_item = EvidenceItem(
                id=item.id,
                incident_id=item.incident_id,
                kind=item.kind.value if hasattr(item.kind, "value") else str(item.kind),
                source=item.source,
                observed_at=item.observed_at,
                unit=item.unit,
                binding_generation=item.binding_generation,
                content=item.content,
                sha256=item.sha256,
                actor="test:collector",
            )
            await repo.add(db_item)

        # Verify stored records in DB
        db_items = list(
            (
                await repo.session.scalars(
                    sa.select(EvidenceItem).where(EvidenceItem.incident_id == inc.id)
                )
            ).all()
        )
        assert len(db_items) == len(bundle.items)
        db_cpu = next(i for i in db_items if "cpu_rate" in i.source)
        assert db_cpu.unit == "cores"


@pytest.mark.asyncio
async def test_bounded_collection_retry_mechanism():
    """Verifies that transient scrape errors are retried up to MAX_COLLECTION_RETRIES."""
    mock_prom = PrometheusClient()
    # Fails on first attempt, succeeds on second
    attempt_count = 0

    async def flaky_query(*args, **kwargs):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            return MetricQueryResult(query="test", status="unavailable", error_message="timeout")
        return MetricQueryResult(
            query="test",
            status="success",
            samples=[
                MetricSample(
                    values=[MetricValue(timestamp=datetime.now(UTC), value=1.0)], latest_value=1.0
                )
            ],
        )

    mock_prom.query_instant = AsyncMock(side_effect=flaky_query)
    mock_prom.get_active_alerts = AsyncMock(return_value=[])

    collector = EvidenceCollector(
        prometheus_client=mock_prom,
        container_reader=MockContainerReader(),
        max_retries=2,
    )

    inc_id = uuid4()
    res_id = uuid4()
    bundle = await collector.collect(incident_id=inc_id, resource_id=res_id)

    assert bundle.completeness["prometheus"] == "COMPLETE"
    assert attempt_count > 1  # Proves retry occurred


@pytest.mark.asyncio
async def test_deployment_history_unavailable_handling():
    """Verifies 60m deployment window explicitly marks UNAVAILABLE when session is absent."""
    collector = EvidenceCollector(container_reader=MockContainerReader())
    bundle = await collector.collect(
        incident_id=uuid4(),
        resource_id=uuid4(),
        session=None,  # No session provided
    )

    assert bundle.deployment_history_status == "UNAVAILABLE"
    deploy_item = next(i for i in bundle.items if i.kind == EvidenceKind.RESOURCE_CHANGES)
    assert deploy_item.content["status"] == "UNAVAILABLE"
    assert "No database session" in deploy_item.content["reason"]
