from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
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
    AutomationMode,
    EvaluationContext,
    PolicyRuleSet,
)


@pytest.fixture
def base_context() -> EvaluationContext:
    return EvaluationContext(
        incident_id=uuid4(),
        plan_id=uuid4(),
        plan_version=1,
        content_hash="abc123hash",
        container_id="demo-api-container-123",
        binding_generation=1,
        service_name="demo-api",
        environment="local",
        action="restart_container",
        risk="LOW",
        confidence=0.90,
        root_cause="CONTAINER_STOPPED",
        evidence_summary={},
        attempts=0,
        retry_limit=2,
        automation_mode=AutomationMode.APPROVAL_REQUIRED,
        current_time=datetime.now(UTC),
    )


@pytest.fixture
def default_rules() -> PolicyRuleSet:
    return PolicyRuleSet()


def test_kill_switch_rule(base_context: EvaluationContext, default_rules: PolicyRuleSet):
    # Active mode
    base_context.automation_mode = AutomationMode.APPROVAL_REQUIRED
    res = evaluate_kill_switch(base_context, default_rules)
    assert res.passed is True
    assert res.reason_code == "KILL_SWITCH_INACTIVE"

    # Disabled mode (kill switch active)
    base_context.automation_mode = AutomationMode.DISABLED
    res = evaluate_kill_switch(base_context, default_rules)
    assert res.passed is False
    assert res.reason_code == "GLOBAL_KILL_SWITCH_ACTIVE"


def test_emergency_stop_rule(base_context: EvaluationContext, default_rules: PolicyRuleSet):
    res = evaluate_emergency_stop(base_context, default_rules)
    assert res.passed is True

    base_context.emergency_stopped_resources = ["demo-api"]
    res = evaluate_emergency_stop(base_context, default_rules)
    assert res.passed is False
    assert res.reason_code == "RESOURCE_EMERGENCY_STOP"


def test_prohibited_action_and_risk(base_context: EvaluationContext, default_rules: PolicyRuleSet):
    res = evaluate_prohibited_action(base_context, default_rules)
    assert res.passed is True

    # Unknown / prohibited action
    base_context.action = "delete_all_databases"
    res = evaluate_prohibited_action(base_context, default_rules)
    assert res.passed is False
    assert res.reason_code == "PROHIBITED_ACTION"

    # Risk level exceeded
    base_context.action = "restart_container"
    base_context.risk = "HIGH"
    res = evaluate_prohibited_action(base_context, default_rules)
    assert res.passed is False
    assert res.reason_code == "RISK_LEVEL_EXCEEDED"


def test_allowlist_rule(base_context: EvaluationContext, default_rules: PolicyRuleSet):
    # demo-api is allowed
    base_context.service_name = "demo-api"
    res = evaluate_allowlist(base_context, default_rules)
    assert res.passed is True

    # demo-worker is not allowlisted in M1
    base_context.service_name = "demo-worker"
    res = evaluate_allowlist(base_context, default_rules)
    assert res.passed is False
    assert res.reason_code == "TARGET_NOT_ALLOWLISTED"


def test_environment_rule(base_context: EvaluationContext, default_rules: PolicyRuleSet):
    base_context.environment = "local"
    res = evaluate_environment(base_context, default_rules)
    assert res.passed is True

    base_context.environment = "production"
    res = evaluate_environment(base_context, default_rules)
    assert res.passed is False
    assert res.reason_code == "ENVIRONMENT_MISMATCH"


def test_confidence_and_evidence_rule(
    base_context: EvaluationContext, default_rules: PolicyRuleSet
):
    # High confidence (>= 0.85)
    base_context.confidence = 0.90
    res = evaluate_confidence_and_evidence(base_context, default_rules)
    assert res.passed is True
    assert res.reason_code == "HIGH_CONFIDENCE_ELIGIBLE"

    # Moderate confidence (0.60 <= c < 0.85) -> requires approval
    base_context.confidence = 0.75
    res = evaluate_confidence_and_evidence(base_context, default_rules)
    assert res.passed is True
    assert res.reason_code == "APPROVAL_REQUIRED_LOW_CONFIDENCE"

    # Low confidence (< 0.60) -> deny
    base_context.confidence = 0.50
    res = evaluate_confidence_and_evidence(base_context, default_rules)
    assert res.passed is False
    assert res.reason_code == "CONFIDENCE_TOO_LOW"

    # Ambiguous or unsupported root causes
    for cause in [
        "AMBIGUOUS",
        "INSUFFICIENT_EVIDENCE",
        "NO_ACTIVE_FAULT",
        "DEPENDENCY_UNAVAILABLE",
    ]:
        base_context.confidence = 0.95
        base_context.root_cause = cause
        res = evaluate_confidence_and_evidence(base_context, default_rules)
        assert res.passed is False
        assert res.reason_code == "EVIDENCE_INSUFFICIENT_OR_AMBIGUOUS"

    # Stale evidence
    base_context.root_cause = "CONTAINER_STOPPED"
    base_context.evidence_summary = {"evidence_age_seconds": 450.0}
    res = evaluate_confidence_and_evidence(base_context, default_rules)
    assert res.passed is False
    assert res.reason_code == "EVIDENCE_STALE"


