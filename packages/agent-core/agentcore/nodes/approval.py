import logging
from typing import Any

from agentcore.graph.state import IncidentGraphState

logger = logging.getLogger(__name__)


async def wait_for_approval_node(state: IncidentGraphState) -> dict[str, Any]:
    """Handles workflow interruption while awaiting human approval.

    Per Section 6 Step 8 & ADR-0003:
    - Persists approval interrupts and timer wake-ups.
    - Safely escalates (APPROVED -> ESCALATED or PENDING_APPROVAL -> ESCALATED) if approval
      is expired, rejected, or revoked before execution.
    """
    if state.get("is_denied") or state.get("approval_expired"):
        reason = state.get("escalation_reason", "Approval rejected or expired.")
        logger.warning(f"Incident {state.get('incident_id')} escalating from approval: {reason}")
        return {
            "status": "ESCALATED",
            "wait_reason": None,
            "escalation_reason": reason,
        }

    is_approved = state.get("is_approved")
    if is_approved is True:
        return {"status": "APPROVED", "wait_reason": None}

    logger.info(f"Incident {state.get('incident_id')} awaiting human approval.")
    return {
        "status": "PENDING_APPROVAL",
        "wait_reason": "AWAITING_APPROVAL",
    }
