from typing import Any

from sharedmodels.enums import RiskLevel

from actioncatalog.actions.base import BaseAction
from actioncatalog.schemas.action import ActionCategory
from actioncatalog.schemas.parameters import WaitForStabilizationParams


class WaitForStabilizationAction(BaseAction):
    """Declares 90s stabilization window and verification schedule. Never claims success independently."""

    name: str = "wait_for_stabilization"
    version: str = "1.0"
    category: ActionCategory = ActionCategory.VERIFICATION_DECLARATION
    risk: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 120
    retryable: bool = False
    verification_profile: str | None = "m1_default_restart_profile"
    description: str = (
        "Declare and schedule independent telemetry verification sampling window (90s)."
    )
    parameters_schema: type[WaitForStabilizationParams] = WaitForStabilizationParams

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        validated = self.validate_parameters(params)
        return {
            "status": "STABILIZATION_SCHEDULED",
            "resource_id": str(validated.resource_id),
            "duration_seconds": validated.duration_seconds,
            "verification_profile": validated.verification_profile,
            "category": self.category.value,
        }
