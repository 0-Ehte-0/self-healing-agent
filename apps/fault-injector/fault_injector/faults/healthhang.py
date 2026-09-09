import logging

import httpx
from fault_injector.config import get_settings
from fault_injector.faults.base import BaseFault
from redis.asyncio import Redis

logger = logging.getLogger(__name__)
settings = get_settings()


class HealthHangFault(BaseFault):
    """SCN-003: Injects process-local hang directly into target demo-api container."""

    async def inject(self) -> None:
        self._active = True
        try:
            async with httpx.AsyncClient(base_url=settings.demo_api_url, timeout=5.0) as client:
                res = await client.post(
                    "/_faults/hang/inject",
                    headers={"X-Fault-Token": settings.fault_injector_secret},
                )
                res.raise_for_status()
                logger.info(f"Triggered process-local hang on demo-api: {res.json()}")
        except Exception as e:
            logger.error(f"Failed to inject process-local hang on demo-api: {e}")
            raise

    async def clear(self) -> None:
        try:
            async with httpx.AsyncClient(base_url=settings.demo_api_url, timeout=5.0) as client:
                res = await client.post(
                    "/_faults/hang/clear",
                    headers={"X-Fault-Token": settings.fault_injector_secret},
                )
                logger.info(f"Cleared process-local hang on demo-api: {res.status_code}")
        except Exception as e:
            logger.warning(
                f"Error requesting hang clear on demo-api (container may have restarted): {e}"
            )
        finally:
            # Also ensure legacy redis key is deleted if it existed
            try:
                redis = Redis.from_url(settings.redis_url, decode_responses=True)
                await redis.delete("fault:health_hang")
                await redis.aclose()
            except Exception:
                pass
            self._active = False
