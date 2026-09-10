from policyengine.schemas.models import (
    EvaluationContext,
    PolicyRuleSet,
    RuleEvaluationResult,
)


def evaluate_resource_lock(ctx: EvaluationContext, rules: PolicyRuleSet) -> RuleEvaluationResult:
    """Gate 9: Checks if target resource lock is currently held by another worker."""
    if ctx.is_resource_locked:
        owner = ctx.lock_owner or "another worker"
        return RuleEvaluationResult(
            rule_name="resource_lock_eligibility",
            passed=False,
            observed_value=f"locked:{owner}",
            threshold="UNLOCKED",
            reason_code="RESOURCE_LOCKED",
            message=f"Target resource is locked by {owner}.",
        )

    return RuleEvaluationResult(
        rule_name="resource_lock_eligibility",
        passed=True,
        observed_value="unlocked",
        threshold="UNLOCKED",
        reason_code="RESOURCE_LOCK_AVAILABLE",
        message="Resource lock is available.",
    )
