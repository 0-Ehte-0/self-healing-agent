from uuid import uuid4

import pytest
from actioncatalog.planner.validator import (
    PlanValidationError,
    PlanValidator,
    compute_plan_content_hash,
)
from sharedmodels.enums import RiskLevel
from sharedmodels.plan import RemediationPlanSchema, RemediationStepSchema, TargetBinding


@pytest.fixture
def valid_target() -> TargetBinding:
    return TargetBinding(
        resource_id=uuid4(),
        container_id="1122334455667788",
        service_name="demo-api",
        binding_generation=1,
    )


@pytest.fixture
def valid_plan(valid_target: TargetBinding) -> RemediationPlanSchema:
    inc_id = uuid4()
    diag_id = uuid4()
    steps = [
        RemediationStepSchema(
            id=uuid4(),
            resource_id=valid_target.resource_id,
            position=0,
            action="inspect_container",
            parameters={
                "container_id": valid_target.container_id,
                "resource_id": str(valid_target.resource_id),
                "binding_generation": 1,
            },
        ),
        RemediationStepSchema(
            id=uuid4(),
            resource_id=valid_target.resource_id,
            position=1,
            action="restart_container",
            parameters={
                "container_id": valid_target.container_id,
                "resource_id": str(valid_target.resource_id),
                "service_name": valid_target.service_name,
                "timeout_seconds": 30,
            },
            verification={"profile": "m1_default_restart_profile"},
        ),
        RemediationStepSchema(
            id=uuid4(),
            resource_id=valid_target.resource_id,
            position=2,
            action="wait_for_stabilization",
            parameters={
                "resource_id": str(valid_target.resource_id),
                "duration_seconds": 90,
                "verification_profile": "m1_default_restart_profile",
            },
        ),
    ]
    hash_val = compute_plan_content_hash(
        incident_id=str(inc_id),
        diagnosis_id=str(diag_id),
        version=1,
        risk="LOW",
        target_binding=valid_target.model_dump(),
        steps=[s.model_dump() for s in steps],
        verification_profile="m1_default_restart_profile",
    )
    return RemediationPlanSchema(
        id=uuid4(),
        incident_id=inc_id,
        diagnosis_id=diag_id,
        version=1,
        risk=RiskLevel.LOW,
        target_binding=valid_target,
        steps=steps,
        verification_profile="m1_default_restart_profile",
        content_hash=hash_val,
        actor="test:planner",
    )


def test_validator_valid_plan(valid_plan: RemediationPlanSchema, valid_target: TargetBinding):
    validator = PlanValidator()
    errors = validator.validate(valid_plan, expected_target=valid_target)
    assert errors == []
    validator.assert_valid(valid_plan, expected_target=valid_target)


def test_validator_rejects_unregistered_action(valid_plan: RemediationPlanSchema):
    validator = PlanValidator()
    valid_plan.steps[1].action = "unknown_command_runner"
    errors = validator.validate(valid_plan, verify_content_hash=False)
    assert any("Unregistered action" in err for err in errors)


def test_validator_rejects_forged_docker_id(valid_plan: RemediationPlanSchema):
    validator = PlanValidator()
    # Step specifies a different container ID than the target binding
    valid_plan.steps[1].parameters["container_id"] = "deadbeef99887766"
    errors = validator.validate(valid_plan, verify_content_hash=False)
    assert any("does not match plan target" in err or "forgery" in err for err in errors)


def test_validator_rejects_target_outside_binding(valid_plan: RemediationPlanSchema):
    validator = PlanValidator()
    # Step specifies an unrelated resource_id
    valid_plan.steps[1].resource_id = uuid4()
    errors = validator.validate(valid_plan, verify_content_hash=False)
    assert any("does not match plan target resource_id" in err for err in errors)


def test_validator_rejects_multiple_mutations(
    valid_plan: RemediationPlanSchema, valid_target: TargetBinding
):
    validator = PlanValidator()
    # Add second mutating step
    second_restart = RemediationStepSchema(
        id=uuid4(),
        resource_id=valid_target.resource_id,
        position=3,
        action="restart_container",
        parameters={
            "container_id": valid_target.container_id,
            "resource_id": str(valid_target.resource_id),
            "service_name": valid_target.service_name,
            "timeout_seconds": 30,
        },
    )
    valid_plan.steps.append(second_restart)
    errors = validator.validate(valid_plan, verify_content_hash=False)
    assert any("mutating steps" in err and "prohibits" in err for err in errors)


def test_validator_rejects_disordered_steps(valid_plan: RemediationPlanSchema):
    validator = PlanValidator()
    # Invert order: restart at 0, inspect at 1
    valid_plan.steps[0].position = 1
    valid_plan.steps[1].position = 0
    errors = validator.validate(valid_plan, verify_content_hash=False)
    assert any("preceded by an 'inspect_container' step" in err for err in errors)


def test_validator_rejects_tampered_content_hash(valid_plan: RemediationPlanSchema):
    validator = PlanValidator()
    # Valid plan structure but forged hash
    valid_plan.content_hash = "0" * 64
    with pytest.raises(PlanValidationError, match="content hash mismatch"):
        validator.assert_valid(valid_plan, verify_content_hash=True)


def test_validator_rejects_shell_payload_in_parameters():
    from actioncatalog.schemas.parameters import NotifyOperatorParams

    # Attempt to inject shell command into reason
    with pytest.raises(
        ValueError, match="Shell metacharacters and commands are strictly forbidden"
    ):
        NotifyOperatorParams(
            incident_id=uuid4(),
            reason="Restart needed; rm -rf /",
            message="Clean alert",
        )

    # Attempt to inject pipe
    with pytest.raises(
        ValueError, match="Shell metacharacters and commands are strictly forbidden"
    ):
        NotifyOperatorParams(
            incident_id=uuid4(),
            reason="alert | cat /etc/shadow",
            message="Clean alert",
        )