def test_attempt_budget_rule(base_context: EvaluationContext, default_rules: PolicyRuleSet):
    # retry_limit = 2 -> max 3 attempts total (0, 1, 2)
    base_context.attempts = 0
    assert evaluate_attempt_budget(base_context, default_rules).passed is True

    base_context.attempts = 2
    assert evaluate_attempt_budget(base_context, default_rules).passed is True

    base_context.attempts = 3
    res = evaluate_attempt_budget(base_context, default_rules)
    assert res.passed is False
    assert res.reason_code == "ATTEMPT_BUDGET_EXHAUSTED"


def test_cooldown_rule(base_context: EvaluationContext, default_rules: PolicyRuleSet):
    # No prior execution
    base_context.last_execution_time = None
    assert evaluate_cooldown(base_context, default_rules).passed is True

    # Executed 120s ago (< 600s cooldown)
    now = datetime.now(UTC)
    base_context.current_time = now
    base_context.last_execution_time = now - timedelta(seconds=120)
    res = evaluate_cooldown(base_context, default_rules)
    assert res.passed is False
    assert res.reason_code == "IN_COOLDOWN"

    # Executed 650s ago (>= 600s cooldown)
    base_context.last_execution_time = now - timedelta(seconds=650)
    res = evaluate_cooldown(base_context, default_rules)
    assert res.passed is True
    assert res.reason_code == "COOLDOWN_SATISFIED"


def test_lock_rule(base_context: EvaluationContext, default_rules: PolicyRuleSet):
    base_context.is_resource_locked = False
    assert evaluate_resource_lock(base_context, default_rules).passed is True

    base_context.is_resource_locked = True
    base_context.lock_owner = "worker-node-1"
    res = evaluate_resource_lock(base_context, default_rules)
    assert res.passed is False
    assert res.reason_code == "RESOURCE_LOCKED"


def test_approval_requirement_and_grant(
    base_context: EvaluationContext, default_rules: PolicyRuleSet
):
    now = datetime.now(UTC)
    base_context.current_time = now

    # 1. Automatic mode with high confidence -> no approval required
    base_context.automation_mode = AutomationMode.AUTOMATIC
    base_context.confidence = 0.90
    res = evaluate_approval_requirement(base_context, default_rules)
    assert res.passed is True
    assert res.reason_code == "AUTOMATIC_EXECUTION_PERMITTED"

    # 2. Approval required mode, no grant -> fails
    base_context.automation_mode = AutomationMode.APPROVAL_REQUIRED
    base_context.approval_grant = None
    res = evaluate_approval_requirement(base_context, default_rules)
    assert res.passed is False
    assert res.reason_code == "APPROVAL_REQUIRED"

    # 3. Valid grant with APPROVE
    base_context.approval_grant = {
        "decision": "APPROVE",
        "plan_version": base_context.plan_version,
        "content_hash": base_context.content_hash,
        "expires_at": now + timedelta(minutes=25),
        "approver_role": "approver",
    }
    res = evaluate_approval_requirement(base_context, default_rules)
    assert res.passed is True
    assert res.reason_code == "APPROVAL_VALID"

    # 4. Rejected grant
    base_context.approval_grant["decision"] = "REJECT"
    base_context.approval_grant["rejection_reason"] = "Risk too high"
    res = evaluate_approval_requirement(base_context, default_rules)
    assert res.passed is False
    assert res.reason_code == "APPROVAL_REJECTED"

    # 5. Expired grant (> 30 minutes)
    base_context.approval_grant["decision"] = "APPROVE"
    base_context.approval_grant["expires_at"] = now - timedelta(minutes=1)
    res = evaluate_approval_requirement(base_context, default_rules)
    assert res.passed is False
    assert res.reason_code == "APPROVAL_EXPIRED"

    # 6. Plan version mismatch
    base_context.approval_grant["expires_at"] = now + timedelta(minutes=20)
    base_context.approval_grant["plan_version"] = 999
    res = evaluate_approval_requirement(base_context, default_rules)
    assert res.passed is False
    assert res.reason_code == "APPROVAL_PLAN_VERSION_MISMATCH"

    # 7. Content hash mismatch
    base_context.approval_grant["plan_version"] = base_context.plan_version
    base_context.approval_grant["content_hash"] = "wrong_hash"
    res = evaluate_approval_requirement(base_context, default_rules)
    assert res.passed is False
    assert res.reason_code == "APPROVAL_CONTENT_HASH_MISMATCH"

    # 8. Unauthorized role
    base_context.approval_grant["content_hash"] = base_context.content_hash
    base_context.approval_grant["approver_role"] = "viewer"
    res = evaluate_approval_requirement(base_context, default_rules)
    assert res.passed is False
    assert res.reason_code == "UNAUTHORIZED_APPROVER_ROLE"
