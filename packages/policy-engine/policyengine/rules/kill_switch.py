from policyengine.schemas.models import (
    AutomationMode,
    EvaluationContext,
    PolicyRuleSet,
    RuleEvaluationResult,
)


def evaluate_kill_switch(ctx: EvaluationContext, rules: PolicyRuleSet) -> RuleEvaluationResult:
    """Gate 1: Checks if global automation is disabled."""
    is_disabled = ctx.automation_mode == AutomationMode.DISABLED
    return RuleEvaluationResult(
        rule_name="global_kill_switch",
        passed=not is_disabled,
        observed_value=ctx.automation_mode.value,
        threshold=AutomationMode.DISABLED.value,
        reason_code="GLOBAL_KILL_SWITCH_ACTIVE" if is_disabled else "KILL_SWITCH_INACTIVE",
        message="Global automation is disabled via kill switch."
        if is_disabled
        else "Global kill switch is not active.",
    )
