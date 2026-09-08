import httpx
from fault_injector.config import get_settings
from fault_injector.faults.base import BaseFault

settings = get_settings()


class ProcessCrashFault(BaseFault):
    async def inject(self) -> None:
        self._active = True
        try:
            async with httpx.AsyncClient(base_url=settings.demo_api_url, timeout=2.0) as client:
                await client.post("/_faults/crash", headers={"X-Fault-Token": settings.fault_injector_secret})
        except (httpx.RemoteProtocolError, httpx.ConnectError):
            pass

    async def clear(self) -> None:
        self._active = False
