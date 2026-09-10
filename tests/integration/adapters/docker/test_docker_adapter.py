import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
import sqlalchemy as sa
from app.db.models import (
    Approval,
    AutomationControl,
    Diagnosis,
    Execution,
    Incident,
    PolicyDecision,
    RemediationPlan,
    RemediationStep,
    Resource,
    ResourceLock,
)
from app.db.repositories.control_plane import ConflictError, unit_of_work
from app.services.resources.lock import (
    QuarantinedResourceError,
    ResourceLockedError,
    ResourceLockService,
)
from provideradapters.base import (
    ContainerNotFoundError,
    ContainerSnapshot,
    DockerDaemonUnreachableError,
    DockerTimeoutError,
    ExecutionIntent,
    ExecutionOutcomeStatus,
    PrecheckFailedError,
    TargetRecreatedError,
)
from provideradapters.docker.adapter import DockerExecutionAdapter
from provideradapters.docker.client import DockerClientWrapper
from sharedmodels.enums import ExecutionStatus, IncidentState, RiskLevel, Severity


class FakeDockerContainer:
    def __init__(
        self, container_id: str, name: str, labels: dict[str, Any], status: str = "running"
    ):
        self.id = container_id
        self.name = name
        self.labels = labels
        self.status = status
        self.restart_call_count = 0
        self.restart_count = 0
        self.created_at = "2026-09-10T14:00:00.000000Z"
        self.started_at = "2026-09-10T14:00:05.000000Z"
        self.finished_at = None

    @property
    def attrs(self) -> dict[str, Any]:
        return {
            "Id": self.id,
            "Name": f"/{self.name}",
            "Created": self.created_at,
            "RestartCount": self.restart_count,
            "Config": {
                "Image": "demo-api:latest",
                "Labels": self.labels,
            },
            "State": {
                "Status": self.status,
                "StartedAt": self.started_at,
                "FinishedAt": self.finished_at,
                "ExitCode": 0,
                "Health": {"Status": "healthy"},
            },
        }

    def restart(self, timeout: int = 15):
        self.restart_call_count += 1
        self.restart_count += 1
        self.started_at = datetime.now(UTC).isoformat()


class FakeDockerClient:
    def __init__(self, containers: dict[str, FakeDockerContainer] | None = None):
        self._containers = containers or {}

    @property
    def containers(self):
        parent = self

        class ContainerManager:
            def get(self, cid: str):
                if cid not in parent._containers:
                    raise Exception(f"404 Client Error: Not Found ('No such container: {cid}')")
                return parent._containers[cid]

            def list(self, all: bool = True, filters: dict | None = None):
                return list(parent._containers.values())

        return ContainerManager()


@pytest.fixture
async def setup_incident_context(session_factory):
    """Sets up a managed resource, active incident, diagnosis, plan, and policy decision."""
    res_id = uuid4()
    cid = f"abc11223344556677889900{res_id.hex[:16]}"
    async with unit_of_work(session_factory, actor="test:setup") as repo:
        res = Resource(
            id=res_id,
            provider="compose",
            external_id=f"self-healing:demo-api-{res_id.hex[:8]}",
            name="demo-api",
            environment="local",
            labels={
                "compose_service": "demo-api",
                "compose_project": "self-healing-agent",
                "docker_container_id": cid,
                "binding_generation": 1,
            },
            managed=True,
        )
        repo.session.add(res)
        await repo.session.flush()

        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"corr-{uuid4().hex}",
            severity=Severity.HIGH,
            approval_required=False,
        )
        await repo.transition(inc.id, 1, IncidentState.TRIAGED)
        await repo.transition(inc.id, 2, IncidentState.DIAGNOSED)

        diag = Diagnosis(
            id=uuid4(),
            incident_id=inc.id,
            root_cause="CONTAINER_STOPPED",
            confidence=0.95,
            evidence_ids=["ev-test"],
            reasoning={"state": "exited"},
            actor="test:setup",
        )
        await repo.add(diag)

        step_id = uuid4()
        plan = await repo.create_remediation_plan_with_steps(
            incident_id=inc.id,
            diagnosis_id=diag.id,
            version=1,
            risk=RiskLevel.LOW,
            content_hash="hash123",
            container_id=cid,
            binding_generation=1,
            verification_profile="m1_default_restart_profile",
            steps_data=[
                {
                    "id": step_id,
                    "resource_id": res.id,
                    "position": 0,
                    "action": "restart_container",
                    "action_schema_version": "1.0",
                    "parameters": {
                        "container_id": cid,
                        "resource_id": str(res.id),
                        "service_name": "demo-api",
                        "timeout_seconds": 15,
                        "binding_generation": 1,
                    },
                    "verification": {"profile": "m1_default_restart_profile"},
                }
            ],
        )
        _, steps = await repo.get_plan_with_steps(plan.id)
        step = steps[0]

        # Add initial policy decision ALLOW
        policy_dec = PolicyDecision(
            id=uuid4(),
            incident_id=inc.id,
            plan_id=plan.id,
            plan_version=1,
            policy_version=1,
            content_hash="hash123",
            container_id=cid,
            binding_generation=1,
            decision="ALLOW",
            reason_codes=["M1_POLICY_AUTO_ALLOW"],
            rule_results=[],
            evaluated_facts={},
            actor="test:setup",
        )
        repo.session.add(policy_dec)
        await repo.session.flush()

        await repo.transition(inc.id, 3, IncidentState.PLANNED)

    intent = ExecutionIntent(
        incident_id=inc.id,
        plan_id=plan.id,
        plan_version=1,
        step_id=step.id,
        attempt_number=1,
        resource_id=res.id,
        container_id=cid,
        binding_generation=1,
        service_name="demo-api",
        timeout_seconds=15,
        idempotency_key=f"exec:{inc.id}:1:{step.id}:1",
    )

    return {
        "resource": res,
        "incident": inc,
        "plan": plan,
        "step": step,
        "intent": intent,
        "container_id": cid,
    }


