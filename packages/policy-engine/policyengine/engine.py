import logging
from datetime import timedelta
from typing import Any

from policyengine.rules import (
    evaluate_allowlist,
    evaluate_approval_requirement,
    evaluate_attempt_budget,
    evaluate_confidence_and_evidence,
    evaluate_cooldown,
    evaluate_emergency_stop,
    evaluate_environment,
    evaluate_kill_switch,
    evaluate_prohibited_action,
    evaluate_resource_lock,
)
from policyengine.schemas.models import (
    EvaluationContext,
    PolicyDecisionOutcome,
    PolicyEvaluationDecision,
    PolicyRuleSet,
    RuleEvaluationResult,
)

logger = logging.getLogger(__name__)


class PolicyEngine:
    """Evaluates safety policies against remediation plans following the ordered gate sequence:

    1. Global Kill Switch
    2. Resource Emergency Stop
    3. Prohibited Action & Risk Level
    4. Target Allowlist
    5. Target Environment
    6. Evidence Validity & Diagnosis Confidence
    7. Remediation Attempt Budget
    8. Per-Resource Cooldown Window
    9. Resource Lock Eligibility
    10. Approval Requirement & Grant Validation
    """

    def __init__(self, default_rules: PolicyRuleSet | None = None):
        self.rules = default_rules or PolicyRuleSet()

    def evaluate(
        self,
        ctx: EvaluationContext,
        rules: PolicyRuleSet | None = None,
    ) -> PolicyEvaluationDecision:
        active_rules = rules or self.rules
        rule_results: list[RuleEvaluationResult] = []
        reason_codes: list[str] = []

        # Gate 1: Global Kill Switch
        r1 = evaluate_kill_switch(ctx, active_rules)
        rule_results.append(r1)
        if not r1.passed:
            reason_codes.append(r1.reason_code)

        # Gate 2: Resource Emergency Stop
        r2 = evaluate_emergency_stop(ctx, active_rules)
        rule_results.append(r2)
        if not r2.passed:
            reason_codes.append(r2.reason_code)

        # Gate 3: Prohibited Action & Risk
        r3 = evaluate_prohibited_action(ctx, active_rules)
        rule_results.append(r3)
        if not r3.passed:
            reason_codes.append(r3.reason_code)

        # Gate 4: Target Allowlist
        r4 = evaluate_allowlist(ctx, active_rules)
        rule_results.append(r4)
        if not r4.passed:
            reason_codes.append(r4.reason_code)

        # Gate 5: Target Environment
        r5 = evaluate_environment(ctx, active_rules)
        rule_results.append(r5)
        if not r5.passed:
            reason_codes.append(r5.reason_code)

        # Gate 6: Evidence Validity & Diagnosis Confidence
        r6 = evaluate_confidence_and_evidence(ctx, active_rules)
        rule_results.append(r6)
        reason_codes.append(r6.reason_code)

        # Gate 7: Remediation Attempt Budget
        r7 = evaluate_attempt_budget(ctx, active_rules)
        rule_results.append(r7)
        if not r7.passed:
            reason_codes.append(r7.reason_code)

        # Gate 8: Per-Resource Cooldown Window
        r8 = evaluate_cooldown(ctx, active_rules)
        rule_results.append(r8)
        if not r8.passed:
            reason_codes.append(r8.reason_code)

        # Gate 9: Resource Lock Eligibility
        r9 = evaluate_resource_lock(ctx, active_rules)
        rule_results.append(r9)
        if not r9.passed:
            reason_codes.append(r9.reason_code)

        # Gate 10: Approval Requirement & Grant Validation
        r10 = evaluate_approval_requirement(ctx, active_rules)
        rule_results.append(r10)
        reason_codes.append(r10.reason_code)

        # Determine Structured Outcome Precedence:
        # 1. Hard Denials (Gates 1-5, Gate 6 hard failure, Gate 7, Gate 10 rejected/expired/invalid)
        hard_deny_rules = [r1, r2, r3, r4, r5, r7]
        hard_deny = any(not r.passed for r in hard_deny_rules) or (not r6.passed)
        if not hard_deny and not r10.passed:
            if r10.reason_code in {
                "APPROVAL_REJECTED",
                "APPROVAL_EXPIRED",
                "APPROVAL_PLAN_VERSION_MISMATCH",
                "APPROVAL_CONTENT_HASH_MISMATCH",
                "UNAUTHORIZED_APPROVER_ROLE",
                "APPROVAL_DECISION_INVALID",
            }:
                hard_deny = True

        defer_until = None
        wait_reason = None

        if hard_deny:
            decision = PolicyDecisionOutcome.DENY
        elif not r8.passed:  # In Cooldown
            decision = PolicyDecisionOutcome.DEFER
            wait_reason = "WAITING_FOR_COOLDOWN"
            if ctx.last_execution_time:
                last_time = ctx.last_execution_time
                if last_time.tzinfo is None and ctx.current_time.tzinfo is not None:
                    last_time = last_time.replace(tzinfo=ctx.current_time.tzinfo)
                defer_until = last_time + timedelta(seconds=active_rules.cooldown_seconds)
        elif not r9.passed:  # Resource Locked
            decision = PolicyDecisionOutcome.DEFER
            wait_reason = "WAITING_FOR_LOCK"
        elif not r10.passed and r10.reason_code == "APPROVAL_REQUIRED":
            decision = PolicyDecisionOutcome.REQUIRE_APPROVAL
            wait_reason = "AWAITING_APPROVAL"
        else:
            decision = PolicyDecisionOutcome.ALLOW

        evaluated_facts: dict[str, Any] = {
            "incident_id": str(ctx.incident_id),
            "plan_id": str(ctx.plan_id),
            "plan_version": ctx.plan_version,
            "content_hash": ctx.content_hash,
            "service_name": ctx.service_name,
            "container_id": ctx.container_id,
            "environment": ctx.environment,
            "action": ctx.action,
            "risk": ctx.risk,
            "confidence": ctx.confidence,
            "root_cause": ctx.root_cause,
            "attempts": ctx.attempts,
            "retry_limit": ctx.retry_limit,
            "automation_mode": ctx.automation_mode.value,
            "is_resource_locked": ctx.is_resource_locked,
            "has_approval_grant": ctx.approval_grant is not None,
        }

        return PolicyEvaluationDecision(
            decision=decision,
            policy_name=active_rules.name,
            policy_version=active_rules.version,
            plan_id=ctx.plan_id,
            plan_version=ctx.plan_version,
            content_hash=ctx.content_hash,
            container_id=ctx.container_id,
            binding_generation=ctx.binding_generation,
            evaluated_facts=evaluated_facts,
            rule_results=rule_results,
            reason_codes=reason_codes,
            defer_until=defer_until,
            wait_reason=wait_reason,
        )
