from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.db.models import Diagnosis, Incident, Resource
from app.db.repositories.control_plane import unit_of_work
from sharedmodels.enums import ExecutionStatus, IncidentState, RiskLevel, Severity


@pytest.fixture
async def create_verifying_incident(session_factory):
    """Factory fixture returning a fully formed incident in VERIFYING state with a valid Execution."""

    async def _builder(
        scenario_id: str = "SCN-001",
        attempts: int = 0,
        retry_limit: int = 2,
    ):
        res_id = uuid4()
        cid = f"node11223344556677889900{res_id.hex[:16]}"
        step_id = uuid4()

        async with unit_of_work(session_factory, actor="test:fixture_setup") as repo:
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
            inc.retry_limit = retry_limit
            await repo.transition(inc.id, 1, IncidentState.TRIAGED)
            await repo.transition(inc.id, 2, IncidentState.DIAGNOSED)

            diag = Diagnosis(
                id=uuid4(),
                incident_id=inc.id,
                root_cause="CONTAINER_STOPPED",
                confidence=0.95,
                evidence_ids=["ev-test"],
                reasoning={"state": "exited"},
                actor="test:fixture_setup",
            )
            await repo.add(diag)

            plan = await repo.create_remediation_plan_with_steps(
                incident_id=inc.id,
                diagnosis_id=diag.id,
                version=1,
                risk=RiskLevel.LOW,
                content_hash=f"hash_{uuid4().hex}",
                container_id=cid,
                binding_generation=1,
                verification_profile=scenario_id,
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
                            "binding_generation": 1,
                        },
                        "verification": {"profile": scenario_id},
                    }
                ],
            )

            await repo.transition(inc.id, 3, IncidentState.PLANNED)

            exec_rec = await repo.record_execution(
                incident_id=inc.id,
                step_id=step_id,
                resource_id=res.id,
                idempotency_key=f"exec:{inc.id}:1:{step_id}:1",
                attempt_number=1,
                container_id=cid,
                binding_generation=1,
                pre_state={"status": "exited"},
                plan_id=plan.id,
            )
            await repo.finish_execution(exec_rec.id, 1, ExecutionStatus.RUNNING, result={})
            await repo.finish_execution(
                exec_rec.id, 2, ExecutionStatus.SUCCEEDED, result={"status": "running"}
            )

            await repo.transition(inc.id, 4, IncidentState.EXECUTING)
            inc = await repo.transition(inc.id, 5, IncidentState.VERIFYING)

            for att in range(1, attempts + 1):
                cur_v = inc.version
                await repo.transition(inc.id, cur_v, IncidentState.DIAGNOSED)
                await repo.transition(inc.id, cur_v + 1, IncidentState.PLANNED)
                await repo.transition(inc.id, cur_v + 2, IncidentState.EXECUTING)
                retry_exec = await repo.record_execution(
                    incident_id=inc.id,
                    step_id=step_id,
                    resource_id=res.id,
                    idempotency_key=f"exec:{inc.id}:1:{step_id}:{att + 1}",
                    attempt_number=att + 1,
                    container_id=cid,
                    binding_generation=1,
                    pre_state={"status": "exited"},
                    plan_id=plan.id,
                )
                await repo.finish_execution(retry_exec.id, 1, ExecutionStatus.RUNNING, result={})
                await repo.finish_execution(
                    retry_exec.id, 2, ExecutionStatus.SUCCEEDED, result={"status": "running"}
                )
                inc = await repo.transition(inc.id, cur_v + 3, IncidentState.VERIFYING)
                exec_rec = retry_exec

        state = {
            "incident_id": str(inc.id),
            "version": inc.version,
            "correlation_key": inc.correlation_key,
            "resource_id": str(res.id),
            "current_diagnosis_id": str(diag.id),
            "current_plan_id": str(plan.id),
            "execution_id": str(exec_rec.id),
            "attempts": attempts,
            "retry_limit": retry_limit,
            "approval_required": False,
            "is_approved": False,
            "target_container_id": cid,
            "binding_generation": 1,
            "root_cause": scenario_id,
        }

        return {
            "resource": res,
            "incident": inc,
            "plan": plan,
            "execution": exec_rec,
            "state": state,
            "container_id": cid,
        }

    return _builder
