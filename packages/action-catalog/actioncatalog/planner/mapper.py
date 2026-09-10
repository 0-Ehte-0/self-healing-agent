import logging
from typing import Any
from uuid import UUID, uuid4

from sharedmodels.enums import RiskLevel, RootCause, Severity
from sharedmodels.plan import RemediationPlanSchema, RemediationStepSchema, TargetBinding

from actioncatalog.planner.validator import PlanValidator, compute_plan_content_hash

logger = logging.getLogger(__name__)

SUPPORTED_RESTART_CAUSES = {
    RootCause.CONTAINER_STOPPED,
    RootCause.CPU_SATURATION,
    RootCause.API_UNRESPONSIVE,
    "CONTAINER_STOPPED",
    "CPU_SATURATION",
    "API_UNRESPONSIVE",
}


class DeterministicPlanner:
    """Deterministic mapper transforming diagnoses into validated, bounded single-target plans."""

    def __init__(
        self,
        validator: PlanValidator | None = None,
        actor: str = "agent:deterministic_planner",
    ):
        self.validator = validator or PlanValidator()
        self.actor = actor

    def create_plan(
        self,
        *,
        incident_id: UUID,
        diagnosis_id: UUID,
        root_cause: str | RootCause,
        target_binding: TargetBinding | dict[str, Any],
        version: int = 1,
        actor: str | None = None,
        plan_id: UUID | None = None,
    ) -> RemediationPlanSchema:
        """Constructs and validates a deterministic RemediationPlanSchema."""
        plan_actor = actor or self.actor
        target = (
            target_binding
            if isinstance(target_binding, TargetBinding)
            else TargetBinding.model_validate(target_binding)
        )
        pid = plan_id or uuid4()

        cause_str = root_cause.value if hasattr(root_cause, "value") else str(root_cause)

        if cause_str in SUPPORTED_RESTART_CAUSES:
            steps = [
                RemediationStepSchema(
                    id=uuid4(),
                    plan_id=pid,
                    resource_id=target.resource_id,
                    position=0,
                    action="inspect_container",
                    action_schema_version="1.0",
                    parameters={
                        "container_id": target.container_id,
                        "resource_id": str(target.resource_id),
                        "binding_generation": target.binding_generation,
                    },
                    verification={},
                ),
                RemediationStepSchema(
                    id=uuid4(),
                    plan_id=pid,
                    resource_id=target.resource_id,
                    position=1,
                    action="restart_container",
                    action_schema_version="1.0",
                    parameters={
                        "container_id": target.container_id,
                        "resource_id": str(target.resource_id),
                        "service_name": target.service_name,
                        "timeout_seconds": 30,
                        "binding_generation": target.binding_generation,
                    },
                    verification={"profile": "m1_default_restart_profile"},
                ),
                RemediationStepSchema(
                    id=uuid4(),
                    plan_id=pid,
                    resource_id=target.resource_id,
                    position=2,
                    action="wait_for_stabilization",
                    action_schema_version="1.0",
                    parameters={
                        "resource_id": str(target.resource_id),
                        "duration_seconds": 90,
                        "verification_profile": "m1_default_restart_profile",
                    },
                    verification={"profile": "m1_default_restart_profile"},
                ),
            ]
            risk = RiskLevel.LOW
            verification_profile = "m1_default_restart_profile"
        else:
            # Escalation plan for unsupported or ambiguous root causes
            steps = [
                RemediationStepSchema(
                    id=uuid4(),
                    plan_id=pid,
                    resource_id=target.resource_id,
                    position=0,
                    action="notify_operator",
                    action_schema_version="1.0",
                    parameters={
                        "incident_id": str(incident_id),
                        "reason": f"Escalation required for unhandled cause: {cause_str}",
                        "severity": Severity.HIGH.value,
                        "message": f"Autonomous remediation unavailable for root cause '{cause_str}'. Manual operator intervention is required.",
                    },
                    verification={},
                ),
                RemediationStepSchema(
                    id=uuid4(),
                    plan_id=pid,
                    resource_id=target.resource_id,
                    position=1,
                    action="open_incident_ticket",
                    action_schema_version="1.0",
                    parameters={
                        "incident_id": str(incident_id),
                        "title": f"Escalated Incident: {cause_str}",
                        "summary": f"Incident {incident_id} escalated due to unsupported root cause {cause_str}.",
                        "root_cause": cause_str,
                        "escalation_reason": f"Root cause '{cause_str}' requires manual operator resolution.",
                    },
                    verification={},
                ),
            ]
            risk = RiskLevel.LOW
            verification_profile = None

        content_hash = compute_plan_content_hash(
            incident_id=str(incident_id),
            diagnosis_id=str(diagnosis_id),
            version=version,
            risk=risk.value if hasattr(risk, "value") else str(risk),
            target_binding=target.model_dump(),
            steps=[s.model_dump() for s in steps],
            verification_profile=verification_profile,
        )

        plan = RemediationPlanSchema(
            id=pid,
            incident_id=incident_id,
            diagnosis_id=diagnosis_id,
            version=version,
            risk=risk,
            target_binding=target,
            steps=steps,
            verification_profile=verification_profile,
            content_hash=content_hash,
            approved=False,
            actor=plan_actor,
        )

        # Validate against safety boundaries and target contracts
        self.validator.assert_valid(plan, expected_target=target, verify_content_hash=True)
        return plan

    async def plan(
        self,
        incident_id: UUID,
        diagnosis_id: UUID,
        root_cause: str | RootCause | None = None,
        target_binding: TargetBinding | dict[str, Any] | None = None,
        version: int = 1,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Async protocol-compatible planning entry point."""
        if root_cause is None:
            raise ValueError("root_cause must be supplied to generate remediation plan")
        if target_binding is None:
            raise ValueError(
                "target_binding must be supplied to bind plan steps to an exact container"
            )

        plan = self.create_plan(
            incident_id=incident_id,
            diagnosis_id=diagnosis_id,
            root_cause=root_cause,
            target_binding=target_binding,
            version=version,
            actor=kwargs.get("actor", self.actor),
        )

        cause_str = root_cause.value if hasattr(root_cause, "value") else str(root_cause)
        is_actionable = cause_str in SUPPORTED_RESTART_CAUSES

        return {
            "id": str(plan.id),
            "plan": plan,
            "is_actionable": is_actionable,
            "version": plan.version,
            "content_hash": plan.content_hash,
        }
