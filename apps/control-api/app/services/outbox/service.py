import json
import logging
from typing import Any
from uuid import UUID

from app.db.repositories.control_plane import unit_of_work
from app.workers.incidentpublisher import IncidentPublisher
from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


class OutboxService:
    """Service responsible for sweeping and dispatching pending transactional outbox events."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        publisher: IncidentPublisher | None = None,
        actor: str = "worker:outbox",
    ):
        self.session_factory = session_factory
        self.publisher = publisher or IncidentPublisher()
        self.actor = actor

    async def sweep_and_publish(self, batch_size: int = 50) -> int:
        """Sweeps up to batch_size pending outbox events and publishes them to Redis Streams."""
        processed_count = 0

        async with unit_of_work(self.session_factory, actor=self.actor) as repo:
            events = await repo.fetch_pending_outbox_events(limit=batch_size)
            if not events:
                return 0

            for event in events:
                try:
                    payload = dict(event.payload)
                    fields: dict[str, str] = {
                        "event_type": str(event.event_type),
                        "aggregate_type": str(event.aggregate_type),
                        "aggregate_id": str(event.aggregate_id),
                        "incident_id": str(event.aggregate_id),
                        "aggregate_version": str(event.aggregate_version),
                        "version": str(event.aggregate_version),
                    }
                    for k, v in payload.items():
                        if isinstance(v, (dict, list)):
                            fields[k] = json.dumps(v)
                        else:
                            fields[k] = str(v)

                    await self.publisher.publish_with_retry(
                        stream_key=self.publisher.stream_key,
                        fields=fields,
                        max_retries=3,
                        dlq_key=self.publisher.dlq_key,
                    )
                    await repo.mark_outbox_published(event.id)
                    processed_count += 1
                except Exception as exc:
                    logger.error(
                        f"Failed to publish outbox event {event.id} ({event.event_type}): {exc}",
                        exc_info=True,
                    )
                    await repo.mark_outbox_failed(event.id, str(exc))

        return processed_count
