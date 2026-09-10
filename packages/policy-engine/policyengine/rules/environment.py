from policyengine.schemas.models import (
    EvaluationContext,
    PolicyRuleSet,
    RuleEvaluationResult,
)


def evaluate_environment(ctx: EvaluationContext, rules: PolicyRuleSet) -> RuleEvaluationResult:
    """Gate 5: Verifies target environment matches policy configuration."""
    is_allowed = ctx.environment.lower() in [e.lower() for e in rules.allowed_environments]
    return RuleEvaluationResult(
        rule_name="target_environment",
        passed=is_allowed,
        observed_value=ctx.environment,
        threshold=rules.allowed_environments,
        reason_code="ENVIRONMENT_ALLOWED" if is_allowed else "ENVIRONMENT_MISMATCH",
        message=f"Environment '{ctx.environment}' is allowed."
        if is_allowed
        else f"Environment '{ctx.environment}' does not match allowed environments {rules.allowed_environments}.",
    )
