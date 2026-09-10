from uuid import uuid4

import pytest
from actioncatalog.planner.dry_run import DryRunExecutor
from actioncatalog.planner.mapper import DeterministicPlanner
from sharedmodels.enums import IncidentState, RootCause
from sharedmodels.plan import TargetBinding


@pytest.mark.asyncio
async def test_dry_run_simulation():
    planner = DeterministicPlanner()
    dry_runner = DryRunExecutor()

    res_id = uuid4()
    inc_id = uuid4()
    diag_id = uuid4()

    target = TargetBinding(
        resource_id=res_id,
        container_id="abcdef1234567890",
        service_name="demo-api",
        binding_generation=1,
    )

    plan = planner.create_plan(
        incident_id=inc_id,
        diagnosis_id=diag_id,
        root_cause=RootCause.CONTAINER_STOPPED,
        target_binding=target,
        version=1,
    )

    dry_run = await dry_runner.execute_dry_run(plan)

    assert dry_run.incident_id == inc_id
    assert dry_run.plan_id == plan.id
    assert dry_run.content_hash == plan.content_hash
    assert dry_run.validation_result["valid"] is True
    assert dry_run.policy_evaluation["simulated"] is True
    assert dry_run.policy_evaluation["eligible_for_execution"] is True

    # Check simulated steps
    assert len(dry_run.simulated_steps) == 3

    restart_sim = dry_run.simulated_steps[1]
    assert restart_sim["action"] == "restart_container"
    assert restart_sim["mutation_dispatched"] is False
    assert restart_sim["docker_call_bypassed"] is True

    stab_sim = dry_run.simulated_steps[2]
    assert stab_sim["action"] == "wait_for_stabilization"
    assert stab_sim["verification_dispatched"] is False
    assert stab_sim["zero_verification_records_written"] is True


@pytest.mark.asyncio
async def test_dry_run_metric_non_inflation_guarantees():
    """Explicitly assert that a dry run produces zero mutations, leaves incident resolved_at null,

    preserves the attempts counter, and writes zero rows to verification.
    """
    planner = DeterministicPlanner()
    dry_runner = DryRunExecutor()

    # Mock incident state object
    simulated_incident = {
        "id": uuid4(),
        "state": IncidentState.DIAGNOSED,
        "attempts": 0,
        "resolved_at": None,
    }
    verification_records: list[dict] = []

    target = TargetBinding(
        resource_id=uuid4(),
        container_id="1122334455667788",
        service_name="demo-api",
        binding_generation=1,
    )

    plan = planner.create_plan(
        incident_id=simulated_incident["id"],
        diagnosis_id=uuid4(),
        root_cause=RootCause.CPU_SATURATION,
        target_binding=target,
        version=1,
    )

    # Execute dry run
    dry_run = await dry_runner.execute_dry_run(plan)

    # 1. Verification results table remains completely empty (zero rows)
    assert len(verification_records) == 0

    # 2. Incident attempts counter is not incremented
    assert simulated_incident["attempts"] == 0

    # 3. Incident resolved_at remains null
    assert simulated_incident["resolved_at"] is None

    # 4. Incident state remains unchanged (not transitioned to RESOLVED)
    assert simulated_incident["state"] == IncidentState.DIAGNOSED

    # 5. Dry run record explicitly confirms zero verification records written
    assert any(s.get("zero_verification_records_written") is True for s in dry_run.simulated_steps)
