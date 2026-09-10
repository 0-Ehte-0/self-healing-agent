from typing import Any

from sharedmodels.enums import RiskLevel

from actioncatalog.actions.base import BaseAction
from actioncatalog.schemas.action import ActionCategory
from actioncatalog.schemas.parameters import InspectContainerParams


class InspectContainerAction(BaseAction):
    """Inspects Docker container status for an exact bound container ID (read-only)."""

    name: str = "inspect_container"
    version: str = "1.0"
    category: ActionCategory = ActionCategory.READ_ONLY
    risk: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 15
    retryable: bool = True
    verification_profile: str | None = None
    description: str = "Inspect Docker container status and configuration for exact bound ID."
    parameters_schema: type[InspectContainerParams] = InspectContainerParams

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        validated = self.validate_parameters(params)
        return {
            "status": "INSPECTED",
            "container_id": validated.container_id,  # type: ignore[attr-defined]
            "resource_id": str(validated.resource_id),  # type: ignore[attr-defined]
            "category": self.category.value,
        }
