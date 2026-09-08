from fault_injector.config import get_settings
from fault_injector.faults.base import BaseFault
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

settings = get_settings()


class DbConnectionLeakFault(BaseFault):
    def __init__(self, scenario_id: str, connection_count: int = 15) -> None:
        super().__init__(scenario_id)
        self.connection_count = connection_count
        self._engine: AsyncEngine | None = None
        self._connections: list[AsyncConnection] = []

    async def inject(self) -> None:
        db_url = getattr(
            settings,
            "database_url",
            "postgresql+asyncpg://postgres:postgres@postgres:5432/self_healing",
        )
        self._engine = create_async_engine(
            db_url,
            pool_size=self.connection_count,
            max_overflow=0,
        )
        for _ in range(self.connection_count):
            conn = await self._engine.connect()
            await conn.execute(text("SELECT 1"))
            self._connections.append(conn)
        self._active = True

    async def clear(self) -> None:
        for conn in self._connections:
            await conn.close()
        self._connections.clear()
        if self._engine:
            await self._engine.dispose()
            self._engine = None
        self._active = False
