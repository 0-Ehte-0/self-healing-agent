from uuid import uuid4

import pytest
from actioncatalog.registry import ActionRegistry, get_action_registry
from actioncatalog.schemas.action import ActionCategory
from pydantic import ValidationError
from sharedmodels.enums import RiskLevel, Severity


def test_action_registry_defaults():
    registry = get_action_registry()
    actions = {a.name: a for a in registry.list_actions()}

    expected_actions = {
        "inspect_container",
        "restart_container",
        "wait_for_stabilization",
        "notify_operator",
        "open_incident_ticket",
    }
    assert set(actions.keys()) == expected_actions

    # Verify inspect_container properties
    inspect_def = actions["inspect_container"]
    assert inspect_def.category == ActionCategory.READ_ONLY
    assert inspect_def.risk == RiskLevel.LOW
    assert inspect_def.timeout_seconds == 15
    assert inspect_def.retryable is True
    assert inspect_def.verification_profile is None

    # Verify restart_container properties
    restart_def = actions["restart_container"]
    assert restart_def.category == ActionCategory.WORKLOAD_MUTATION
    assert restart_def.risk == RiskLevel.LOW
    assert restart_def.timeout_seconds == 30
    assert restart_def.retryable is False
    assert restart_def.verification_profile == "m1_default_restart_profile"

    # Verify wait_for_stabilization properties
    wait_def = actions["wait_for_stabilization"]
    assert wait_def.category == ActionCategory.VERIFICATION_DECLARATION
    assert wait_def.risk == RiskLevel.LOW
    assert wait_def.timeout_seconds == 120
    assert wait_def.retryable is False
    assert wait_def.verification_profile == "m1_default_restart_profile"

    # Verify notify_operator properties
    notify_def = actions["notify_operator"]
    assert notify_def.category == ActionCategory.OPERATOR_NOTIFICATION
    assert notify_def.risk == RiskLevel.LOW
    assert notify_def.timeout_seconds == 5
    assert notify_def.retryable is True

    # Verify open_incident_ticket properties
    ticket_def = actions["open_incident_ticket"]
    assert ticket_def.category == ActionCategory.LOCAL_ESCALATION
    assert ticket_def.risk == RiskLevel.LOW
    assert ticket_def.timeout_seconds == 5
    assert ticket_def.retryable is True


def test_action_registry_unknown_action():
    registry = ActionRegistry(register_defaults=True)
    with pytest.raises(KeyError, match="not registered"):
        registry.get("unregistered_malicious_action")


def test_validate_action_parameters_success():
    registry = get_action_registry()
    res_id = uuid4()

    # Valid inspect_container
    params = {
        "container_id": "abcdef1234567890",
        "resource_id": res_id,
        "binding_generation": 1,
    }
    validated = registry.validate_action_parameters("inspect_container", params)
    assert validated.container_id == "abcdef1234567890"  # type: ignore[attr-defined]

    # Valid restart_container
    params_restart = {
        "container_id": "abcdef1234567890",
        "resource_id": res_id,
        "service_name": "demo-api",
        "timeout_seconds": 30,
    }
    validated_restart = registry.validate_action_parameters("restart_container", params_restart)
    assert validated_restart.service_name == "demo-api"  # type: ignore[attr-defined]


def test_validate_action_parameters_extra_fields_rejected():
    registry = get_action_registry()
    res_id = uuid4()

    # Extra field injection attempt
    tampered_params = {
        "container_id": "abcdef1234567890",
        "resource_id": res_id,
        "arbitrary_command": "rm -rf /",
        "privileged": True,
    }
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        registry.validate_action_parameters("inspect_container", tampered_params)


def test_validate_restart_non_allowlisted_service_rejected():
    registry = get_action_registry()
    res_id = uuid4()

    tampered_service = {
        "container_id": "abcdef1234567890",
        "resource_id": res_id,
        "service_name": "postgres",  # non-allowlisted service
        "timeout_seconds": 30,
    }
    with pytest.raises(ValidationError, match="not allowlisted"):
        registry.validate_action_parameters("restart_container", tampered_service)
