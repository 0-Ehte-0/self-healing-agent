from typing import Any

from sharedmodels.enums import RiskLevel

from actioncatalog.actions.base import BaseAction
from actioncatalog.schemas.action import ActionCategory
from actioncatalog.schemas.parameters import NotifyOperatorParams


class NotifyOperatorAction(BaseAction):
    """Creates a persisted dashboard attention item for operator observation (local record)."""

    name: str = "notify_operator"
    version: str = "1.0"
    category: ActionCategory = ActionCategory.OPERATOR_NOTIFICATION
    risk: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 5
    retryable: bool = True
    verification_profile: str | None = None
    description: str = "Create an auditable dashboard attention item for operator review."
    parameters_schema: type[NotifyOperatorParams] = NotifyOperatorParams

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        validated = self.validate_parameters(params)
        return {
            "status": "ATTENTION_ITEM_RECORDED",
            "incident_id": str(validated.incident_id),
            "reason": validated.reason,
            "severity": validated.severity.value
            if hasattr(validated.severity, "value")
            else str(validated.severity),
            "message": validated.message,
            "category": self.category.value,
        }
