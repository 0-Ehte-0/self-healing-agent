import logging
from typing import Any
from uuid import UUID

from app.db.models import Incident, RemediationPlan, RemediationStep
from app.db.repositories.control_plane import unit_of_work
from provideradapters.base import (
    BaseProviderAdapter,
    ExecutionIntent,
    ExecutionOutcomeStatus,
    PrecheckFailedError,
    TargetRecreatedError,
)
from provideradapters.docker.adapter import DockerExecutionAdapter
from sharedmodels.enums import IncidentState
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentcore.graph.state import IncidentGraphState
from agentcore.nodes.protocols import ExecutorProtocol

logger = logging.getLogger(__name__)


async def execute_node(
    state: IncidentGraphState,
    adapter: BaseProviderAdapter | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    actor: str = "agent:execution_node",
    executor: ExecutorProtocol | None = None,
) -> dict[str, Any]:
    """Executes the remediating mutation step under strict safety bounds.

    1. Prechecks run while incident is in APPROVED or PLANNED.
    2. If prechecks fail: transitions directly APPROVED -> ESCALATED (or PLANNED -> ESCALATED).
    3. If prechecks pass: transitions to EXECUTING, acquires lock, and dispatches Docker restart.
    4. On success: transitions EXECUTING -> VERIFYING.
    5. On failure/uncertainty: transitions EXECUTING -> FAILED -> ESCALATED.
    """
    incident_id = UUID(state["incident_id"])
    resource_id = UUID(state["resource_id"])
    attempt = state.get("attempts", 0) + 1

    # Fallback to legacy protocol if specifically provided without session_factory
    if executor is not None and session_factory is None:
        step_id = UUID(state.get("current_step_id", state["incident_id"]))
        res = await executor.execute_step(
            incident_id=incident_id,
            step_id=step_id,
            resource_id=resource_id,
            attempt=attempt,
        )
        return {
            "status": "EXECUTED",
            "attempts": attempt,
        }

    if session_factory is None:
        raise ValueError("session_factory is required for authoritative execution lifecycle")

    if adapter is None:
        adapter = DockerExecutionAdapter(session_factory=session_factory, actor=actor)

    # 1. Retrieve Plan and Target Step
    plan_id_str = state.get("current_plan_id")
    if not plan_id_str:
        raise ValueError(f"No current_plan_id in workflow state for incident {incident_id}")

    plan_id = UUID(plan_id_str)
    async with unit_of_work(session_factory, actor=actor) as repo:
        plan, steps = await repo.get_plan_with_steps(plan_id)
        if not plan:
            raise LookupError(f"Remediation plan {plan_id} not found")

        # Select mutating restart_container step
        target_step = next((s for s in steps if s.action == "restart_container"), None)
        if not target_step:
            target_step = steps[0] if steps else None

        if not target_step:
            raise ValueError(f"Plan {plan_id} has no steps to execute")

        container_id = target_step.parameters.get(
            "container_id", state.get("target_container_id", "")
        )
        binding_generation = target_step.parameters.get(
            "binding_generation", state.get("binding_generation", 1)
        )
        service_name = target_step.parameters.get("service_name", "demo-api")
        timeout_seconds = target_step.parameters.get("timeout_seconds", 30)

    # 2. Build Typed ExecutionIntent
    intent = ExecutionIntent(
        incident_id=incident_id,
        plan_id=plan.id,
        plan_version=plan.version,
        step_id=target_step.id,
        attempt_number=attempt,
        resource_id=resource_id,
        container_id=container_id,
        binding_generation=int(binding_generation),
        service_name=service_name,
        timeout_seconds=int(timeout_seconds),
        idempotency_key=f"exec:{incident_id}:{plan.version}:{target_step.id}:{attempt}",
    )

    # 3. Phase 1: Prechecks (Incident is still in APPROVED or PLANNED)
    try:
        await adapter.precheck(intent)
    except (PrecheckFailedError, TargetRecreatedError, Exception) as e:
        reason = str(e)
        logger.warning("Precheck failed for incident %s: %s", incident_id, reason)

        # Transition directly APPROVED -> ESCALATED or PLANNED -> ESCALATED (ADR-0003)
        async with unit_of_work(session_factory, actor=actor) as repo:
            inc = await repo.session.get(Incident, incident_id)
            if inc and inc.state in {IncidentState.APPROVED, IncidentState.PLANNED}:
                await repo.transition(incident_id, inc.version, IncidentState.ESCALATED)
            await repo.create_escalation_record(
                incident_id=incident_id,
                title="Execution Precheck Failed",
                summary=f"Execution precheck failed prior to dispatch: {reason}",
                root_cause="PRECHECK_FAILED",
                escalation_reason=reason,
                ticket_reference=f"PRECHECK-{incident_id.hex[:8]}",
            )
            await repo.create_attention_item(
                incident_id=incident_id,
                severity="HIGH",
                reason="Precheck Failed",
                message=f"Precheck prevented mutation: {reason}",
            )
        return {
            "status": "PRECHECK_ESCALATED",
            "last_error": reason,
        }

    # 4. Phase 2: Dispatch (Transition to EXECUTING, execute mutation)
    async with unit_of_work(session_factory, actor=actor) as repo:
        inc = await repo.session.get(Incident, incident_id)
        if inc and inc.state in {IncidentState.APPROVED, IncidentState.PLANNED}:
            await repo.transition(incident_id, inc.version, IncidentState.EXECUTING)

    outcome = await adapter.execute(intent)

    if outcome.status == ExecutionOutcomeStatus.SUCCEEDED:
        # Transition EXECUTING -> VERIFYING
        cur_v = state.get("version", 1)
        async with unit_of_work(session_factory, actor=actor) as repo:
            inc = await repo.session.get(Incident, incident_id)
            if inc and inc.state == IncidentState.EXECUTING:
                inc = await repo.transition(incident_id, inc.version, IncidentState.VERIFYING)
                cur_v = inc.version
        return {
            "status": "EXECUTED",
            "version": cur_v,
            "attempts": attempt,
            "current_step_id": str(intent.step_id),
            "execution_id": str(outcome.execution_id) if outcome.execution_id else None,
        }
    else:
        # Transition EXECUTING -> FAILED -> ESCALATED
        err_msg = outcome.error or outcome.uncertainty_reason or "Execution failed"
        async with unit_of_work(session_factory, actor=actor) as repo:
            inc = await repo.session.get(Incident, incident_id)
            if inc and inc.state == IncidentState.EXECUTING:
                inc = await repo.transition(incident_id, inc.version, IncidentState.FAILED)
                await repo.transition(incident_id, inc.version, IncidentState.ESCALATED)
            await repo.create_escalation_record(
                incident_id=incident_id,
                title="Execution Failed or Uncertain",
                summary=f"Docker restart mutation failed or had uncertain outcome: {err_msg}",
                root_cause="EXECUTION_FAILED",
                escalation_reason=err_msg,
                ticket_reference=f"EXEC-FAIL-{incident_id.hex[:8]}",
            )
            await repo.create_attention_item(
                incident_id=incident_id,
                severity="CRITICAL",
                reason="Execution Failed or Uncertain",
                message=f"Remediation mutation failed: {err_msg}",
            )
        return {
            "status": "EXECUTION_FAILED",
            "last_error": err_msg,
        }
