from fault_injector.config import get_settings
from fault_injector.faults.base import BaseFault
from redis.asyncio import Redis

settings = get_settings()


class RedisDisconnectFault(BaseFault):
    async def inject(self) -> None:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            await redis.execute_command("CLIENT", "PAUSE", 600000, "ALL")
            self._active = True
        finally:
            await redis.aclose()

    async def clear(self) -> None:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            await redis.execute_command("CLIENT", "UNPAUSE")
            self._active = False
        finally:
            await redis.aclose()
