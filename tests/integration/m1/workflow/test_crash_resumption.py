from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from agentcore.runtime.runner import WorkflowRunner
from app.db.models import Diagnosis, Incident, Resource
from app.db.repositories.control_plane import unit_of_work
from sharedmodels.enums import IncidentState as S
from sharedmodels.enums import Severity


@pytest.mark.asyncio
async def test_worker_crash_resumption_uses_committed_db_records(session_factory):
    """Verifies that resuming after a worker termination reuses committed records from PostgreSQL."""
    async with unit_of_work(session_factory, actor="test:crash") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        assert res is not None

        # 1. Create incident and advance to DIAGNOSED with a committed Diagnosis record
        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-crash-resumption-{uuid4().hex}",
            severity=Severity.HIGH,
        )
        inc = await repo.transition(inc.id, 1, S.TRIAGED)
        inc = await repo.transition(inc.id, 2, S.DIAGNOSED)
        incident_id = inc.id

        # Insert committed diagnosis
        diag = Diagnosis(
            id=uuid4(),
            incident_id=incident_id,
            root_cause="CONTAINER_STOPPED",
            confidence=0.95,
            evidence_ids=["ev-1", "ev-2"],
            reasoning={"analysis": "Container exit code 137"},
            actor="test:crash",
        )
        await repo.add(diag)
        committed_diag_id = diag.id

    # 2. Simulate worker 1 crashing, and a brand new worker 2 starting up
    worker2_id = "worker-resumed-instance-2"

    # Define a custom plan node that records the diagnosis ID it saw from state
    observed_state_on_planning = {}

    async def mock_plan_node(state):
        observed_state_on_planning["current_diagnosis_id"] = state.get("current_diagnosis_id")
        observed_state_on_planning["version"] = state.get("version")
        # Route to escalate to conclude the test cleanly without needing M1-D/F engines
        return {"status": "PLANNED"}

    async def mock_policy_node(state):
        return {"status": "POLICY_DENIED"}  # Safe escalation edge

    runner2 = WorkflowRunner(
        session_factory=session_factory,
        worker_id=worker2_id,
        node_overrides={
            "plan": mock_plan_node,
            "evaluate_policy": mock_policy_node,
        },
    )

    # 3. Resume execution on worker 2
    result = await runner2.run_incident(incident_id=incident_id, expected_version=3)

    # 4. Verify runner2 reused the committed diagnosis ID from PostgreSQL on resume
    assert observed_state_on_planning.get("current_diagnosis_id") == str(committed_diag_id)
    assert observed_state_on_planning.get("version") == 3

    # Verify no duplicate diagnoses were created in PostgreSQL
    async with unit_of_work(session_factory, actor="test:verify") as repo:
        diags = list(
            (
                await repo.session.scalars(
                    sa.select(Diagnosis).where(Diagnosis.incident_id == incident_id)
                )
            ).all()
        )
        assert len(diags) == 1
        assert diags[0].id == committed_diag_id
