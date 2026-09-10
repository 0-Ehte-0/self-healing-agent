from policyengine.schemas.models import (
    EvaluationContext,
    PolicyRuleSet,
    RuleEvaluationResult,
)


def evaluate_prohibited_action(
    ctx: EvaluationContext, rules: PolicyRuleSet
) -> RuleEvaluationResult:
    """Gate 3: Checks if proposed action is prohibited or violates risk policy."""
    if ctx.action not in rules.allowed_actions:
        return RuleEvaluationResult(
            rule_name="prohibited_action",
            passed=False,
            observed_value=ctx.action,
            threshold=rules.allowed_actions,
            reason_code="PROHIBITED_ACTION",
            message=f"Action '{ctx.action}' is not in the policy allowed actions list.",
        )

    # Risk level check: for M1 restart actions, risk must not exceed LOW
    if rules.require_low_risk_for_approval and ctx.risk.upper() not in {"LOW"}:
        return RuleEvaluationResult(
            rule_name="prohibited_action",
            passed=False,
            observed_value=ctx.risk,
            threshold="LOW",
            reason_code="RISK_LEVEL_EXCEEDED",
            message=f"Action risk level '{ctx.risk}' exceeds allowable risk 'LOW'.",
        )

    return RuleEvaluationResult(
        rule_name="prohibited_action",
        passed=True,
        observed_value=f"{ctx.action}:{ctx.risk}",
        threshold="ALLOWED",
        reason_code="ACTION_PERMITTED",
        message=f"Action '{ctx.action}' with risk '{ctx.risk}' is permitted.",
    )
