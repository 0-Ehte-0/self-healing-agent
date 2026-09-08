import asyncio
import logging
import sys
import time
from typing import Any

from demo_worker.config import get_settings
from prometheus_client import Gauge, start_http_server
from redis.asyncio import Redis
from redis.exceptions import ResponseError

logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}',
)
logger = logging.getLogger("demo-worker")

settings = get_settings()

WORKER_HEARTBEAT_SECONDS = Gauge(
    "demo_worker_heartbeat_seconds",
    "Worker heartbeat unix epoch timestamp",
)
WORKER_QUEUE_DEPTH = Gauge(
    "demo_worker_queue_depth",
    "Current pending length of the Redis job stream",
)


async def handle_event(event_id: str, payload: dict[str, Any]) -> None:
    logger.info(f"Processing event {event_id}: payload={payload}")
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
            queue_len = await redis.xlen(settings.stream_key)
            WORKER_QUEUE_DEPTH.set(queue_len)

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

            if entries:
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
