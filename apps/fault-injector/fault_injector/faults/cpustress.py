import logging

import httpx
from fault_injector.config import get_settings
from fault_injector.faults.base import BaseFault

logger = logging.getLogger(__name__)
settings = get_settings()


class CpuStressFault(BaseFault):
    """SCN-002: Injects CPU stress directly inside the target demo-api container."""

    async def inject(self) -> None:
        self._active = True
        try:
            async with httpx.AsyncClient(base_url=settings.demo_api_url, timeout=5.0) as client:
                res = await client.post(
                    "/_faults/cpu/inject?cores=1",
                    headers={"X-Fault-Token": settings.fault_injector_secret},
                )
                res.raise_for_status()
                logger.info(f"Triggered container-internal CPU stress on demo-api: {res.json()}")
        except Exception as e:
            logger.error(f"Failed to inject CPU stress on demo-api: {e}")
            raise

    async def clear(self) -> None:
        try:
            async with httpx.AsyncClient(base_url=settings.demo_api_url, timeout=5.0) as client:
                res = await client.post(
                    "/_faults/cpu/clear",
                    headers={"X-Fault-Token": settings.fault_injector_secret},
                )
                logger.info(f"Cleared CPU stress on demo-api: {res.status_code}")
        except Exception as e:
            logger.warning(f"Error requesting CPU clear on demo-api (container may have restarted): {e}")
        finally:
            self._active = False
