import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import sqlalchemy as sa
from agentcore.graph.state import IncidentGraphState
from agentcore.runtime.runner import WorkflowRunner
from app.db.models import (
    Diagnosis,
    EscalationRecord,
    Execution,
    Incident,
    PolicyDecision,
    RemediationPlan,
    RemediationStep,
    Resource,
    User,
    VerificationResult,
    WorkflowSchedule,
)
from app.db.repositories.control_plane import unit_of_work
from diagnosis.schemas import ObservationBundle
from provideradapters.docker.adapter import DockerExecutionAdapter
from sharedmodels.enums import ExecutionStatus, RiskLevel, RootCause, Severity, UserRole
from sharedmodels.enums import IncidentState as S
from sharedmodels.verification import CheckStatus, VerificationVerdict


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
                "ExitCode": 0 if self.status == "running" else 137,
                "Health": {"Status": "healthy"},
            },
        }

    def restart(self, timeout: int = 15):
        self.restart_call_count += 1
        self.restart_count += 1
        self.status = "running"
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


async def setup_test_resource(session_factory, cid: str, service: str = "demo-api") -> Resource:
    res_id = None
    async with unit_of_work(session_factory, actor="test:setup") as repo:
        res = await repo.session.scalar(
            sa.select(Resource).where(Resource.name == service).limit(1)
        )
        if res is None:
            res = Resource(
                id=uuid4(),
                provider="compose",
                external_id=f"self-healing:{service}-{uuid4().hex[:8]}",
                name=service,
                environment="local",
                labels={
                    "compose_service": service,
                    "compose_project": "self-healing-agent",
                    "docker_container_id": cid,
                    "binding_generation": 1,
                },
                managed=True,
            )
            repo.session.add(res)
        else:
            labels = dict(res.labels or {})
            labels["docker_container_id"] = cid
            labels["container_id"] = cid
            labels["binding_generation"] = 1
            labels["compose_service"] = service
            labels["compose_project"] = "self-healing-agent"
            res.labels = labels
        res_id = res.id

    async with unit_of_work(session_factory, actor="test:setup") as repo:
        return await repo.session.get(Resource, res_id)


def make_mock_verifier(passed: bool = True, profile_id: str = "SCN-001"):
    verifier = AsyncMock()
    now = datetime.now(UTC)
    verifier.verify.return_value = {
        "passed": passed,
        "status": "RESOLVED" if passed else "VERIFICATION_FAILED",
        "attribution": "AGENT_HEALED",
        "profile_id": profile_id,
        "profile_version": "1.0",
        "health_score": 1.0 if passed else 0.4,
        "warm_up_duration_seconds": 30.0 if passed else None,
        "stabilization_resets": 0 if passed else 2,
        "window_start": now - timedelta(seconds=90),
        "window_end": now,
        "checks": {"readiness": {"status": "PASS" if passed else "FAIL"}},
        "samples": [{"sample_index": 0, "all_passed": passed}],
        "failure_reason": None if passed else "Stabilization window reset due to unhealthy samples",
    }
    return verifier


