import logging
from typing import Any
from uuid import UUID

from actioncatalog.planner.mapper import DeterministicPlanner
from actioncatalog.planner.validator import PlanValidationError
from app.db.models import Diagnosis, Resource
from app.db.repositories.control_plane import unit_of_work
from sharedmodels.enums import IncidentState
from sharedmodels.plan import TargetBinding
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentcore.graph.state import IncidentGraphState
from agentcore.nodes.protocols import PlannerProtocol

logger = logging.getLogger(__name__)


async def plan_node(
    state: IncidentGraphState,
    planner: PlannerProtocol | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    actor: str = "agent:planning_node",
) -> dict[str, Any]:
    """Generates a typed remediation plan and commits authoritative state transitions.

    - On success: Persists plan atomically, invalidates older version approvals,
      and transitions DIAGNOSED -> PLANNED.
    - On failure/escalation: Persists EscalationRecord & AttentionItem and transitions DIAGNOSED -> ESCALATED.
    """
    if state.get("current_plan_id"):
        logger.info(
            "Plan %s already committed in database for incident %s; skipping re-planning on resume.",
            state.get("current_plan_id"),
            state.get("incident_id"),
        )
        return {"current_plan_id": state["current_plan_id"], "status": "PLANNED"}

    incident_id = UUID(state["incident_id"])
    diagnosis_id = (
        UUID(state["current_diagnosis_id"]) if state.get("current_diagnosis_id") else None
    )
    if not diagnosis_id:
        raise ValueError("Cannot plan remediation without an active diagnosis ID")

    resource_id = UUID(state["resource_id"])
    expected_version = state.get("version", 1)

    if planner is None:
        planner = DeterministicPlanner(actor=actor)

    # 1. Authoritative DB path when session_factory is present
    if session_factory is not None:
        async with unit_of_work(session_factory, actor=actor) as repo:
            diag = await repo.session.get(Diagnosis, diagnosis_id)
            if not diag:
                raise LookupError(f"Diagnosis {diagnosis_id} not found in database")

            resource = await repo.session.get(Resource, resource_id)
            if not resource:
                raise LookupError(f"Resource {resource_id} not found in database")

            container_id = (
                state.get("target_container_id")
                or state.get("container_id")
                or resource.labels.get("docker_container_id")
                or resource.labels.get("container_id", "demo-api-container-id")
            )
            binding_generation = state.get("binding_generation") or resource.labels.get(
                "binding_generation", 1
            )
            service_name = (
                state.get("service_name") or resource.labels.get("compose_service") or "demo-api"
            )

            target_binding = TargetBinding(
                resource_id=resource_id,
                container_id=container_id,
                service_name=service_name,
                binding_generation=int(binding_generation),
            )

            root_cause = diag.root_cause

            try:
                if isinstance(planner, DeterministicPlanner):
                    plan_obj = planner.create_plan(
                        incident_id=incident_id,
                        diagnosis_id=diagnosis_id,
                        root_cause=root_cause,
                        target_binding=target_binding,
                        version=1,
                        actor=actor,
                    )
                    is_actionable = plan_obj.verification_profile is not None
                else:
                    plan_res = await planner.plan(
                        incident_id=incident_id,
                        diagnosis_id=diagnosis_id,
                        root_cause=root_cause,
                        target_binding=target_binding,
                    )
                    plan_obj = plan_res.get("plan")
                    is_actionable = plan_res.get("is_actionable", True)

                if is_actionable and plan_obj is not None:
                    steps_data = [s.model_dump() for s in plan_obj.steps]
                    persisted_plan = await repo.create_remediation_plan_with_steps(
                        incident_id=incident_id,
                        diagnosis_id=diagnosis_id,
                        version=plan_obj.version,
                        risk=plan_obj.risk,
                        content_hash=plan_obj.content_hash,
                        container_id=target_binding.container_id,
                        binding_generation=target_binding.binding_generation,
                        verification_profile=plan_obj.verification_profile,
                        steps_data=steps_data,
                    )

                    updated_incident = await repo.transition(
                        incident_id=incident_id,
                        expected_version=expected_version,
                        target=IncidentState.PLANNED,
                    )

                    risk_val = (
                        persisted_plan.risk.value
                        if hasattr(persisted_plan.risk, "value")
                        else str(persisted_plan.risk)
                    )

                    return {
                        "current_plan_id": str(persisted_plan.id),
                        "status": "PLANNED",
                        "version": updated_incident.version,
                        "plan_version": persisted_plan.version,
                        "content_hash": persisted_plan.content_hash,
                        "container_id": target_binding.container_id,
                        "target_container_id": target_binding.container_id,
                        "binding_generation": target_binding.binding_generation,
                        "service_name": target_binding.service_name,
                        "risk": risk_val,
                        "action": "restart_container",
                        "verification_profile": persisted_plan.verification_profile,
                        "scenario_id": persisted_plan.verification_profile,
                    }

                else:
                    # Escalation path for non-actionable causes
                    reason = (
                        diag.escalation_reason
                        or f"Diagnosis root cause '{root_cause}' cannot be safely remediated"
                    )
                    ticket_ref = f"LOCAL-TICKET-{incident_id.hex[:8].upper()}"

                    await repo.create_escalation_record(
                        incident_id=incident_id,
                        title=f"Planning Escalation: {root_cause}",
                        summary=f"Incident {incident_id} escalated during planning due to {root_cause}",
                        root_cause=root_cause,
                        escalation_reason=reason,
                        ticket_reference=ticket_ref,
                    )

                    await repo.create_attention_item(
                        incident_id=incident_id,
                        severity="HIGH",
                        reason=f"Planning escalation for {root_cause}",
                        message=reason,
                    )

                    updated_incident = await repo.transition(
                        incident_id=incident_id,
                        expected_version=expected_version,
                        target=IncidentState.ESCALATED,
                    )

                    return {
                        "status": "PLANNING_ESCALATED",
                        "last_error": reason,
                        "version": updated_incident.version,
                    }

            except (PlanValidationError, ValueError) as exc:
                logger.warning("Planning validation failed for incident %s: %s", incident_id, exc)
                reason = str(exc)
                ticket_ref = f"LOCAL-TICKET-{incident_id.hex[:8].upper()}"

                await repo.create_escalation_record(
                    incident_id=incident_id,
                    title="Planning Validation Escalation",
                    summary=f"Incident {incident_id} failed plan validation",
                    root_cause=root_cause,
                    escalation_reason=reason,
                    ticket_reference=ticket_ref,
                )

                updated_incident = await repo.transition(
                    incident_id=incident_id,
                    expected_version=expected_version,
                    target=IncidentState.ESCALATED,
                )

                return {
                    "status": "PLANNING_ESCALATED",
                    "last_error": reason,
                    "version": updated_incident.version,
                }

    # 2. Fallback path when running without session_factory (e.g. mock/lightweight node tests)
    target_binding = TargetBinding(
        resource_id=resource_id,
        container_id=state.get("target_container_id", "demo-api-container-id"),
        service_name="demo-api",
        binding_generation=state.get("binding_generation", 1),
    )
    root_cause = state.get("root_cause", "CONTAINER_STOPPED")

    if isinstance(planner, DeterministicPlanner):
        plan_obj = planner.create_plan(
            incident_id=incident_id,
            diagnosis_id=diagnosis_id,
            root_cause=root_cause,
            target_binding=target_binding,
            version=1,
            actor=actor,
        )
        is_actionable = plan_obj.verification_profile is not None
        plan_id = str(plan_obj.id)
    else:
        plan_res = await planner.plan(
            incident_id=incident_id,
            diagnosis_id=diagnosis_id,
            root_cause=root_cause,
            target_binding=target_binding,
        )
        plan_id = str(plan_res.get("id")) if plan_res.get("id") else None
        is_actionable = plan_res.get("is_actionable", True)

    if is_actionable and plan_id:
        return {"current_plan_id": plan_id, "status": "PLANNED"}
    else:
        return {
            "status": "PLANNING_ESCALATED",
            "last_error": f"Diagnosis '{root_cause}' cannot be remediated automatically",
        }
