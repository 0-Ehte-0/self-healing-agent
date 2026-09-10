from uuid import uuid4

import pytest
from actioncatalog.planner.mapper import DeterministicPlanner
from sharedmodels.enums import RiskLevel, RootCause
from sharedmodels.plan import TargetBinding


@pytest.fixture
def target_binding() -> TargetBinding:
    return TargetBinding(
        resource_id=uuid4(),
        container_id="abcdef1234567890",
        service_name="demo-api",
        binding_generation=1,
    )


@pytest.mark.parametrize(
    "root_cause",
    [
        RootCause.CONTAINER_STOPPED,
        RootCause.CPU_SATURATION,
        RootCause.API_UNRESPONSIVE,
    ],
)
def test_deterministic_planner_supported_causes(
    root_cause: RootCause, target_binding: TargetBinding
):
    planner = DeterministicPlanner()
    incident_id = uuid4()
    diagnosis_id = uuid4()

    plan = planner.create_plan(
        incident_id=incident_id,
        diagnosis_id=diagnosis_id,
        root_cause=root_cause,
        target_binding=target_binding,
        version=1,
    )

    assert plan.incident_id == incident_id
    assert plan.diagnosis_id == diagnosis_id
    assert plan.version == 1
    assert plan.risk == RiskLevel.LOW
    assert plan.verification_profile == "m1_default_restart_profile"
    assert len(plan.content_hash) == 64

    # Verify 3 ordered steps
    assert len(plan.steps) == 3
    assert plan.steps[0].action == "inspect_container"
    assert plan.steps[0].position == 0
    assert plan.steps[0].parameters["container_id"] == target_binding.container_id

    assert plan.steps[1].action == "restart_container"
    assert plan.steps[1].position == 1
    assert plan.steps[1].parameters["service_name"] == "demo-api"

    assert plan.steps[2].action == "wait_for_stabilization"
    assert plan.steps[2].position == 2
    assert plan.steps[2].parameters["duration_seconds"] == 90


@pytest.mark.parametrize(
    "unsupported_cause",
    [
        RootCause.DEPENDENCY_UNAVAILABLE,
        RootCause.INSUFFICIENT_EVIDENCE,
        RootCause.NO_ACTIVE_FAULT,
        RootCause.AMBIGUOUS,
    ],
)
def test_deterministic_planner_unsupported_causes_escalate(
    unsupported_cause: RootCause, target_binding: TargetBinding
):
    planner = DeterministicPlanner()
    incident_id = uuid4()
    diagnosis_id = uuid4()

    plan = planner.create_plan(
        incident_id=incident_id,
        diagnosis_id=diagnosis_id,
        root_cause=unsupported_cause,
        target_binding=target_binding,
        version=1,
    )

    assert plan.verification_profile is None
    assert len(plan.steps) == 2
    assert plan.steps[0].action == "notify_operator"
    assert plan.steps[1].action == "open_incident_ticket"


@pytest.mark.asyncio
async def test_planner_async_protocol(target_binding: TargetBinding):
    planner = DeterministicPlanner()
    incident_id = uuid4()
    diagnosis_id = uuid4()

    res = await planner.plan(
        incident_id=incident_id,
        diagnosis_id=diagnosis_id,
        root_cause=RootCause.CONTAINER_STOPPED,
        target_binding=target_binding,
    )
    assert res["is_actionable"] is True
    assert res["version"] == 1
    assert len(res["content_hash"]) == 64


def test_content_hash_sensitivity(target_binding: TargetBinding):
    planner = DeterministicPlanner()
    incident_id = uuid4()
    diag_id = uuid4()

    plan1 = planner.create_plan(
        incident_id=incident_id,
        diagnosis_id=diag_id,
        root_cause=RootCause.CONTAINER_STOPPED,
        target_binding=target_binding,
        version=1,
    )

    # Rebuilding with exact same input produces identical hash
    plan2 = planner.create_plan(
        incident_id=incident_id,
        diagnosis_id=diag_id,
        root_cause=RootCause.CONTAINER_STOPPED,
        target_binding=target_binding,
        version=1,
    )
    assert plan1.content_hash == plan2.content_hash

    # Version bump produces different hash
    plan_v2 = planner.create_plan(
        incident_id=incident_id,
        diagnosis_id=diag_id,
        root_cause=RootCause.CONTAINER_STOPPED,
        target_binding=target_binding,
        version=2,
    )
    assert plan1.content_hash != plan_v2.content_hash

    # Container ID change produces different hash
    alt_target = TargetBinding(
        resource_id=target_binding.resource_id,
        container_id="fedcba9876543210",
        service_name="demo-api",
        binding_generation=2,
    )
    plan_alt_target = planner.create_plan(
        incident_id=incident_id,
        diagnosis_id=diag_id,
        root_cause=RootCause.CONTAINER_STOPPED,
        target_binding=alt_target,
        version=1,
    )
    assert plan1.content_hash != plan_alt_target.content_hash
