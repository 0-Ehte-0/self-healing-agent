import logging
from typing import Any
from uuid import UUID

from agentcore.graph.state import IncidentGraphState
from agentcore.nodes.protocols import ExecutorProtocol

logger = logging.getLogger(__name__)


async def execute_node(
    state: IncidentGraphState,
    executor: ExecutorProtocol | None = None,
) -> dict[str, Any]:
    """Executes the remediating mutation step.

    Per Section 3.1 & Gap 2: Defaults to NotImplementedError when invoked in live stack prior to M1-F.
    Zero automatic retries are permitted on this node.
    """
    if executor is None:
        raise NotImplementedError(
            "Docker execution engine is not configured; concrete implementation arrives in M1-F."
        )

    incident_id = UUID(state["incident_id"])
    resource_id = UUID(state["resource_id"])
    step_id = UUID(state.get("current_step_id", state["incident_id"]))
    attempt = state.get("attempts", 0) + 1

    res = await executor.execute_step(
        incident_id=incident_id,
        step_id=step_id,
        resource_id=resource_id,
        attempt=attempt,
    )
    return {
        "status": "EXECUTED",
        "attempts": attempt,
    }
