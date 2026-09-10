from policyengine.schemas.models import (
    EvaluationContext,
    PolicyRuleSet,
    RuleEvaluationResult,
)


def evaluate_emergency_stop(ctx: EvaluationContext, rules: PolicyRuleSet) -> RuleEvaluationResult:
    """Gate 2: Checks if target resource is under emergency stop."""
    stopped = (
        ctx.service_name in ctx.emergency_stopped_resources
        or ctx.container_id in ctx.emergency_stopped_resources
    )
    return RuleEvaluationResult(
        rule_name="resource_emergency_stop",
        passed=not stopped,
        observed_value=ctx.service_name,
        threshold="NOT_IN_EMERGENCY_STOP",
        reason_code="RESOURCE_EMERGENCY_STOP" if stopped else "EMERGENCY_STOP_INACTIVE",
        message=f"Target resource '{ctx.service_name}' is currently under emergency stop."
        if stopped
        else f"Target resource '{ctx.service_name}' is not under emergency stop.",
    )