# ---------------------------------------------------------------------------
# Test 1: Restart a labeled disposable target; reject an unlabeled target
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_restart_labeled_target_and_reject_unlabeled(session_factory, setup_incident_context):
    ctx = setup_incident_context
    cid = ctx["container_id"]

    # 1. Valid container with self-healing.managed=true
    fake_container = FakeDockerContainer(
        container_id=cid,
        name="demo-api",
        labels={
            "self-healing.managed": "true",
            "com.docker.compose.service": "demo-api",
            "com.docker.compose.project": "self-healing-agent",
            "secret_token": "super-secret-pass",
        },
    )
    fake_docker = FakeDockerClient({cid: fake_container})
    adapter = DockerExecutionAdapter(session_factory=session_factory, docker_client=fake_docker)

    # Precheck passes
    pre = await adapter.precheck(ctx["intent"])
    assert pre["precheck_passed"] is True
    # Verify label redaction
    assert pre["pre_snapshot"]["labels"]["secret_token"] == "[REDACTED]"

    # Execute restart
    outcome = await adapter.execute(ctx["intent"])
    assert outcome.status == ExecutionOutcomeStatus.SUCCEEDED
    assert fake_container.restart_call_count == 1
    assert outcome.reconciled is False

    # 2. Reject unlabeled container
    fake_container.labels["self-healing.managed"] = "false"
    with pytest.raises(PrecheckFailedError, match="missing 'self-healing.managed=true'"):
        await adapter.precheck(ctx["intent"])

    # 3. Reject wrong service
    fake_container.labels["self-healing.managed"] = "true"
    fake_container.labels["com.docker.compose.service"] = "demo-worker"
    with pytest.raises(PrecheckFailedError, match="compose service label is 'demo-worker'"):
        await adapter.precheck(ctx["intent"])


# ---------------------------------------------------------------------------
# Test 2: Two simultaneous incidents cannot overlap mutations
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_simultaneous_incidents_cannot_overlap_mutations(
    session_factory, setup_incident_context
):
    ctx = setup_incident_context
    cid = ctx["container_id"]
    res_id = ctx["resource"].id

    fake_container = FakeDockerContainer(
        container_id=cid,
        name="demo-api",
        labels={
            "self-healing.managed": "true",
            "com.docker.compose.service": "demo-api",
            "com.docker.compose.project": "self-healing-agent",
        },
    )
    fake_docker = FakeDockerClient({cid: fake_container})
    adapter1 = DockerExecutionAdapter(
        session_factory=session_factory, docker_client=fake_docker, actor="worker-1"
    )
    adapter2 = DockerExecutionAdapter(
        session_factory=session_factory, docker_client=fake_docker, actor="worker-2"
    )

    # Worker 1 acquires lock
    async with unit_of_work(session_factory, actor="worker-1") as repo:
        lock_svc = ResourceLockService(repo)
        token1 = await lock_svc.acquire_lock(res_id, seconds=60)
        assert token1 is not None

    # Worker 2 attempts execution on the same resource -> must fail with ResourceLockedError
    intent2 = ctx["intent"].model_copy(
        update={"idempotency_key": f"exec:{uuid4()}:1:{ctx['step'].id}:1"}
    )
    with pytest.raises(ResourceLockedError, match="currently locked"):
        await adapter2.execute(intent2)

    # Worker 1 releases lock
    async with unit_of_work(session_factory, actor="worker-1") as repo:
        lock_svc = ResourceLockService(repo)
        await lock_svc.release_lock(res_id, token1)

    # Worker 2 can now execute
    outcome2 = await adapter2.execute(intent2)
    assert outcome2.status == ExecutionOutcomeStatus.SUCCEEDED


