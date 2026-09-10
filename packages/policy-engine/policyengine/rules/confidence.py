from policyengine.schemas.models import (
    EvaluationContext,
    PolicyRuleSet,
    RuleEvaluationResult,
)


def evaluate_confidence_and_evidence(
    ctx: EvaluationContext, rules: PolicyRuleSet
) -> RuleEvaluationResult:
    """Gate 6: Evaluates diagnosis root-cause validity, evidence quality, and confidence score."""
    # 1. Unactionable or ambiguous root causes cannot proceed to mutation
    unsupported_causes = {
        "AMBIGUOUS",
        "INSUFFICIENT_EVIDENCE",
        "NO_ACTIVE_FAULT",
        "DEPENDENCY_UNAVAILABLE",
        "UNKNOWN",
    }
    if ctx.root_cause in unsupported_causes:
        return RuleEvaluationResult(
            rule_name="diagnosis_confidence",
            passed=False,
            observed_value=ctx.root_cause,
            threshold="ACTIONABLE_ROOT_CAUSE",
            reason_code="EVIDENCE_INSUFFICIENT_OR_AMBIGUOUS",
            message=f"Root cause '{ctx.root_cause}' cannot be remediated automatically.",
        )

    # 2. Check evidence freshness if timestamp is present in summary
    if "evidence_age_seconds" in ctx.evidence_summary:
        age = ctx.evidence_summary["evidence_age_seconds"]
        if age > rules.evidence_freshness_limit_seconds:
            return RuleEvaluationResult(
                rule_name="diagnosis_confidence",
                passed=False,
                observed_value=age,
                threshold=rules.evidence_freshness_limit_seconds,
                reason_code="EVIDENCE_STALE",
                message=f"Evidence age {age:.1f}s exceeds limit of {rules.evidence_freshness_limit_seconds}s.",
            )

    # 3. Check confidence thresholds
    if ctx.confidence < rules.min_approval_confidence_threshold:
        return RuleEvaluationResult(
            rule_name="diagnosis_confidence",
            passed=False,
            observed_value=ctx.confidence,
            threshold=rules.min_approval_confidence_threshold,
            reason_code="CONFIDENCE_TOO_LOW",
            message=f"Confidence {ctx.confidence:.2f} is below minimum remediation threshold {rules.min_approval_confidence_threshold:.2f}.",
        )

    if ctx.confidence < rules.auto_approval_confidence_threshold:
        return RuleEvaluationResult(
            rule_name="diagnosis_confidence",
            passed=True,
            observed_value=ctx.confidence,
            threshold=rules.auto_approval_confidence_threshold,
            reason_code="APPROVAL_REQUIRED_LOW_CONFIDENCE",
            message=f"Confidence {ctx.confidence:.2f} is in approval-required range [{rules.min_approval_confidence_threshold:.2f}, {rules.auto_approval_confidence_threshold:.2f}).",
        )

    return RuleEvaluationResult(
        rule_name="diagnosis_confidence",
        passed=True,
        observed_value=ctx.confidence,
        threshold=rules.auto_approval_confidence_threshold,
        reason_code="HIGH_CONFIDENCE_ELIGIBLE",
        message=f"Confidence {ctx.confidence:.2f} meets or exceeds automatic execution threshold {rules.auto_approval_confidence_threshold:.2f}.",
    )
