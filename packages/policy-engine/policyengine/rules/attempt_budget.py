from policyengine.schemas.models import (
    EvaluationContext,
    PolicyRuleSet,
    RuleEvaluationResult,
)


def evaluate_attempt_budget(ctx: EvaluationContext, rules: PolicyRuleSet) -> RuleEvaluationResult:
    """Gate 7: Checks remediation attempts against retry limit.

    Per ADR-0004: retry_limit = 2 permits at most 3 dispatches total (1 initial + 2 retries).
    """
    max_dispatches = 1 + rules.retry_limit
    within_budget = ctx.attempts < max_dispatches
    return RuleEvaluationResult(
        rule_name="attempt_budget",
        passed=within_budget,
        observed_value=ctx.attempts,
        threshold=max_dispatches,
        reason_code="ATTEMPT_BUDGET_AVAILABLE" if within_budget else "ATTEMPT_BUDGET_EXHAUSTED",
        message=f"Current attempts ({ctx.attempts}) within maximum allowed dispatches ({max_dispatches})."
        if within_budget
        else f"Remediation attempts ({ctx.attempts}) exhausted maximum allowed dispatches ({max_dispatches}).",
    )
