from typing import Any

from sharedmodels.enums import RiskLevel

from actioncatalog.actions.base import BaseAction
from actioncatalog.schemas.action import ActionCategory
from actioncatalog.schemas.parameters import RestartContainerParams


class RestartContainerAction(BaseAction):
    """Restart allowlisted managed container ('demo-api') with bounded timeout (mutating)."""

    name: str = "restart_container"
    version: str = "1.0"
    category: ActionCategory = ActionCategory.WORKLOAD_MUTATION
    risk: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 30
    retryable: bool = False
    verification_profile: str | None = "m1_default_restart_profile"
    description: str = "Restart allowlisted managed container ('demo-api') with bounded timeout."
    parameters_schema: type[RestartContainerParams] = RestartContainerParams

    def check_preconditions(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> bool:
        # Preconditions: service_name must be allowlisted 'demo-api'
        validated = self.validate_parameters(params)
        return getattr(validated, "service_name", None) == "demo-api"

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        validated = self.validate_parameters(params)
        return {
            "status": "RESTART_SUBMITTED",
            "container_id": validated.container_id,
            "resource_id": str(validated.resource_id),
            "service_name": validated.service_name,
            "timeout_seconds": validated.timeout_seconds,
            "category": self.category.value,
        }
