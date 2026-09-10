import logging
from typing import Any
from uuid import UUID

from agentcore.graph.state import IncidentGraphState
from agentcore.nodes.protocols import PolicyEngineProtocol

logger = logging.getLogger(__name__)


async def evaluate_policy_node(
    state: IncidentGraphState,
    policy_engine: PolicyEngineProtocol | None = None,
) -> dict[str, Any]:
    """Evaluates safety policies against proposed remediation plan.

    Per Section 3.1 & Gap 2: Defaults to NotImplementedError when invoked in live stack prior to M1-E.
    """
    if policy_engine is None:
        raise NotImplementedError(
            "Policy engine is not configured; concrete implementation arrives in M1-E."
        )

    plan_id = UUID(state["current_plan_id"]) if state.get("current_plan_id") else None
    if not plan_id:
        raise ValueError("Cannot evaluate policy without an active plan ID")

    decision = await policy_engine.evaluate(plan_id=plan_id)
    return {
        "approval_required": decision.get("approval_required", False),
        "status": "POLICY_EVALUATED",
    }
