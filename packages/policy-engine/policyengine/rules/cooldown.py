from datetime import timedelta

from policyengine.schemas.models import (
    EvaluationContext,
    PolicyRuleSet,
    RuleEvaluationResult,
)


def evaluate_cooldown(ctx: EvaluationContext, rules: PolicyRuleSet) -> RuleEvaluationResult:
    """Gate 8: Enforces 600-second per-resource cooldown window.

    Per ADR-0004: Cooldown is strict; if within 600s of prior execution, defer until cooldown expires.
    """
    if ctx.last_execution_time is None:
        return RuleEvaluationResult(
            rule_name="resource_cooldown",
            passed=True,
            observed_value=None,
            threshold=rules.cooldown_seconds,
            reason_code="COOLDOWN_SATISFIED",
            message="No prior execution recorded for resource; cooldown satisfied.",
        )

    last_time = ctx.last_execution_time
    # Ensure tz-aware comparison
    if last_time.tzinfo is None and ctx.current_time.tzinfo is not None:
        last_time = last_time.replace(tzinfo=ctx.current_time.tzinfo)
    elif last_time.tzinfo is not None and ctx.current_time.tzinfo is None:
        ctx.current_time = ctx.current_time.replace(tzinfo=last_time.tzinfo)

    elapsed_seconds = (ctx.current_time - last_time).total_seconds()
    if elapsed_seconds < rules.cooldown_seconds:
        remaining = rules.cooldown_seconds - elapsed_seconds
        return RuleEvaluationResult(
            rule_name="resource_cooldown",
            passed=False,
            observed_value=elapsed_seconds,
            threshold=rules.cooldown_seconds,
            reason_code="IN_COOLDOWN",
            message=f"Resource cooldown active. {remaining:.1f}s remaining of {rules.cooldown_seconds}s requirement.",
        )

    return RuleEvaluationResult(
        rule_name="resource_cooldown",
        passed=True,
        observed_value=elapsed_seconds,
        threshold=rules.cooldown_seconds,
        reason_code="COOLDOWN_SATISFIED",
        message=f"Cooldown satisfied ({elapsed_seconds:.1f}s elapsed >= {rules.cooldown_seconds}s threshold).",
    )
