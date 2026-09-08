from fault_injector.config import get_settings
from fault_injector.faults.base import BaseFault
from redis.asyncio import Redis

settings = get_settings()


class LatencyFault(BaseFault):
    def __init__(self, scenario_id: str, delay_seconds: float = 2.0) -> None:
        super().__init__(scenario_id)
        self.delay_seconds = delay_seconds

    async def inject(self) -> None:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            await redis.set("fault:latency", str(self.delay_seconds))
            self._active = True
        finally:
            await redis.aclose()

    async def clear(self) -> None:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            await redis.delete("fault:latency")
            self._active = False
        finally:
            await redis.aclose()
