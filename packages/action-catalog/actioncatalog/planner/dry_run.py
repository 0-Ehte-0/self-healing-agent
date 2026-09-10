import logging
from typing import Any
from uuid import uuid4

from sharedmodels.plan import DryRunSchema, RemediationPlanSchema

from actioncatalog.planner.validator import PlanValidator
from actioncatalog.registry import ActionRegistry, get_action_registry

logger = logging.getLogger(__name__)


class DryRunExecutor:
    """Executes a non-mutating, isolated simulation of a remediation plan.

    Guarantees:
    - Validates plan structure, safety boundaries, and content hash.
    - Simulates policy evaluation and step preconditions.
    - Zero Docker mutations (never calls Docker restart).
    - Zero writes to verification_results table.
    - Never increments incident attempts counter.
    - Never modifies incident state or resolved_at.
    """

    def __init__(
        self,
        validator: PlanValidator | None = None,
        registry: ActionRegistry | None = None,
        actor: str = "agent:dry_run_executor",
    ):
        self.validator = validator or PlanValidator()
        self.registry = registry or get_action_registry()
        self.actor = actor

    async def execute_dry_run(
        self,
        plan: RemediationPlanSchema,
        policy_rules: dict[str, Any] | None = None,
        actor: str | None = None,
    ) -> DryRunSchema:
        """Runs validation and simulated execution of all plan steps."""
        run_actor = actor or self.actor

        # 1. Strict validation
        validation_errors = self.validator.validate(plan, verify_content_hash=True)
        is_valid = len(validation_errors) == 0

        # 2. Simulated Policy Evaluation
        rules = policy_rules or {
            "allowed_environment": "local",
            "managed_only": True,
            "confidence_threshold": 0.85,
            "max_risk": "LOW",
        }

        risk_val = plan.risk.value if hasattr(plan.risk, "value") else str(plan.risk)
        policy_evaluation = {
            "simulated": True,
            "policy_applied": "m1_default_policy",
            "plan_risk": risk_val,
            "rules_evaluated": {
                "allowed_environment": rules.get("allowed_environment") == "local",
                "risk_acceptable": risk_val in {"LOW", "MEDIUM"},
                "managed_target": plan.target_binding.service_name == "demo-api",
            },
            "eligible_for_execution": is_valid and risk_val == "LOW",
        }

        # 3. Simulate Steps (safe reads / declarations only, no real mutations)
        simulated_steps: list[dict[str, Any]] = []
        for step in plan.steps:
            if not self.registry.has_action(step.action):
                simulated_steps.append(
                    {
                        "position": step.position,
                        "action": step.action,
                        "status": "SIMULATION_FAILED",
                        "error": f"Unknown action: {step.action}",
                    }
                )
                continue

            action_instance = self.registry.get(step.action)
            preconditions_met = action_instance.check_preconditions(step.parameters)

            # Step simulation output (explicitly labeled SIMULATED)
            sim_result = {
                "position": step.position,
                "action": step.action,
                "category": action_instance.category.value,
                "preconditions_met": preconditions_met,
                "simulated_outcome": f"Simulated {step.action} execution completed successfully (no real mutation performed)",
                "action_schema_version": step.action_schema_version,
            }

            if step.action == "restart_container":
                sim_result["mutation_dispatched"] = False
                sim_result["docker_call_bypassed"] = True
                sim_result["target_container_id"] = step.parameters.get("container_id")
            elif step.action == "wait_for_stabilization":
                sim_result["verification_dispatched"] = False
                sim_result["sampling_bypassed"] = True
                sim_result["zero_verification_records_written"] = True

            simulated_steps.append(sim_result)

        dry_run_record = DryRunSchema(
            id=uuid4(),
            incident_id=plan.incident_id,
            plan_id=plan.id,
            plan_version=plan.version,
            content_hash=plan.content_hash,
            policy_evaluation=policy_evaluation,
            validation_result={
                "valid": is_valid,
                "errors": validation_errors,
            },
            simulated_steps=simulated_steps,
            actor=run_actor,
        )

        return dry_run_record
