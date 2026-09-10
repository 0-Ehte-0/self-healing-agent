import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import sqlalchemy as sa
from agentcore.runtime.lease import IncidentLeaseManager
from agentcore.runtime.runner import WorkflowRunner
from app.db.models import Incident, Resource, WorkflowLease
from app.db.repositories.control_plane import ConflictError, unit_of_work
from sharedmodels.enums import IncidentState as S
from sharedmodels.enums import Severity


@pytest.mark.asyncio
async def test_concurrent_lease_acquisition_rejected(session_factory):
    """Verifies that two workers cannot hold a lease on the same incident concurrently."""
    async with unit_of_work(session_factory, actor="test:lease") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        assert res is not None

        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-lease-conflict-{uuid4().hex}",
            severity=Severity.MEDIUM,
        )
        incident_id = inc.id

    lease_mgr1 = IncidentLeaseManager(session_factory, worker_id="worker-alpha", ttl_seconds=30)
    lease_mgr2 = IncidentLeaseManager(session_factory, worker_id="worker-bravo", ttl_seconds=30)

    # Worker alpha acquires the lease
    token1 = await lease_mgr1.acquire(incident_id, expected_version=1)
    assert token1 is not None

    # Worker bravo attempts to acquire the lease for the same incident while active
    with pytest.raises(ConflictError, match="currently leased by another worker"):
        await lease_mgr2.acquire(incident_id, expected_version=1)

    # Verify worker alpha still holds valid lease
    assert await lease_mgr1.verify(incident_id, token1, expected_version=1) is True

    # Release worker alpha lease
    await lease_mgr1.release(incident_id, token1)

    # Now worker bravo can acquire it
    token2 = await lease_mgr2.acquire(incident_id, expected_version=1)
    assert token2 is not None
    assert token2 != token1
    await lease_mgr2.release(incident_id, token2)


@pytest.mark.asyncio
async def test_stale_worker_fenced_after_lease_expiry_and_takeover(session_factory):
    """Verifies that when a worker's lease expires and another worker takes over,
    the original stale worker is fenced and cannot renew or verify the lease.
    """
    async with unit_of_work(session_factory, actor="test:expiry") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-lease-expiry-{uuid4().hex}",
            severity=Severity.HIGH,
        )
        incident_id = inc.id

    lease_mgr1 = IncidentLeaseManager(session_factory, worker_id="worker-slow-1", ttl_seconds=1)
    lease_mgr2 = IncidentLeaseManager(session_factory, worker_id="worker-fast-2", ttl_seconds=30)

    # Worker 1 acquires lease with 1 second TTL
    token1 = await lease_mgr1.acquire(incident_id, expected_version=1)

    # Allow lease to expire
    await asyncio.sleep(1.2)

    # Worker 2 takes over the expired lease
    token2 = await lease_mgr2.acquire(incident_id, expected_version=1)
    assert token2 != token1

    # Stale Worker 1 attempts to verify ownership: must be rejected (False)
    assert await lease_mgr1.verify(incident_id, token1, expected_version=1) is False

    # Stale Worker 1 attempts to renew: must be rejected (False)
    assert await lease_mgr1.renew(incident_id, token1) is False

    # Active Worker 2 is verified and valid
    assert await lease_mgr2.verify(incident_id, token2, expected_version=1) is True
    await lease_mgr2.release(incident_id, token2)


@pytest.mark.asyncio
async def test_stale_version_rejected_from_acquiring_or_committing(session_factory):
    """Verifies optimistic concurrency fencing:
    A worker operating with a stale version is rejected from acquiring a lease
    and rejected from committing state transitions.
    """
    async with unit_of_work(session_factory, actor="test:stale_ver") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-stale-ver-{uuid4().hex}",
            severity=Severity.HIGH,
        )
        incident_id = inc.id

        # Advance incident to version 2 (TRIAGED)
        inc = await repo.transition(incident_id, 1, S.TRIAGED)
        assert inc.version == 2

    lease_mgr = IncidentLeaseManager(session_factory, worker_id="worker-stale-version")

    # Attempting to acquire lease specifying stale version 1 must raise ConflictError
    with pytest.raises(ConflictError, match="Incident version mismatch"):
        await lease_mgr.acquire(incident_id, expected_version=1)

    # Attempting to transition incident with stale version 1 must raise ConflictError
    async with unit_of_work(session_factory, actor="test:stale_transition") as repo:
        with pytest.raises(ConflictError, match="Stale incident version"):
            await repo.transition(incident_id, expected_version=1, target=S.DIAGNOSED)

    # Incident remains untouched at version 2, TRIAGED
    async with unit_of_work(session_factory, actor="test:verify") as repo:
        inc_current = await repo.session.get(Incident, incident_id)
        assert inc_current.version == 2
        assert inc_current.state == S.TRIAGED


@pytest.mark.asyncio
async def test_workflow_runner_rejects_incident_with_stale_version(session_factory):
    """Verifies that WorkflowRunner skips execution if incident is already past expected_version."""
    async with unit_of_work(session_factory, actor="test:runner_stale") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-runner-stale-{uuid4().hex}",
            severity=Severity.LOW,
        )
        # Advance to version 3
        inc = await repo.transition(inc.id, 1, S.TRIAGED)
        inc = await repo.transition(inc.id, 2, S.DIAGNOSED)
        incident_id = inc.id
        assert inc.version == 3

    runner = WorkflowRunner(session_factory=session_factory, worker_id="worker-runner-test")

    # Calling run_incident with stale expected_version=2 returns None without modifying anything
    result = await runner.run_incident(incident_id=incident_id, expected_version=2)
    assert result is None
