import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis
from app.core.config import get_settings
from app.db.models import Incident

logger = logging.getLogger(__name__)


class IncidentPublisher:
    """Publishes incident lifecycle events to Redis Streams with retries and DLQ."""

    def __init__(
        self,
        redis_client: aioredis.Redis | None = None,
        stream_key: str | None = None,
        dlq_key: str | None = None,
    ):
        settings = get_settings()
        self.stream_key = stream_key or settings.INCIDENT_STREAM_KEY
        self.dlq_key = dlq_key or settings.INCIDENT_DLQ_KEY
        self._external_client = redis_client is not None
        self._redis = redis_client
        self._redis_url = str(settings.REDIS_URL)

    async def get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    async def close(self) -> None:
        if not self._external_client and self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def publish_with_retry(
        self,
        stream_key: str,
        fields: dict[str, str],
        max_retries: int = 3,
        dlq_key: str | None = None,
    ) -> str:
        """Publishes a field-value dict to a Redis Stream with exponential backoff and DLQ routing."""
        last_error: Exception | None = None
        target_dlq = dlq_key or self.dlq_key

        for attempt in range(1, max_retries + 1):
            try:
                client = await self.get_redis()
                # pyrefly: ignore [no-matching-overload]
                message_id = await client.xadd(stream_key, fields)
                logger.info(
                    f"Published to stream '{stream_key}' [msg_id={message_id}] on attempt {attempt}"
                )
                return message_id
            except Exception as e:
                last_error = e
                logger.warning(
                    f"Publish attempt {attempt}/{max_retries} to '{stream_key}' failed: {e}"
                )
                if attempt < max_retries:
                    backoff = 0.05 * (2 ** (attempt - 1))
                    await asyncio.sleep(backoff)

        # Retries exhausted - route to Dead Letter Queue
        logger.error(
            f"All {max_retries} publish attempts to '{stream_key}' failed. Routing to DLQ '{target_dlq}'"
        )
        dlq_payload = {
            "target_stream": stream_key,
            "original_payload": json.dumps(fields),
            "error": str(last_error),
            "failed_at": datetime.now(UTC).isoformat(),
            "retries": str(max_retries),
        }
        try:
            client = await self.get_redis()
            # pyrefly: ignore [no-matching-overload]
            dlq_id = await client.xadd(target_dlq, dlq_payload)
            logger.info(f"Routed failed message to DLQ '{target_dlq}' [dlq_msg_id={dlq_id}]")
            return dlq_id
        except Exception as dlq_err:
            logger.critical(f"Failed to route message to DLQ '{target_dlq}': {dlq_err}")
            raise (last_error or dlq_err) from dlq_err

    async def publish_incident_created(
        self,
        incident: Incident,
        event_ids: list[UUID] | None = None,
        max_retries: int = 3,
    ) -> str:
        """Publishes an incident.detected event to the configured incident stream."""
        state_str = (
            incident.state.value if hasattr(incident.state, "value") else str(incident.state)
        )
        sev_str = (
            incident.severity.value
            if hasattr(incident.severity, "value")
            else str(incident.severity)
        )
        created_str = (
            incident.created_at.isoformat()
            if getattr(incident, "created_at", None)
            else datetime.now(UTC).isoformat()
        )
        e_ids = [str(eid) for eid in (event_ids or [])]

        fields = {
            "event_type": "incident.detected",
            "incident_id": str(incident.id),
            "resource_id": str(incident.resource_id),
            "correlation_key": incident.correlation_key,
            "state": state_str,
            "severity": sev_str,
            "version": str(incident.version),
            "created_at": created_str,
            "event_ids": json.dumps(e_ids),
        }
        return await self.publish_with_retry(
            stream_key=self.stream_key,
            fields=fields,
            max_retries=max_retries,
            dlq_key=self.dlq_key,
        )