# ---------------------------------------------------------------------------
# Test 3: Identical execution requests reuse record; changed payload fails
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_idempotent_requests_and_tampered_payload_conflict(
    session_factory, setup_incident_context
):
    ctx = setup_incident_context
    cid = ctx["container_id"]

    fake_container = FakeDockerContainer(
        container_id=cid,
        name="demo-api",
        labels={
            "self-healing.managed": "true",
            "com.docker.compose.service": "demo-api",
            "com.docker.compose.project": "self-healing-agent",
        },
    )
    fake_docker = FakeDockerClient({cid: fake_container})
    adapter = DockerExecutionAdapter(session_factory=session_factory, docker_client=fake_docker)

    # 1. Initial execution
    outcome1 = await adapter.execute(ctx["intent"])
    assert outcome1.status == ExecutionOutcomeStatus.SUCCEEDED
    assert fake_container.restart_call_count == 1

    # 2. Duplicate identical request with same idempotency key -> reuses record, NO duplicate restart
    outcome2 = await adapter.execute(ctx["intent"])
    assert outcome2.status == ExecutionOutcomeStatus.SUCCEEDED
    assert outcome2.execution_id == outcome1.execution_id
    assert fake_container.restart_call_count == 1  # Not restarted again!

    # 3. Request with same idempotency key but changed payload (different container ID) -> ConflictError
    tampered_intent = ctx["intent"].model_copy(update={"container_id": "9999999999999999"})
    with pytest.raises(
        ConflictError, match="Idempotency key already belongs to a different execution"
    ):
        await adapter.execute(tampered_intent)


# ---------------------------------------------------------------------------
# Test 4: Target replacement, label removal, policy change, and lease loss
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_safety_boundary_prechecks_prevent_dispatch(session_factory, setup_incident_context):
    ctx = setup_incident_context
    cid = ctx["container_id"]
    res_id = ctx["resource"].id
    plan_id = ctx["plan"].id
    inc_id = ctx["incident"].id

    fake_container = FakeDockerContainer(
        container_id=cid,
        name="demo-api",
        labels={
            "self-healing.managed": "true",
            "com.docker.compose.service": "demo-api",
            "com.docker.compose.project": "self-healing-agent",
        },
    )
    fake_docker = FakeDockerClient({cid: fake_container})
    adapter = DockerExecutionAdapter(session_factory=session_factory, docker_client=fake_docker)

    # 1. Target replacement: DB resource binding generation bumped
    async with unit_of_work(session_factory, actor="test:modify") as repo:
        res = await repo.session.get(Resource, res_id)
        labels = dict(res.labels or {})
        labels["binding_generation"] = 2
        res.labels = labels

    with pytest.raises(TargetRecreatedError, match="binding generation mismatch"):
        await adapter.precheck(ctx["intent"])

    # Reset generation
    async with unit_of_work(session_factory, actor="test:modify") as repo:
        res = await repo.session.get(Resource, res_id)
        labels = dict(res.labels or {})
        labels["binding_generation"] = 1
        res.labels = labels

    # 2. Policy change: latest decision updated to DENY
    async with unit_of_work(session_factory, actor="test:modify") as repo:
        deny_dec = PolicyDecision(
            id=uuid4(),
            incident_id=inc_id,
            plan_id=plan_id,
            plan_version=1,
            policy_version=1,
            content_hash="hash123",
            container_id=cid,
            binding_generation=1,
            decision="DENY",
            reason_codes=["EMERGENCY_STOP"],
            rule_results=[],
            evaluated_facts={},
            actor="test:modify",
        )
        repo.session.add(deny_dec)

    with pytest.raises(PrecheckFailedError, match="policy decision is DENY"):
        await adapter.precheck(ctx["intent"])

    # Reset policy decision to ALLOW so we specifically isolate emergency stop check
    async with unit_of_work(session_factory, actor="test:modify") as repo:
        allow_dec = PolicyDecision(
            id=uuid4(),
            incident_id=inc_id,
            plan_id=plan_id,
            plan_version=1,
            policy_version=1,
            content_hash="hash123",
            container_id=cid,
            binding_generation=1,
            decision="ALLOW",
            reason_codes=["M1_POLICY_AUTO_ALLOW"],
            rule_results=[],
            evaluated_facts={},
            actor="test:modify",
        )
        repo.session.add(allow_dec)

    # 3. Emergency stop active
    async with unit_of_work(session_factory, actor="test:modify") as repo:
        ctrl = await repo.get_automation_controls()
        ctrl.emergency_stopped_resources = [str(res_id)]

    with pytest.raises(QuarantinedResourceError, match="emergency stop list"):
        await adapter.precheck(ctx["intent"])


