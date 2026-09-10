from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa
from agent_worker.recovery import WorkflowRecoveryService
from agentcore.nodes.verification import verify_node
from agentcore.runtime.runner import WorkflowRunner
from app.db.models import Incident, WorkflowSchedule
from app.db.repositories.control_plane import unit_of_work
from sharedmodels.enums import IncidentState


@pytest.mark.asyncio
async def test_cooldown_schedule_survives_worker_restart_and_resumes(
    session_factory, create_verifying_incident
):
    """Verifies that cooldown schedules survive worker restarts and are processed when due."""
    ctx = await create_verifying_incident(scenario_id="SCN-001")
    inc_id = ctx["incident"].id

    mock_verifier = AsyncMock()
    mock_verifier.verify.return_value = {
        "passed": False,
        "status": "VERIFICATION_FAILED",
        "attribution": "AGENT_HEALED",
        "profile_id": "SCN-001",
        "profile_version": "1.0",
        "failure_reason": "Stabilization reset",
    }

    # Worker 1 runs verify_node, which fails and schedules a 600s cooldown
    result = await verify_node(
        state=ctx["state"],
        verifier=mock_verifier,
        session_factory=session_factory,
        actor="worker:1",
        cooldown_seconds=600,
    )
    assert result["status"] == "COOLDOWN_SCHEDULED"
    assert result["attempts"] == 1

    # Verify schedule persisted in DB
    async with unit_of_work(session_factory, actor="test:check") as repo:
        inc = await repo.session.get(Incident, inc_id)
        assert inc.state == IncidentState.DIAGNOSED
        assert inc.attempts == 1

        sched = await repo.session.scalar(
            sa.select(WorkflowSchedule).where(
                WorkflowSchedule.incident_id == inc_id,
                WorkflowSchedule.wait_reason == "COOLDOWN",
            )
        )
        assert sched is not None
        assert sched.status == "PENDING"
        schedule_id = sched.id

        # Fast-forward next_run_at to past so it is due
        sched.next_run_at = datetime.now(UTC) - timedelta(seconds=10)
        await repo.session.flush()

    # Worker 2 (new worker process after restart) boots up
    new_runner = WorkflowRunner(session_factory=session_factory, worker_id="worker:2")
    # Mock runner.run_incident to verify it gets invoked
    new_runner.run_incident = AsyncMock()

    recovery_service = WorkflowRecoveryService(
        session_factory=session_factory,
        runner=new_runner,
    )

    dispatched = await recovery_service.recover_due_schedules()
    assert dispatched == 1

    # Verify runner was invoked with incident ID and version
    new_runner.run_incident.assert_awaited_once()
    call_kwargs = new_runner.run_incident.call_args.kwargs
    assert call_kwargs["incident_id"] == inc_id

    # Verify schedule was atomically marked PROCESSED in DB
    async with unit_of_work(session_factory, actor="test:check") as repo:
        updated_sched = await repo.session.get(WorkflowSchedule, schedule_id)
        assert updated_sched.status == "PROCESSED"


@pytest.mark.asyncio
async def test_duplicate_wakeup_does_not_double_increment_attempts(
    session_factory, create_verifying_incident
):
    """Verifies that duplicate wake-ups do not double-increment attempt counters."""
    ctx = await create_verifying_incident(scenario_id="SCN-001")
    inc_id = ctx["incident"].id
    current_version = ctx["incident"].version

    runner = WorkflowRunner(session_factory=session_factory, worker_id="worker:test")

    # Current incident version is 5, attempts = 0
    # A duplicate/stale wake-up arrives with expected_version = 3 (< current version 5)
    result = await runner.run_incident(
        incident_id=inc_id,
        expected_version=3,  # Stale duplicate message
    )

    # Must be skipped cleanly as a duplicate
    assert result is None

    # Incident attempts counter remains unchanged
    async with unit_of_work(session_factory, actor="test:check") as repo:
        inc = await repo.session.get(Incident, inc_id)
        assert inc.version == current_version
        assert inc.attempts == 0
