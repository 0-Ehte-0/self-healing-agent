from collections.abc import AsyncGenerator

from demo_api.config import get_settings
from redis.asyncio import Redis

settings = get_settings()
redis_client: Redis = Redis.from_url(settings.redis_url, decode_responses=True)


async def get_redis() -> AsyncGenerator[Redis, None]:
    yield redis_client
