import asyncio
import json
import logging
import sys
import time
from typing import Any

from demo_worker.config import get_settings
from prometheus_client import Gauge, start_http_server
from redis.asyncio import Redis
from redis.exceptions import ResponseError


class WorkerFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "timestamp": time.time(),
                "level": record.levelname,
                "service": "demo-worker",
                "message": record.getMessage(),
                "scenario_id": getattr(record, "scenario_id", "BASELINE"),
                "correlation_id": getattr(record, "correlation_id", "none"),
            }
        )


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(WorkerFormatter())
logger = logging.getLogger("demo-worker")
logger.handlers = [handler]
logger.setLevel(logging.INFO)
logger.propagate = False

settings = get_settings()

WORKER_HEARTBEAT_SECONDS = Gauge(
    "demo_worker_heartbeat_seconds",
    "Worker heartbeat unix epoch timestamp",
)
WORKER_QUEUE_DEPTH = Gauge(
    "demo_worker_queue_depth",
    "Current pending length of the Redis job stream",
)


async def get_stream_backlog(redis: Redis, stream_key: str, consumer_group: str) -> int:
    """Calculate the backlog for the consumer group using consumer lag and unacknowledged messages."""
    try:
        groups = await redis.xinfo_groups(stream_key)
        for g in groups:
            if isinstance(g, dict) and g.get("name") == consumer_group:
                lag = g.get("lag") or 0
                pending = g.get("pending") or 0
                return int(lag) + int(pending)
    except Exception:
        pass

    try:
        pending_info = await redis.xpending(stream_key, consumer_group)
        if isinstance(pending_info, dict):
            return int(pending_info.get("pending", 0) or 0)
        elif isinstance(pending_info, (list, tuple)) and len(pending_info) > 0:
            return int(pending_info[0] or 0)
    except Exception:
        pass

    return 0


async def handle_event(event_id: str, payload: dict[str, Any]) -> None:
    logger.info(
        "Processing event %s",
        event_id,
        extra={
            "scenario_id": payload.get("scenario_id", "BASELINE"),
            "correlation_id": payload.get("correlation_id", "none"),
        },
    )
    await asyncio.sleep(0.05)


async def consume_stream_events(redis: Redis) -> None:
    try:
        await redis.xgroup_create(
            name=settings.stream_key,
            groupname=settings.consumer_group,
            id="0",
            mkstream=True,
        )
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise

    while True:
        try:
            WORKER_HEARTBEAT_SECONDS.set(time.time())
            backlog = await get_stream_backlog(redis, settings.stream_key, settings.consumer_group)
            WORKER_QUEUE_DEPTH.set(backlog)

            # Injected worker pause check (SCN-008)
            if await redis.get("fault:worker_pause"):
                await asyncio.sleep(0.5)
                continue

            entries = await redis.xreadgroup(
                groupname=settings.consumer_group,
                consumername=settings.consumer_name,
                streams={settings.stream_key: ">"},
                count=10,
                block=1000,
            )

            if isinstance(entries, list) and entries:
                for stream_name, messages in entries:
                    for message_id, data in messages:
                        await handle_event(message_id, data)
                        await redis.xack(settings.stream_key, settings.consumer_group, message_id)
            else:
                await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in consumer loop: {e}")
            await asyncio.sleep(1)


def run_worker() -> None:
    start_http_server(settings.metrics_port)
    logger.info(f"Worker metrics exporter listening on port {settings.metrics_port}")
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        asyncio.run(consume_stream_events(redis))
    finally:
        asyncio.run(redis.aclose())


if __name__ == "__main__":
    run_worker()