@pytest.mark.asyncio
async def test_m1_complete_lifecycle_scn001_container_stopped(session_factory):
    """SCN-001: Container stopped -> Diagnosis -> Plan -> Policy ALLOW -> Execute -> Verify -> RESOLVED."""
    cid = f"c1234567890abcdef{uuid4().hex[:16]}"
    res = await setup_test_resource(session_factory, cid)

    container = FakeDockerContainer(
        cid,
        "demo-api",
        {
            "self-healing.managed": "true",
            "com.docker.compose.service": "demo-api",
            "com.docker.compose.project": "self-healing-agent",
        },
        status="exited",
    )
    fake_docker = FakeDockerClient({cid: container})
    adapter = DockerExecutionAdapter(session_factory, fake_docker)

    # Ingest incident
    async with unit_of_work(session_factory, actor="test:lifecycle") as repo:
        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"m1-lifecycle-scn001-{uuid4().hex[:8]}",
            severity=Severity.CRITICAL,
            approval_required=False,
        )

    # Evidence collector mock returning stopped container observation
    async def mock_evidence_node(state: IncidentGraphState, **kwargs):
        obs = ObservationBundle(
            resource_id=res.id,
            service_name="demo-api",
            container_status="exited",
            exit_code=137,
            up_metric=0.0,
            firing_alerts=["ContainerDown"],
        )
        async with unit_of_work(session_factory, actor="test:evidence") as repo:
            inc_db = await repo.session.get(Incident, inc.id)
            if inc_db.state == S.DETECTED:
                await repo.transition(inc.id, inc_db.version, S.TRIAGED)
        return {
            "status": "TRIAGED",
            "observation_bundle": obs.model_dump(mode="json"),
            "evidence_ids": [str(uuid4())],
            "container_id": cid,
            "binding_generation": 1,
            "service_name": "demo-api",
        }

    mock_verifier = make_mock_verifier(passed=True, profile_id="SCN-001")

    async def mock_verify_node(state: IncidentGraphState, **kwargs):
        from agentcore.nodes.verification import verify_node

        return await verify_node(
            state=state,
            verifier=mock_verifier,
            session_factory=session_factory,
            actor="test:worker",
        )

    async def mock_execute_node(state: IncidentGraphState, **kwargs):
        from agentcore.nodes.execution import execute_node

        return await execute_node(
            state=state,
            adapter=adapter,
            session_factory=session_factory,
            actor="test:worker",
        )

    runner = WorkflowRunner(
        session_factory=session_factory,
        worker_id="test:worker:m1",
        node_overrides={
            "collect_evidence": mock_evidence_node,
            "execute": mock_execute_node,
            "verify": mock_verify_node,
        },
    )

    final_state = await runner.run_incident(inc.id)
    print("FINAL STATE IS:", final_state)
    assert final_state is not None
    assert final_state.get("status") == "RESOLVED"

    # Verify end-to-end database persistence
    async with unit_of_work(session_factory, actor="test:check") as repo:
        inc_db = await repo.session.get(Incident, inc.id)
        assert inc_db.state == S.RESOLVED
        assert inc_db.resolved_at is not None

        # Diagnosis
        diags = await repo.session.scalars(
            sa.select(Diagnosis).where(Diagnosis.incident_id == inc.id)
        )
        diag_list = list(diags.all())
        assert len(diag_list) == 1
        assert diag_list[0].root_cause == RootCause.CONTAINER_STOPPED.value

        # Plan
        plans = await repo.session.scalars(
            sa.select(RemediationPlan).where(RemediationPlan.incident_id == inc.id)
        )
        plan_list = list(plans.all())
        assert len(plan_list) == 1
        assert plan_list[0].risk == RiskLevel.LOW

        # Policy decision
        pols = await repo.session.scalars(
            sa.select(PolicyDecision).where(PolicyDecision.incident_id == inc.id)
        )
        pol_list = list(pols.all())
        assert len(pol_list) == 1
        assert pol_list[0].decision == "ALLOW"

        # Execution
        execs = await repo.session.scalars(
            sa.select(Execution).where(Execution.incident_id == inc.id)
        )
        exec_list = list(execs.all())
        assert len(exec_list) == 1
        assert exec_list[0].status == ExecutionStatus.SUCCEEDED

        # Verification result
        verifs = await repo.session.scalars(
            sa.select(VerificationResult).where(VerificationResult.incident_id == inc.id)
        )
        verif_list = list(verifs.all())
        assert len(verif_list) == 1
        assert verif_list[0].passed is True
        assert verif_list[0].profile_version == "1.0"
        assert verif_list[0].attribution == "AGENT_HEALED"

    # Container was actually restarted
    assert container.restart_call_count == 1
    assert container.status == "running"


