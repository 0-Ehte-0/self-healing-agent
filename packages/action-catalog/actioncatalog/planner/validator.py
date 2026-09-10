import hashlib
import json
import logging
from typing import Any

from sharedmodels.plan import RemediationPlanSchema, TargetBinding

from actioncatalog.registry import ActionRegistry, get_action_registry
from actioncatalog.schemas.action import ActionCategory

logger = logging.getLogger(__name__)


class PlanValidationError(ValueError):
    """Raised when a remediation plan fails strict safety or contract validation."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def compute_plan_content_hash(
    *,
    incident_id: str,
    diagnosis_id: str,
    version: int,
    risk: str,
    target_binding: dict[str, Any],
    steps: list[dict[str, Any]],
    verification_profile: str | None,
) -> str:
    """Computes a deterministic, canonical SHA-256 content hash for a plan.

    Canonical ordering ensures content hashing is strictly reproducible and sensitive
    to any parameter, target, step, or diagnosis tampering.
    """
    canonical_dict = {
        "incident_id": str(incident_id),
        "diagnosis_id": str(diagnosis_id),
        "version": int(version),
        "risk": str(risk),
        "target_binding": {
            "resource_id": str(target_binding["resource_id"]),
            "container_id": str(target_binding["container_id"]),
            "service_name": str(target_binding["service_name"]),
            "binding_generation": int(target_binding.get("binding_generation", 1)),
        },
        "steps": [
            {
                "position": int(s["position"]),
                "action": str(s["action"]),
                "resource_id": str(s["resource_id"]),
                "action_schema_version": str(s.get("action_schema_version", "1.0")),
                "parameters": s.get("parameters", {}),
                "verification": s.get("verification", {}),
            }
            for s in sorted(steps, key=lambda x: x["position"])
        ],
        "verification_profile": str(verification_profile) if verification_profile else None,
    }
    encoded = json.dumps(canonical_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PlanValidator:
    """Validates remediation plans against strict M1 safety and binding contracts."""

    def __init__(self, registry: ActionRegistry | None = None):
        self.registry = registry or get_action_registry()

    def validate(
        self,
        plan: RemediationPlanSchema | dict[str, Any],
        expected_target: TargetBinding | None = None,
        verify_content_hash: bool = True,
    ) -> list[str]:
        """Performs exhaustive validation of a plan.

        Returns a list of error strings (empty if completely valid).
        """
        errors: list[str] = []

        # 1. Pydantic schema validation
        if isinstance(plan, dict):
            try:
                plan_obj = RemediationPlanSchema.model_validate(plan)
            except Exception as exc:
                return [f"Schema validation failed: {exc}"]
        else:
            plan_obj = plan

        # 2. Target binding checks
        target = plan_obj.target_binding
        if expected_target is not None:
            if target.resource_id != expected_target.resource_id:
                errors.append(
                    f"Target resource {target.resource_id} does not match expected binding {expected_target.resource_id}"
                )
            if target.container_id != expected_target.container_id:
                errors.append(
                    f"Target container ID {target.container_id} does not match expected binding {expected_target.container_id}"
                )
            if target.service_name != expected_target.service_name:
                errors.append(
                    f"Target service {target.service_name} does not match expected binding {expected_target.service_name}"
                )
            if target.binding_generation != expected_target.binding_generation:
                errors.append(
                    f"Target binding generation {target.binding_generation} does not match expected {expected_target.binding_generation}"
                )

        if target.service_name != "demo-api":
            errors.append(
                f"Target service '{target.service_name}' is not allowlisted for M1 remediation"
            )

        # 3. Action and parameter checks for each step
        mutating_steps = []
        positions_seen = set()

        for step in plan_obj.steps:
            if step.position in positions_seen:
                errors.append(f"Duplicate step position: {step.position}")
            positions_seen.add(step.position)

            # Check action registered
            if not self.registry.has_action(step.action):
                errors.append(f"Unregistered action in step {step.position}: '{step.action}'")
                continue

            action_def = self.registry.get(step.action)

            # Validate parameters against action schema
            try:
                action_def.validate_parameters(step.parameters)
            except Exception as exc:
                errors.append(
                    f"Invalid parameters for action '{step.action}' at step {step.position}: {exc}"
                )

            # Check target binding consistency on steps
            if step.resource_id != target.resource_id:
                errors.append(
                    f"Step {step.position} resource_id {step.resource_id} does not match plan target resource_id {target.resource_id}"
                )

            step_container_id = step.parameters.get("container_id")
            if step_container_id and step_container_id != target.container_id:
                errors.append(
                    f"Step {step.position} specifies container_id '{step_container_id}' which does not match plan target '{target.container_id}' (possible forgery)"
                )

            step_service = step.parameters.get("service_name")
            if step_service and step_service != target.service_name:
                errors.append(
                    f"Step {step.position} specifies service '{step_service}' which does not match plan target service '{target.service_name}'"
                )

            # Track mutations
            if action_def.category == ActionCategory.WORKLOAD_MUTATION:
                mutating_steps.append(step)

        # 4. Mutating steps limit (M1 allows ONLY 1 single-target mutation)
        if len(mutating_steps) > 1:
            errors.append(
                f"Plan contains {len(mutating_steps)} mutating steps; M1 strictly prohibits multiple mutations in a single plan"
            )

        for m_step in mutating_steps:
            if m_step.action != "restart_container":
                errors.append(
                    f"Action '{m_step.action}' is not allowlisted for workload mutation in M1. Only 'restart_container' is allowed."
                )

        # 5. Step sequence checks for restart plan
        if mutating_steps:
            restart_pos = mutating_steps[0].position
            inspect_steps = [s for s in plan_obj.steps if s.action == "inspect_container"]
            if not inspect_steps or any(s.position >= restart_pos for s in inspect_steps):
                errors.append(
                    "Mutating action 'restart_container' must be preceded by an 'inspect_container' step"
                )

            stab_steps = [s for s in plan_obj.steps if s.action == "wait_for_stabilization"]
            if not stab_steps or any(s.position <= restart_pos for s in stab_steps):
                errors.append(
                    "Mutating action 'restart_container' must be followed by a 'wait_for_stabilization' step"
                )

        # 6. Verify content hash
        if verify_content_hash and not errors:
            recomputed = compute_plan_content_hash(
                incident_id=str(plan_obj.incident_id),
                diagnosis_id=str(plan_obj.diagnosis_id),
                version=plan_obj.version,
                risk=plan_obj.risk.value if hasattr(plan_obj.risk, "value") else str(plan_obj.risk),
                target_binding=target.model_dump(),
                steps=[s.model_dump() for s in plan_obj.steps],
                verification_profile=plan_obj.verification_profile,
            )
            if recomputed != plan_obj.content_hash:
                errors.append(
                    f"Plan content hash mismatch: expected {recomputed}, got {plan_obj.content_hash}"
                )

        return errors

    def assert_valid(
        self,
        plan: RemediationPlanSchema | dict[str, Any],
        expected_target: TargetBinding | None = None,
        verify_content_hash: bool = True,
    ) -> None:
        """Validates the plan and raises PlanValidationError if any error is found."""
        errors = self.validate(plan, expected_target, verify_content_hash)
        if errors:
            raise PlanValidationError(errors)
