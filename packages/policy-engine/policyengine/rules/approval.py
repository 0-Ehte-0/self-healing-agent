from policyengine.schemas.models import (
    AutomationMode,
    EvaluationContext,
    PolicyRuleSet,
    RuleEvaluationResult,
)


def evaluate_approval_requirement(
    ctx: EvaluationContext, rules: PolicyRuleSet
) -> RuleEvaluationResult:
    """Gate 10: Evaluates approval requirement and validates any existing approval grant.

    Enforces 30-minute approval decision expiry, plan version & content hash binding,
    and minimum approver role authorization.
    """
    requires_approval = (
        ctx.automation_mode == AutomationMode.APPROVAL_REQUIRED
        or ctx.confidence < rules.auto_approval_confidence_threshold
    )

    if not requires_approval:
        return RuleEvaluationResult(
            rule_name="approval_requirement",
            passed=True,
            observed_value="AUTOMATIC_MODE",
            threshold="AUTOMATIC_MODE",
            reason_code="AUTOMATIC_EXECUTION_PERMITTED",
            message="High confidence diagnosis and automatic mode permit unattended execution.",
        )

    # Approval is required. Inspect grant if one exists.
    grant = ctx.approval_grant
    if grant is None:
        return RuleEvaluationResult(
            rule_name="approval_requirement",
            passed=False,
            observed_value="NONE",
            threshold="VALID_GRANT",
            reason_code="APPROVAL_REQUIRED",
            message="Human approval is required before mutation can proceed.",
        )

    decision = grant.get("decision", "").upper()
    if decision == "REJECT":
        reason = grant.get("rejection_reason", "No reason provided")
        return RuleEvaluationResult(
            rule_name="approval_requirement",
            passed=False,
            observed_value=decision,
            threshold="APPROVE",
            reason_code="APPROVAL_REJECTED",
            message=f"Plan was rejected by operator: {reason}",
        )

    if decision != "APPROVE":
        return RuleEvaluationResult(
            rule_name="approval_requirement",
            passed=False,
            observed_value=decision,
            threshold="APPROVE",
            reason_code="APPROVAL_DECISION_INVALID",
            message=f"Unrecognized approval decision: {decision}",
        )

    # Verify plan version binding
    grant_plan_version = grant.get("plan_version")
    if grant_plan_version != ctx.plan_version:
        return RuleEvaluationResult(
            rule_name="approval_requirement",
            passed=False,
            observed_value=grant_plan_version,
            threshold=ctx.plan_version,
            reason_code="APPROVAL_PLAN_VERSION_MISMATCH",
            message=f"Approval plan version {grant_plan_version} does not match current plan version {ctx.plan_version}.",
        )

    # Verify content hash binding
    grant_hash = grant.get("content_hash")
    if grant_hash and grant_hash != ctx.content_hash:
        return RuleEvaluationResult(
            rule_name="approval_requirement",
            passed=False,
            observed_value=grant_hash,
            threshold=ctx.content_hash,
            reason_code="APPROVAL_CONTENT_HASH_MISMATCH",
            message="Approval was granted for a different plan content hash.",
        )

    # Verify 30-minute approval grant expiration
    expires_at = grant.get("expires_at")
    if expires_at:
        # Align timezones if needed
        curr_time = ctx.current_time
        if expires_at.tzinfo is None and curr_time.tzinfo is not None:
            expires_at = expires_at.replace(tzinfo=curr_time.tzinfo)
        elif expires_at.tzinfo is not None and curr_time.tzinfo is None:
            curr_time = curr_time.replace(tzinfo=expires_at.tzinfo)

        if expires_at <= curr_time:
            return RuleEvaluationResult(
                rule_name="approval_requirement",
                passed=False,
                observed_value=str(expires_at),
                threshold=str(curr_time),
                reason_code="APPROVAL_EXPIRED",
                message="Approval grant has expired (validity window is at most 30 minutes).",
            )

    # Verify approver role authorization
    role = grant.get("approver_role", "")
    if role not in {"approver", "admin"}:
        return RuleEvaluationResult(
            rule_name="approval_requirement",
            passed=False,
            observed_value=role,
            threshold="approver|admin",
            reason_code="UNAUTHORIZED_APPROVER_ROLE",
            message=f"Approval decision role '{role}' lacks permission to approve remediation plans.",
        )

    return RuleEvaluationResult(
        rule_name="approval_requirement",
        passed=True,
        observed_value="APPROVED",
        threshold="VALID_GRANT",
        reason_code="APPROVAL_VALID",
        message="Valid unexpired human approval grant verified.",
    )