@pytest.mark.asyncio
async def test_m1_complete_lifecycle_scn002_cpu_saturation(session_factory):
    """SCN-002: CPU Saturation -> Diagnosis -> Plan -> Policy ALLOW -> Execute -> Verify SCN-002 -> RESOLVED."""
    cid = f"c2234567890abcdef{uuid4().hex[:16]}"
    res = await setup_test_resource(session_factory, cid)

    container = FakeDockerContainer(
        cid,
        "demo-api",
        {
            "self-healing.managed": "true",
            "com.docker.compose.service": "demo-api",
            "com.docker.compose.project": "self-healing-agent",
        },
        status="running",
    )
    fake_docker = FakeDockerClient({cid: container})
    adapter = DockerExecutionAdapter(session_factory, fake_docker)

    async with unit_of_work(session_factory, actor="test:lifecycle") as repo:
        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"m1-lifecycle-scn002-{uuid4().hex[:8]}",
            severity=Severity.HIGH,
            approval_required=False,
        )

    async def mock_evidence_node(state: IncidentGraphState, **kwargs):
        obs = ObservationBundle(
            resource_id=res.id,
            service_name="demo-api",
            container_status="running",
            normalized_cpu=0.92,
            raw_cpu_seconds_rate=0.92,
            firing_alerts=["HighCpuSaturation"],
        )
        async with unit_of_work(session_factory, actor="test:evidence") as repo:
            inc_db = await repo.session.get(Incident, inc.id)
            if inc_db.state == S.DETECTED:
                await repo.transition(inc.id, inc_db.version, S.TRIAGED)
        return {
            "status": "TRIAGED",
            "observation_bundle": obs.model_dump(mode="json"),
            "evidence_ids": [str(uuid4())],
            "container_id": cid,
            "binding_generation": 1,
            "service_name": "demo-api",
        }

    mock_verifier = make_mock_verifier(passed=True, profile_id="SCN-002")

    async def mock_verify_node(state: IncidentGraphState, **kwargs):
        from agentcore.nodes.verification import verify_node

        return await verify_node(
            state=state,
            verifier=mock_verifier,
            session_factory=session_factory,
            actor="test:worker",
        )

    async def mock_execute_node(state: IncidentGraphState, **kwargs):
        from agentcore.nodes.execution import execute_node

        return await execute_node(
            state=state,
            adapter=adapter,
            session_factory=session_factory,
            actor="test:worker",
        )

    runner = WorkflowRunner(
        session_factory=session_factory,
        worker_id="test:worker:m1",
        node_overrides={
            "collect_evidence": mock_evidence_node,
            "execute": mock_execute_node,
            "verify": mock_verify_node,
        },
    )

    final_state = await runner.run_incident(inc.id)
    assert final_state is not None
    assert final_state.get("status") == "RESOLVED"

    async with unit_of_work(session_factory, actor="test:check") as repo:
        inc_db = await repo.session.get(Incident, inc.id)
        assert inc_db.state == S.RESOLVED
        diag = await repo.session.scalar(
            sa.select(Diagnosis)
            .where(Diagnosis.incident_id == inc.id)
            .order_by(Diagnosis.created_at.desc())
        )
        assert diag is not None
        assert diag.root_cause == RootCause.CPU_SATURATION.value

    assert container.restart_call_count == 1


