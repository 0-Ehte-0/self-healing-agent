import asyncio
import json
import logging
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis
from agentcore.runtime.runner import WorkflowRunner

logger = logging.getLogger(__name__)


class IncidentStreamConsumer:
    """Consumes incident events from Redis Streams with at-least-once delivery and deduplication.

    Per Section 6 Step 6: Consume notifications at least once. Deduplicate by incident and authoritative version;
    a duplicate wake-up must not duplicate diagnosis records, approvals, or mutations.
    """

    def __init__(
        self,
        runner: WorkflowRunner,
        redis_url: str,
        stream_key: str = "stream:incidents",
        group_name: str = "agent-workers",
        consumer_name: str | None = None,
        block_ms: int = 2000,
        batch_size: int = 10,
    ):
        self.runner = runner
        self.redis_url = redis_url
        self.stream_key = stream_key
        self.group_name = group_name
        self.consumer_name = consumer_name or f"worker-{runner.worker_id}"
        self.block_ms = block_ms
        self.batch_size = batch_size
        self._redis: aioredis.Redis | None = None
        self._running = False
        self._task: asyncio.Task | None = None
        # In-memory deduplication cache: (incident_id, version)
        self._seen: set[tuple[str, int]] = set()

    async def get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(self.redis_url, decode_responses=True)
        return self._redis

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        client = await self.get_redis()
        # Ensure consumer group exists
        try:
            await client.xgroup_create(self.stream_key, self.group_name, id="0", mkstream=True)
            logger.info(f"Created consumer group {self.group_name} on {self.stream_key}")
        except Exception as exc:
            if "BUSYGROUP" in str(exc):
                logger.debug(f"Consumer group {self.group_name} already exists.")
            else:
                logger.warning(f"Consumer group initialization warning: {exc}")

        self._task = asyncio.create_task(self._consume_loop())
        logger.info(f"IncidentStreamConsumer started ({self.consumer_name})")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._redis:
            await self._redis.aclose()
            self._redis = None
        logger.info(f"IncidentStreamConsumer stopped ({self.consumer_name})")

    async def _consume_loop(self) -> None:
        client = await self.get_redis()
        while self._running:
            try:
                # Read new messages from group
                streams = await client.xreadgroup(
                    groupname=self.group_name,
                    consumername=self.consumer_name,
                    streams={self.stream_key: ">"},
                    count=self.batch_size,
                    block=self.block_ms,
                )

                if not streams:
                    continue

                for stream_name, messages in streams:
                    # pyrefly: ignore [not-iterable]
                    for msg_id, fields in messages:
                        # pyrefly: ignore [bad-argument-type]
                        await self._process_message(client, msg_id, fields)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Error in consumer loop: {exc}", exc_info=True)
                await asyncio.sleep(1.0)

    async def _process_message(
        self, client: aioredis.Redis, msg_id: str, fields: dict[str, Any]
    ) -> None:
        try:
            incident_id_str = fields.get("incident_id") or fields.get("aggregate_id")
            version_str = fields.get("version") or fields.get("aggregate_version")
            event_type = fields.get("event_type", "incident.detected")

            if not incident_id_str:
                logger.warning(f"Message {msg_id} missing incident_id. Acking and dropping.")
                await client.xack(self.stream_key, self.group_name, msg_id)
                return

            incident_id = UUID(incident_id_str)
            version = int(version_str) if version_str is not None else 1

            # Deduplicate by incident and authoritative version (Step 6)
            dedup_key = (incident_id_str, version)
            if dedup_key in self._seen:
                logger.info(
                    f"Deduplicating already-seen message for incident {incident_id_str} v{version}. Acking."
                )
                await client.xack(self.stream_key, self.group_name, msg_id)
                return

            self._seen.add(dedup_key)
            if len(self._seen) > 5000:
                # Bound in-memory cache size
                self._seen.clear()

            resume_metadata = {}
            if "payload" in fields and isinstance(fields["payload"], str):
                try:
                    resume_metadata = json.loads(fields["payload"])
                except Exception:
                    pass

            logger.info(
                f"Processing event '{event_type}' for incident {incident_id} v{version} (msg_id={msg_id})"
            )

            # Delegate to resumable workflow runner
            await self.runner.run_incident(
                incident_id=incident_id,
                expected_version=version,
                resume_metadata=resume_metadata,
            )

            # Acknowledge stream message
            await client.xack(self.stream_key, self.group_name, msg_id)

        except Exception as exc:
            logger.error(f"Failed to process message {msg_id}: {exc}", exc_info=True)
            # Message remains unacked in PEL for recovery
