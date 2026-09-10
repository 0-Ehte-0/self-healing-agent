import base64
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

import sqlalchemy as sa
from app.db.models import WorkflowCheckpoint, WorkflowCheckpointWrite
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


class PostgresCheckpointSaver(BaseCheckpointSaver):
    """PostgreSQL-backed checkpointer for LangGraph incident workflows.

    Stores checkpoints and channel writes in PostgreSQL tables workflow_checkpoints and
    workflow_checkpoint_writes, ensuring workflow state survives crashes, restarts, and worker handoffs.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        super().__init__()
        self.session_factory = session_factory

    def _encode(self, data: Any) -> dict[str, str]:
        fmt, raw = self.serde.dumps_typed(data)
        return {"format": fmt, "data": base64.b64encode(raw).decode("ascii")}

    def _decode(self, payload: dict[str, str]) -> Any:
        raw = base64.b64decode(payload["data"].encode("ascii"))
        return self.serde.loads_typed((payload["format"], raw))

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id: str = str(config["configurable"]["thread_id"])
        checkpoint_ns: str = str(config["configurable"].get("checkpoint_ns", ""))
        checkpoint_id = get_checkpoint_id(config)

        async with self.session_factory() as session:
            if checkpoint_id:
                stmt = sa.select(WorkflowCheckpoint).where(
                    WorkflowCheckpoint.thread_id == thread_id,
                    WorkflowCheckpoint.checkpoint_ns == checkpoint_ns,
                    WorkflowCheckpoint.checkpoint_id == checkpoint_id,
                )
            else:
                stmt = (
                    sa.select(WorkflowCheckpoint)
                    .where(
                        WorkflowCheckpoint.thread_id == thread_id,
                        WorkflowCheckpoint.checkpoint_ns == checkpoint_ns,
                    )
                    .order_by(WorkflowCheckpoint.created_at.desc())
                    .limit(1)
                )

            row = await session.scalar(stmt)
            if row is None:
                return None

            # Fetch associated pending writes
            writes_stmt = (
                sa.select(WorkflowCheckpointWrite)
                .where(
                    WorkflowCheckpointWrite.thread_id == row.thread_id,
                    WorkflowCheckpointWrite.checkpoint_ns == row.checkpoint_ns,
                    WorkflowCheckpointWrite.checkpoint_id == row.checkpoint_id,
                )
                .order_by(WorkflowCheckpointWrite.idx.asc())
            )
            write_rows = (await session.scalars(writes_stmt)).all()

            checkpoint = self._decode(row.checkpoint)
            metadata = self._decode(row.checkpoint_metadata)
            pending_writes = [(w.task_id, w.channel, self._decode(w.value)) for w in write_rows]

            parent_config = (
                {
                    "configurable": {
                        "thread_id": row.thread_id,
                        "checkpoint_ns": row.checkpoint_ns,
                        "checkpoint_id": row.parent_checkpoint_id,
                    }
                }
                if row.parent_checkpoint_id
                else None
            )

            return CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": row.thread_id,
                        "checkpoint_ns": row.checkpoint_ns,
                        "checkpoint_id": row.checkpoint_id,
                    }
                },
                checkpoint=checkpoint,
                metadata=metadata,
                pending_writes=pending_writes,
                parent_config=parent_config,
            )

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id: str = str(config["configurable"]["thread_id"])
        checkpoint_ns: str = str(config["configurable"].get("checkpoint_ns", ""))
        checkpoint_id: str = str(checkpoint["id"])
        parent_id = config["configurable"].get("checkpoint_id")

        meta_to_save = get_checkpoint_metadata(config, metadata)

        encoded_cp = self._encode(checkpoint)
        encoded_meta = self._encode(meta_to_save)

        table = WorkflowCheckpoint.__table__
        stmt = insert(table).values(
            thread_id=thread_id,
            checkpoint_ns=checkpoint_ns,
            checkpoint_id=checkpoint_id,
            parent_checkpoint_id=parent_id,
            type=encoded_cp["format"],
            checkpoint=encoded_cp,
            metadata=encoded_meta,
        )
        on_conflict_stmt = stmt.on_conflict_do_update(
            index_elements=[
                table.c.thread_id,
                table.c.checkpoint_ns,
                table.c.checkpoint_id,
            ],
            set_={
                "checkpoint": encoded_cp,
                "metadata": encoded_meta,
            },
        )

        async with self.session_factory() as session:
            await session.execute(on_conflict_stmt)
            await session.commit()

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id: str = str(config["configurable"]["thread_id"])
        checkpoint_ns: str = str(config["configurable"].get("checkpoint_ns", ""))
        checkpoint_id: str = str(config["configurable"]["checkpoint_id"])

        async with self.session_factory() as session:
            for idx, (channel, val) in enumerate(writes):
                encoded_val = self._encode(val)
                stmt = insert(WorkflowCheckpointWrite).values(
                    thread_id=thread_id,
                    checkpoint_ns=checkpoint_ns,
                    checkpoint_id=checkpoint_id,
                    task_id=task_id,
                    idx=idx,
                    channel=channel,
                    type=encoded_val["format"],
                    value=encoded_val,
                )
                on_conflict = stmt.on_conflict_do_update(
                    index_elements=[
                        WorkflowCheckpointWrite.thread_id,
                        WorkflowCheckpointWrite.checkpoint_ns,
                        WorkflowCheckpointWrite.checkpoint_id,
                        WorkflowCheckpointWrite.task_id,
                        WorkflowCheckpointWrite.idx,
                    ],
                    set_={"value": encoded_val},
                )
                await session.execute(on_conflict)
            await session.commit()

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        if not config:
            return

        thread_id: str = str(config["configurable"]["thread_id"])
        checkpoint_ns: str = str(config["configurable"].get("checkpoint_ns", ""))

        async with self.session_factory() as session:
            stmt = sa.select(WorkflowCheckpoint).where(
                WorkflowCheckpoint.thread_id == thread_id,
                WorkflowCheckpoint.checkpoint_ns == checkpoint_ns,
            )
            if before and (before_id := get_checkpoint_id(before)):
                stmt = stmt.where(WorkflowCheckpoint.checkpoint_id < before_id)

            stmt = stmt.order_by(WorkflowCheckpoint.created_at.desc())
            if limit:
                stmt = stmt.limit(limit)

            rows = (await session.scalars(stmt)).all()
            for row in rows:
                writes_stmt = (
                    sa.select(WorkflowCheckpointWrite)
                    .where(
                        WorkflowCheckpointWrite.thread_id == row.thread_id,
                        WorkflowCheckpointWrite.checkpoint_ns == row.checkpoint_ns,
                        WorkflowCheckpointWrite.checkpoint_id == row.checkpoint_id,
                    )
                    .order_by(WorkflowCheckpointWrite.idx.asc())
                )
                write_rows = (await session.scalars(writes_stmt)).all()
                checkpoint = self._decode(row.checkpoint)
                metadata = self._decode(row.checkpoint_metadata)
                pending_writes = [(w.task_id, w.channel, self._decode(w.value)) for w in write_rows]

                yield CheckpointTuple(
                    config={
                        "configurable": {
                            "thread_id": row.thread_id,
                            "checkpoint_ns": row.checkpoint_ns,
                            "checkpoint_id": row.checkpoint_id,
                        }
                    },
                    checkpoint=checkpoint,
                    metadata=metadata,
                    pending_writes=pending_writes,
                    parent_config=(
                        {
                            "configurable": {
                                "thread_id": row.thread_id,
                                "checkpoint_ns": row.checkpoint_ns,
                                "checkpoint_id": row.parent_checkpoint_id,
                            }
                        }
                        if row.parent_checkpoint_id
                        else None
                    ),
                )

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        raise NotImplementedError(
            "PostgresCheckpointSaver only supports asynchronous access (aget_tuple)"
        )

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        raise NotImplementedError(
            "PostgresCheckpointSaver only supports asynchronous access (aput)"
        )

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        raise NotImplementedError(
            "PostgresCheckpointSaver only supports asynchronous access (aput_writes)"
        )