@pytest.mark.asyncio
async def test_m1_complete_lifecycle_scn003_api_unresponsive_with_approval(session_factory):
    """SCN-003: API Unresponsive -> Diagnosis -> Plan -> PENDING_APPROVAL -> Grant Approval -> Execute -> Verify -> RESOLVED."""
    cid = f"c3234567890abcdef{uuid4().hex[:16]}"
    res = await setup_test_resource(session_factory, cid)

    container = FakeDockerContainer(
        cid,
        "demo-api",
        {
            "self-healing.managed": "true",
            "com.docker.compose.service": "demo-api",
            "com.docker.compose.project": "self-healing-agent",
        },
        status="running",
    )
    fake_docker = FakeDockerClient({cid: container})
    adapter = DockerExecutionAdapter(session_factory, fake_docker)

    # Ingest incident with approval_required=True
    async with unit_of_work(session_factory, actor="test:lifecycle") as repo:
        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"m1-lifecycle-scn003-{uuid4().hex[:8]}",
            severity=Severity.HIGH,
            approval_required=True,
        )

    async def mock_evidence_node(state: IncidentGraphState, **kwargs):
        obs = ObservationBundle(
            resource_id=res.id,
            service_name="demo-api",
            container_status="running",
            http_status=503,
            consecutive_failures=5,
            latency_p95_seconds=2.5,
            firing_alerts=["ApiUnresponsive"],
        )
        async with unit_of_work(session_factory, actor="test:evidence") as repo:
            inc_db = await repo.session.get(Incident, inc.id)
            if inc_db.state == S.DETECTED:
                await repo.transition(inc.id, inc_db.version, S.TRIAGED)
        return {
            "status": "TRIAGED",
            "observation_bundle": obs.model_dump(mode="json"),
            "evidence_ids": [str(uuid4())],
            "container_id": cid,
            "binding_generation": 1,
            "service_name": "demo-api",
        }

    mock_verifier = make_mock_verifier(passed=True, profile_id="SCN-003")

    async def mock_verify_node(state: IncidentGraphState, **kwargs):
        from agentcore.nodes.verification import verify_node

        return await verify_node(
            state=state,
            verifier=mock_verifier,
            session_factory=session_factory,
            actor="test:worker",
        )

    async def mock_execute_node(state: IncidentGraphState, **kwargs):
        from agentcore.nodes.execution import execute_node

        return await execute_node(
            state=state,
            adapter=adapter,
            session_factory=session_factory,
            actor="test:worker",
        )

    runner = WorkflowRunner(
        session_factory=session_factory,
        worker_id="test:worker:m1",
        node_overrides={
            "collect_evidence": mock_evidence_node,
            "execute": mock_execute_node,
            "verify": mock_verify_node,
        },
    )

    # Phase 1: Workflow runs until PENDING_APPROVAL
    state_p1 = await runner.run_incident(inc.id)
    assert state_p1 is not None
    assert state_p1.get("status") == "PENDING_APPROVAL"

    async with unit_of_work(session_factory, actor="test:check") as repo:
        inc_db = await repo.session.get(Incident, inc.id)
        assert inc_db.state == S.PENDING_APPROVAL
        plan = await repo.session.scalar(
            sa.select(RemediationPlan)
            .where(RemediationPlan.incident_id == inc.id)
            .order_by(RemediationPlan.created_at.desc())
        )
        assert plan is not None

        # Ensure no mutation happened yet
        assert container.restart_call_count == 0

        # Operator approves the plan
        approver = User(
            id=uuid4(),
            username=f"approver_{uuid4().hex[:6]}",
            role=UserRole.APPROVER,
            password_hash="test-only",
        )
        repo.session.add(approver)
        await repo.session.flush()

        await repo.record_approval(
            incident_id=inc.id,
            plan_id=plan.id,
            plan_version=plan.version,
            approver_id=approver.id,
            decision="APPROVE",
            rejection_reason=None,
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        inc_db = await repo.transition(inc.id, inc_db.version, S.APPROVED)

    # Phase 2: Resume workflow from APPROVED state
    state_p2 = await runner.run_incident(inc.id, resume_metadata={"is_resumed_from_approval": True})
    assert state_p2 is not None
    assert state_p2.get("status") == "RESOLVED"

    async with unit_of_work(session_factory, actor="test:check") as repo:
        inc_final = await repo.session.get(Incident, inc.id)
        assert inc_final.state == S.RESOLVED
        assert inc_final.resolved_at is not None

    assert container.restart_call_count == 1


@pytest.mark.asyncio
async def test_m1_complete_lifecycle_policy_denial_safe_escalation(session_factory):
    """ADR-0003: Policy DENY -> safely escalates PLANNED -> ESCALATED without executing mutations."""
    cid = f"c4234567890abcdef{uuid4().hex[:16]}"
    res = await setup_test_resource(session_factory, cid)

    container = FakeDockerContainer(cid, "demo-api", {}, status="running")
    fake_docker = FakeDockerClient({cid: container})
    adapter = DockerExecutionAdapter(session_factory, fake_docker)

    async with unit_of_work(session_factory, actor="test:lifecycle") as repo:
        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"m1-lifecycle-deny-{uuid4().hex[:8]}",
            severity=Severity.HIGH,
            approval_required=False,
        )

    # Mock evidence & diagnosis to reach PLANNED
    async def mock_evidence(state, **kwargs):
        obs = ObservationBundle(
            resource_id=res.id,
            service_name="demo-api",
            container_status="exited",
            firing_alerts=["ContainerDown"],
        )
        async with unit_of_work(session_factory, actor="test:evidence") as repo:
            inc_db = await repo.session.get(Incident, inc.id)
            if inc_db.state == S.DETECTED:
                await repo.transition(inc.id, inc_db.version, S.TRIAGED)
        return {
            "status": "TRIAGED",
            "observation_bundle": obs.model_dump(mode="json"),
            "container_id": cid,
            "binding_generation": 1,
            "service_name": "demo-api",
        }

    # Policy node that enforces DENY
    async def mock_deny_policy(state, **kwargs):
        async with unit_of_work(session_factory, actor="test:policy") as repo:
            inc_db = await repo.session.get(Incident, inc.id)
            plan = await repo.session.scalar(
                sa.select(RemediationPlan)
                .where(RemediationPlan.incident_id == inc.id)
                .order_by(RemediationPlan.created_at.desc())
            )
            await repo.record_policy_decision(
                incident_id=inc.id,
                plan_id=plan.id,
                plan_version=plan.version,
                policy_id=None,
                policy_version=1,
                content_hash=plan.content_hash,
                container_id=cid,
                binding_generation=1,
                decision="DENY",
                evaluated_facts={"reason": "Prohibited target resource"},
                rule_results=[],
                reason_codes=["PROHIBITED_ACTION"],
            )
            # Safe escalation edge PLANNED -> ESCALATED
            if inc_db.state == S.PLANNED:
                await repo.transition(inc.id, inc_db.version, S.ESCALATED)
            await repo.create_escalation_record(
                incident_id=inc.id,
                title="Policy Denied",
                summary="Policy evaluation denied action: PROHIBITED_ACTION",
                root_cause="POLICY_DENIED",
                escalation_reason="PROHIBITED_ACTION",
                ticket_reference=f"POL-DENY-{inc.id.hex[:8]}",
            )
        return {
            "status": "POLICY_DENIED",
            "policy_decision": "DENY",
            "escalation_reason": "PROHIBITED_ACTION",
        }

    execute_called = False

    async def mock_execute(state, **kwargs):
        nonlocal execute_called
        execute_called = True
        return {"status": "EXECUTED"}

    runner = WorkflowRunner(
        session_factory=session_factory,
        worker_id="test:worker:m1",
        node_overrides={
            "collect_evidence": mock_evidence,
            "evaluate_policy": mock_deny_policy,
            "execute": mock_execute,
        },
    )

    final_state = await runner.run_incident(inc.id)
    assert final_state is not None
    assert final_state.get("status") == "ESCALATED"

    # Execution node was NEVER entered
    assert not execute_called
    assert container.restart_call_count == 0

    async with unit_of_work(session_factory, actor="test:check") as repo:
        inc_db = await repo.session.get(Incident, inc.id)
        assert inc_db.state == S.ESCALATED
        esc = await repo.session.scalars(
            sa.select(EscalationRecord).where(EscalationRecord.incident_id == inc.id)
        )
        assert len(list(esc.all())) >= 1


