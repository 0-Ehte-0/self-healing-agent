from fault_injector.config import get_settings
from fault_injector.faults.base import BaseFault
from redis.asyncio import Redis

settings = get_settings()


class ElevatedErrorRateFault(BaseFault):
    async def inject(self) -> None:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            await redis.set("fault:error_rate", "1.0")
            self._active = True
        finally:
            await redis.aclose()

    async def clear(self) -> None:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            await redis.delete("fault:error_rate")
            self._active = False
        finally:
            await redis.aclose()
