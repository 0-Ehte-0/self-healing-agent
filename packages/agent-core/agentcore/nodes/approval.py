import logging
from typing import Any

from agentcore.graph.state import IncidentGraphState

logger = logging.getLogger(__name__)


async def wait_for_approval_node(state: IncidentGraphState) -> dict[str, Any]:
    """Handles workflow interruption while awaiting human approval.

    Per Section 6 Step 8: Persist approval interrupts and timer wake-ups.
    A worker process must not occupy a thread waiting for a person.
    """
    is_approved = state.get("is_approved")
    if is_approved is True:
        return {"status": "APPROVED", "wait_reason": None}

    logger.info(f"Incident {state.get('incident_id')} awaiting human approval.")
    return {
        "status": "PENDING_APPROVAL",
        "wait_reason": "AWAITING_APPROVAL",
    }