@pytest.mark.asyncio
async def test_m1_complete_lifecycle_verification_retry_budget_exhaustion(session_factory):
    """Failed verification after the one allowed retry escalates without scheduling another."""
    cid = f"c5234567890abcdef{uuid4().hex[:16]}"
    res = await setup_test_resource(session_factory, cid)

    async with unit_of_work(session_factory, actor="test:lifecycle") as repo:
        # One retry permits two executions; consume it through the legal retry transition.
        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"m1-lifecycle-exhaust-{uuid4().hex[:8]}",
            severity=Severity.HIGH,
            approval_required=False,
            retry_limit=1,
        )
        inc = await repo.transition(inc.id, 1, S.TRIAGED)
        inc = await repo.transition(inc.id, 2, S.DIAGNOSED)

        diag = Diagnosis(
            id=uuid4(),
            incident_id=inc.id,
            root_cause=RootCause.CONTAINER_STOPPED.value,
            confidence=0.95,
            evidence_ids=[],
            rule_id="DET-001",
            rule_version="1.0",
            contradictory_findings=[],
            escalation_reason=None,
            reasoning={"is_actionable": True},
            actor="test:lifecycle",
        )
        await repo.add(diag)

        steps_data = [
            {
                "position": 1,
                "resource_id": res.id,
                "action": "restart_container",
                "parameters": {"container_id": cid},
                "verification": {"profile": "SCN-001"},
            }
        ]
        plan = await repo.create_remediation_plan_with_steps(
            incident_id=inc.id,
            diagnosis_id=diag.id,
            version=1,
            risk=RiskLevel.LOW,
            content_hash="abc",
            container_id=cid,
            binding_generation=1,
            verification_profile="SCN-001",
            steps_data=steps_data,
        )
        steps = await repo.session.scalars(
            sa.select(RemediationStep).where(RemediationStep.plan_id == plan.id)
        )
        step = list(steps.all())[0]

        for attempt_number in (1, 2):
            inc = await repo.transition(inc.id, inc.version, S.PLANNED)
            inc = await repo.transition(inc.id, inc.version, S.EXECUTING)
            execution = await repo.record_execution(
                incident_id=inc.id,
                plan_id=plan.id,
                step_id=step.id,
                resource_id=res.id,
                attempt_number=attempt_number,
                container_id=cid,
                binding_generation=1,
                idempotency_key=f"exec:{inc.id}:{attempt_number}",
                pre_state={"status": "exited"},
            )
            execution = await repo.finish_execution(
                execution.id, execution.version, ExecutionStatus.RUNNING, result={}
            )
            execution = await repo.finish_execution(
                execution.id,
                execution.version,
                ExecutionStatus.SUCCEEDED,
                result={"status": "running"},
            )
            inc = await repo.transition(inc.id, inc.version, S.VERIFYING)
            if attempt_number == 1:
                inc = await repo.transition(inc.id, inc.version, S.DIAGNOSED)

        assert inc.attempts == inc.retry_limit == 1

    failing_verifier = make_mock_verifier(passed=False, profile_id="SCN-001")

    from agentcore.nodes.verification import verify_node

    state: IncidentGraphState = {
        "incident_id": str(inc.id),
        "resource_id": str(res.id),
        "execution_id": str(execution.id),
        "target_container_id": cid,
        "binding_generation": 1,
        "scenario_id": "SCN-001",
        "attempts": inc.attempts,
        "retry_limit": inc.retry_limit,
        "version": inc.version,
    }

    result = await verify_node(
        state=state,
        verifier=failing_verifier,
        session_factory=session_factory,
        actor="test:worker",
    )

    assert result["status"] == "VERIFICATION_ESCALATED"
    assert result["verification_passed"] is False
    assert result["retry_eligible"] is False

    async with unit_of_work(session_factory, actor="test:check") as repo:
        inc_db = await repo.session.get(Incident, inc.id)
        assert inc_db.state == S.ESCALATED
        esc = await repo.session.scalars(
            sa.select(EscalationRecord).where(EscalationRecord.incident_id == inc.id)
        )
        escalations = list(esc.all())
        assert len(escalations) == 1
        assert escalations[0].title == "Verification Failed - Retries Exhausted"
        assert (
            escalations[0].escalation_reason
            == failing_verifier.verify.return_value["failure_reason"]
        )
        assert inc_db.attempts == inc_db.retry_limit == 1
        schedules = await repo.session.scalars(
            sa.select(WorkflowSchedule).where(WorkflowSchedule.incident_id == inc.id)
        )
        assert list(schedules.all()) == []
        executions = list(
            (
                await repo.session.scalars(
                    sa.select(Execution).where(Execution.incident_id == inc.id)
                )
            ).all()
        )
        assert sorted(record.attempt_number for record in executions) == [1, 2]
        verification = await repo.session.scalar(
            sa.select(VerificationResult).where(VerificationResult.execution_id == execution.id)
        )
        assert verification is not None
        assert verification.passed is False
        assert verification.attempt_number == 2
