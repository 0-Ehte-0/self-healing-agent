from typing import Any
from uuid import uuid4

from sharedmodels.enums import RiskLevel

from actioncatalog.actions.base import BaseAction
from actioncatalog.schemas.action import ActionCategory
from actioncatalog.schemas.parameters import OpenIncidentTicketParams


class OpenIncidentTicketAction(BaseAction):
    """Creates a local escalation record with ticket reference (local record, no external call)."""

    name: str = "open_incident_ticket"
    version: str = "1.0"
    category: ActionCategory = ActionCategory.LOCAL_ESCALATION
    risk: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 5
    retryable: bool = True
    verification_profile: str | None = None
    description: str = (
        "Create a local escalation record with ticket reference for unresolvable incident."
    )
    parameters_schema: type[OpenIncidentTicketParams] = OpenIncidentTicketParams

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        validated = self.validate_parameters(params)
        ticket_ref = f"LOCAL-TICKET-{uuid4().hex[:8].upper()}"
        return {
            "status": "ESCALATION_RECORDED",
            "incident_id": str(validated.incident_id),
            "ticket_reference": ticket_ref,
            "title": validated.title,
            "summary": validated.summary,
            "root_cause": validated.root_cause,
            "escalation_reason": validated.escalation_reason,
            "category": self.category.value,
        }
