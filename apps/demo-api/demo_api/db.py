from collections.abc import AsyncGenerator

from demo_api.config import get_settings
from demo_api.metrics import ACTIVE_DB_CONNECTIONS
from sqlalchemy import Column, DateTime, Integer, String, event, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

settings = get_settings()
engine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()


@event.listens_for(engine.sync_engine, "checkout")
def track_checkout(dbapi_connection, connection_record, connection_proxy):
    ACTIVE_DB_CONNECTIONS.inc()


@event.listens_for(engine.sync_engine, "checkin")
def track_checkin(dbapi_connection, connection_record):
    ACTIVE_DB_CONNECTIONS.dec()


class JobRecord(Base):
    __tablename__ = "demo_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(64), unique=True, index=True, nullable=False)
    payload = Column(String(512), nullable=False)
    status = Column(String(32), default="QUEUED", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
