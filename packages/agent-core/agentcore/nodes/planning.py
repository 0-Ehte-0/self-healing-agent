import logging
from uuid import UUID

from agentcore.graph.state import IncidentGraphState
from agentcore.nodes.protocols import PlannerProtocol

logger = logging.getLogger(__name__)


async def plan_node(
    state: IncidentGraphState,
    planner: PlannerProtocol | None = None,
) -> dict[str, str | None]:
    """Generates a typed remediation plan.

    Per Section 3.1 & Gap 2: Defaults to NotImplementedError when invoked in live stack prior to M1-D.
    Skips planning if committed plan already exists in reconstructed state.
    """
    if state.get("current_plan_id"):
        logger.info(
            "Plan %s already committed in database for incident %s; skipping re-planning on resume.",
            state.get("current_plan_id"),
            state.get("incident_id"),
        )
        return {"current_plan_id": state["current_plan_id"], "status": "PLANNED"}

    if planner is None:
        raise NotImplementedError(
            "Remediation planner is not configured; concrete implementation arrives in M1-D."
        )

    incident_id = UUID(state["incident_id"])
    diagnosis_id = (
        UUID(state["current_diagnosis_id"]) if state.get("current_diagnosis_id") else None
    )
    if not diagnosis_id:
        raise ValueError("Cannot plan remediation without an active diagnosis ID")

    plan_res = await planner.plan(incident_id=incident_id, diagnosis_id=diagnosis_id)
    plan_id = str(plan_res.get("id")) if plan_res.get("id") else None
    return {"current_plan_id": plan_id, "status": "PLANNED"}
