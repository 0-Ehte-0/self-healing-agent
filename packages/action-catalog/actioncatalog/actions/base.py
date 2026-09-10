from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel
from sharedmodels.enums import RiskLevel

from actioncatalog.schemas.action import ActionCategory, ActionDefinition


class BaseAction(ABC):
    """Abstract base class for all catalog actions."""

    name: str
    version: str = "1.0"
    category: ActionCategory
    risk: RiskLevel
    timeout_seconds: int
    retryable: bool
    verification_profile: str | None = None
    description: str
    parameters_schema: type[BaseModel]

    def validate_parameters(self, params: dict[str, Any]) -> BaseModel:
        """Validates parameter dictionary against the strongly typed Pydantic schema.

        Rejects extra fields, invalid types, and disallowed values.
        """
        return self.parameters_schema.model_validate(params)

    def to_definition(self) -> ActionDefinition:
        """Produces a serializable ActionDefinition representation."""
        return ActionDefinition(
            name=self.name,
            version=self.version,
            category=self.category,
            risk=self.risk,
            timeout_seconds=self.timeout_seconds,
            retryable=self.retryable,
            verification_profile=self.verification_profile,
            description=self.description,
            parameters_schema=self.parameters_schema.model_json_schema(),
        )

    def check_preconditions(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> bool:
        """Evaluates whether execution preconditions are satisfied."""
        return True

    @abstractmethod
    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Executes or simulates the action."""
        ...
