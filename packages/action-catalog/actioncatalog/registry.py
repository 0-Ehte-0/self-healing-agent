from typing import Any

from pydantic import BaseModel

from actioncatalog.actions.base import BaseAction
from actioncatalog.actions.inspect_container import InspectContainerAction
from actioncatalog.actions.notify_operator import NotifyOperatorAction
from actioncatalog.actions.open_incident_ticket import OpenIncidentTicketAction
from actioncatalog.actions.restart_container import RestartContainerAction
from actioncatalog.actions.wait_for_stabilization import WaitForStabilizationAction
from actioncatalog.schemas.action import ActionDefinition


class ActionRegistry:
    """Registry maintaining strongly typed action definitions and parameter validators."""

    def __init__(self, register_defaults: bool = True):
        self._actions: dict[str, BaseAction] = {}
        if register_defaults:
            self._register_defaults()

    def _register_defaults(self) -> None:
        defaults = [
            InspectContainerAction(),
            RestartContainerAction(),
            WaitForStabilizationAction(),
            NotifyOperatorAction(),
            OpenIncidentTicketAction(),
        ]
        for action in defaults:
            self.register(action)

    def register(self, action: BaseAction) -> None:
        """Registers a typed action instance."""
        self._actions[action.name] = action

    def get(self, action_name: str) -> BaseAction:
        """Retrieves an action by name, raising KeyError if unregistered."""
        if action_name not in self._actions:
            raise KeyError(f"Action '{action_name}' is not registered in the action catalog.")
        return self._actions[action_name]

    def has_action(self, action_name: str) -> bool:
        return action_name in self._actions

    def validate_action_parameters(self, action_name: str, parameters: dict[str, Any]) -> BaseModel:
        """Validates parameter dictionary against the specific action's Pydantic schema."""
        action = self.get(action_name)
        return action.validate_parameters(parameters)

    def list_actions(self) -> list[ActionDefinition]:
        """Returns metadata for all registered actions."""
        return [action.to_definition() for action in self._actions.values()]


_default_registry: ActionRegistry | None = None


def get_action_registry() -> ActionRegistry:
    """Returns the singleton ActionRegistry with M1 default actions."""
    global _default_registry
    if _default_registry is None:
        _default_registry = ActionRegistry(register_defaults=True)
    return _default_registry