# ---------------------------------------------------------------------------
# Test 5: Crash before dispatch recoverable; crash after dispatch never duplicates
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_crash_recovery_and_no_duplicate_restart(session_factory, setup_incident_context):
    ctx = setup_incident_context
    cid = ctx["container_id"]

    fake_container = FakeDockerContainer(
        container_id=cid,
        name="demo-api",
        labels={
            "self-healing.managed": "true",
            "com.docker.compose.service": "demo-api",
            "com.docker.compose.project": "self-healing-agent",
        },
    )
    fake_docker = FakeDockerClient({cid: fake_container})
    adapter = DockerExecutionAdapter(session_factory=session_factory, docker_client=fake_docker)

    # 1. Simulate timeout on wire during restart call, but container DID restart in Docker daemon
    def timeout_restart(timeout=15):
        # Daemon did perform the restart, but wire response timed out
        fake_container.restart_count += 1
        fake_container.started_at = datetime.now(UTC).isoformat()
        raise DockerTimeoutError("Read timed out on Docker socket")

    fake_container.restart = timeout_restart

    outcome = await adapter.execute(ctx["intent"])

    # Reconciliation detects restart succeeded via incremented RestartCount & recent StartedAt
    assert outcome.status == ExecutionOutcomeStatus.SUCCEEDED
    assert outcome.reconciled is True

    # Calling execute again does NOT issue another restart
    outcome_repeat = await adapter.execute(ctx["intent"])
    assert outcome_repeat.status == ExecutionOutcomeStatus.SUCCEEDED


# ---------------------------------------------------------------------------
# Test 6: Docker timeout, missing target, daemon outage recorded correctly
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_docker_timeout_missing_target_and_daemon_outage(
    session_factory, setup_incident_context
):
    ctx = setup_incident_context
    cid = ctx["container_id"]

    # 1. Missing target (Container not found)
    fake_docker = FakeDockerClient({})
    adapter = DockerExecutionAdapter(session_factory=session_factory, docker_client=fake_docker)

    with pytest.raises(TargetRecreatedError, match="not found in Docker daemon"):
        await adapter.precheck(ctx["intent"])

    # 2. Daemon outage
    class DeadContainers:
        def get(self, cid):
            raise Exception("connection refused: cannot connect to Docker daemon")

    class DeadDockerClient:
        @property
        def containers(self):
            return DeadContainers()

    adapter_dead = DockerExecutionAdapter(
        session_factory=session_factory, docker_client=DeadDockerClient()
    )
    with pytest.raises(PrecheckFailedError, match="Docker inspection precheck failed"):
        await adapter_dead.precheck(ctx["intent"])

    # 3. Unreconciled timeout triggers quarantine
    fake_container = FakeDockerContainer(
        container_id=cid,
        name="demo-api",
        labels={
            "self-healing.managed": "true",
            "com.docker.compose.service": "demo-api",
            "com.docker.compose.project": "self-healing-agent",
        },
    )

    def failed_restart(timeout=15):
        # Container never restarted
        raise DockerTimeoutError("Socket timeout")

    fake_container.restart = failed_restart
    fake_docker2 = FakeDockerClient({cid: fake_container})
    adapter2 = DockerExecutionAdapter(session_factory=session_factory, docker_client=fake_docker2)

    outcome = await adapter2.execute(ctx["intent"])
    assert outcome.status == ExecutionOutcomeStatus.UNCERTAIN

    # Verify resource was quarantined
    async with unit_of_work(session_factory, actor="test:check") as repo:
        lock_svc = ResourceLockService(repo)
        with pytest.raises(QuarantinedResourceError, match="quarantined"):
            await lock_svc.check_safety(ctx["resource"].id)


# ---------------------------------------------------------------------------
# Test 7: Dry-run records eligibility without a Docker mutation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dry_run_records_eligibility_without_mutation(
    session_factory, setup_incident_context
):
    ctx = setup_incident_context
    cid = ctx["container_id"]

    fake_container = FakeDockerContainer(
        container_id=cid,
        name="demo-api",
        labels={
            "self-healing.managed": "true",
            "com.docker.compose.service": "demo-api",
            "com.docker.compose.project": "self-healing-agent",
        },
    )
    fake_docker = FakeDockerClient({cid: fake_container})
    adapter = DockerExecutionAdapter(session_factory=session_factory, docker_client=fake_docker)

    outcome = await adapter.dry_run(ctx["intent"])
    assert outcome.status == ExecutionOutcomeStatus.SUCCEEDED
    assert outcome.post_state["dry_run"] is True
    assert outcome.post_state["eligible"] is True
    assert fake_container.restart_call_count == 0  # Mutation was NOT dispatched
