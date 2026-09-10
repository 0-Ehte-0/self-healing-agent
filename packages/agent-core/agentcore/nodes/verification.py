import logging
from typing import Any
from uuid import UUID

from agentcore.graph.state import IncidentGraphState
from agentcore.nodes.protocols import VerifierProtocol

logger = logging.getLogger(__name__)


async def verify_node(
    state: IncidentGraphState,
    verifier: VerifierProtocol | None = None,
) -> dict[str, Any]:
    """Verifies target health independently using fresh telemetry.

    Per Section 3.1 & Gap 2: Defaults to NotImplementedError when invoked in live stack prior to M1-G.
    """
    if verifier is None:
        raise NotImplementedError(
            "Telemetry verifier is not configured; concrete implementation arrives in M1-G."
        )

    resource_id = UUID(state["resource_id"])
    execution_id = UUID(state.get("current_execution_id", state["incident_id"]))

    res = await verifier.verify(execution_id=execution_id, resource_id=resource_id)
    passed = bool(res.get("passed", False))
    attempts = state.get("attempts", 0)
    retry_limit = state.get("retry_limit", 2)
    retry_eligible = (not passed) and (attempts < retry_limit)

    return {
        "status": "VERIFIED",
        "verification_passed": passed,
        "retry_eligible": retry_eligible,
    }
