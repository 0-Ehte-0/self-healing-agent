import asyncio
import logging
import os
import signal
import sys
from uuid import uuid4

from agentcore.runtime.runner import WorkflowRunner
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agent_worker.consumer import IncidentStreamConsumer
from agent_worker.heartbeat import WorkerHeartbeatPublisher
from agent_worker.recovery import WorkflowRecoveryService

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("agent-worker")


async def run_worker() -> None:
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/control_plane",
    )
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    worker_id = os.getenv("WORKER_ID", f"worker-{uuid4().hex[:8]}")

    logger.info(f"Starting agent-worker [id={worker_id}]...")

    engine = create_async_engine(database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    runner = WorkflowRunner(session_factory=session_factory, worker_id=worker_id)
    heartbeat = WorkerHeartbeatPublisher(session_factory=session_factory, worker_id=worker_id)
    consumer = IncidentStreamConsumer(runner=runner, redis_url=redis_url)
    recovery = WorkflowRecoveryService(session_factory=session_factory, runner=runner)

    await heartbeat.start()
    await consumer.start()
    await recovery.start()

    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("Termination signal received. Shutting down worker...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # On Windows, add_signal_handler is not fully implemented
            pass

    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        await recovery.stop()
        await consumer.stop()
        await heartbeat.stop()
        await engine.dispose()
        logger.info("Agent-worker shutdown complete.")


def main():
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
