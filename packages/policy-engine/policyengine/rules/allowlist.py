from policyengine.schemas.models import (
    EvaluationContext,
    PolicyRuleSet,
    RuleEvaluationResult,
)


def evaluate_allowlist(ctx: EvaluationContext, rules: PolicyRuleSet) -> RuleEvaluationResult:
    """Gate 4: Ensures target service/container is explicitly allowlisted."""
    is_allowlisted = ctx.service_name in rules.allowed_targets
    return RuleEvaluationResult(
        rule_name="target_allowlist",
        passed=is_allowlisted,
        observed_value=ctx.service_name,
        threshold=rules.allowed_targets,
        reason_code="TARGET_ALLOWLISTED" if is_allowlisted else "TARGET_NOT_ALLOWLISTED",
        message=f"Target service '{ctx.service_name}' is allowlisted."
        if is_allowlisted
        else f"Target service '{ctx.service_name}' is not in policy allowlist {rules.allowed_targets}.",
    )
