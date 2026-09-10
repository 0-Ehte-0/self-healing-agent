from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from policyengine import (
    AutomationMode,
    EvaluationContext,
    PolicyDecisionOutcome,
    PolicyEngine,
    PolicyRuleSet,
)


@pytest.fixture
def valid_context() -> EvaluationContext:
    now = datetime.now(UTC)
    return EvaluationContext(
        incident_id=uuid4(),
        plan_id=uuid4(),
        plan_version=1,
        content_hash="valid_hash_123",
        container_id="demo-api-container-1",
        binding_generation=1,
        service_name="demo-api",
        environment="local",
        action="restart_container",
        risk="LOW",
        confidence=0.90,
        root_cause="CONTAINER_STOPPED",
        evidence_summary={"evidence_age_seconds": 30.0},
        attempts=0,
        retry_limit=2,
        automation_mode=AutomationMode.AUTOMATIC,
        current_time=now,
    )


def test_engine_allow_automatic(valid_context: EvaluationContext):
    engine = PolicyEngine()
    decision = engine.evaluate(valid_context)
    assert decision.decision == PolicyDecisionOutcome.ALLOW
    assert "HIGH_CONFIDENCE_ELIGIBLE" in decision.reason_codes
    assert decision.wait_reason is None


def test_engine_require_approval_when_mode_is_approval_required(valid_context: EvaluationContext):
    valid_context.automation_mode = AutomationMode.APPROVAL_REQUIRED
    engine = PolicyEngine()
    decision = engine.evaluate(valid_context)
    assert decision.decision == PolicyDecisionOutcome.REQUIRE_APPROVAL
    assert decision.wait_reason == "AWAITING_APPROVAL"
    assert "APPROVAL_REQUIRED" in decision.reason_codes


def test_engine_allow_with_valid_approval(valid_context: EvaluationContext):
    valid_context.automation_mode = AutomationMode.APPROVAL_REQUIRED
    now = valid_context.current_time
    valid_context.approval_grant = {
        "decision": "APPROVE",
        "plan_version": valid_context.plan_version,
        "content_hash": valid_context.content_hash,
        "expires_at": now + timedelta(minutes=20),
        "approver_role": "admin",
    }
    engine = PolicyEngine()
    decision = engine.evaluate(valid_context)
    assert decision.decision == PolicyDecisionOutcome.ALLOW
    assert "APPROVAL_VALID" in decision.reason_codes


def test_engine_deny_on_kill_switch(valid_context: EvaluationContext):
    valid_context.automation_mode = AutomationMode.DISABLED
    engine = PolicyEngine()
    decision = engine.evaluate(valid_context)
    assert decision.decision == PolicyDecisionOutcome.DENY
    assert "GLOBAL_KILL_SWITCH_ACTIVE" in decision.reason_codes


def test_engine_deny_on_emergency_stop(valid_context: EvaluationContext):
    valid_context.emergency_stopped_resources = [valid_context.service_name]
    engine = PolicyEngine()
    decision = engine.evaluate(valid_context)
    assert decision.decision == PolicyDecisionOutcome.DENY
    assert "RESOURCE_EMERGENCY_STOP" in decision.reason_codes


def test_engine_deny_on_unlisted_target(valid_context: EvaluationContext):
    valid_context.service_name = "demo-worker"
    engine = PolicyEngine()
    decision = engine.evaluate(valid_context)
    assert decision.decision == PolicyDecisionOutcome.DENY
    assert "TARGET_NOT_ALLOWLISTED" in decision.reason_codes


def test_engine_deny_on_low_confidence(valid_context: EvaluationContext):
    valid_context.confidence = 0.40
    engine = PolicyEngine()
    decision = engine.evaluate(valid_context)
    assert decision.decision == PolicyDecisionOutcome.DENY
    assert "CONFIDENCE_TOO_LOW" in decision.reason_codes


def test_engine_deny_on_expired_approval(valid_context: EvaluationContext):
    valid_context.automation_mode = AutomationMode.APPROVAL_REQUIRED
    now = valid_context.current_time
    valid_context.approval_grant = {
        "decision": "APPROVE",
        "plan_version": valid_context.plan_version,
        "content_hash": valid_context.content_hash,
        "expires_at": now - timedelta(seconds=10),
        "approver_role": "approver",
    }
    engine = PolicyEngine()
    decision = engine.evaluate(valid_context)
    assert decision.decision == PolicyDecisionOutcome.DENY
    assert "APPROVAL_EXPIRED" in decision.reason_codes


def test_engine_defer_on_cooldown(valid_context: EvaluationContext):
    now = valid_context.current_time
    valid_context.last_execution_time = now - timedelta(seconds=150)
    engine = PolicyEngine()
    decision = engine.evaluate(valid_context)
    assert decision.decision == PolicyDecisionOutcome.DEFER
    assert decision.wait_reason == "WAITING_FOR_COOLDOWN"
    assert decision.defer_until is not None
    assert "IN_COOLDOWN" in decision.reason_codes


def test_engine_defer_on_resource_lock(valid_context: EvaluationContext):
    valid_context.is_resource_locked = True
    valid_context.lock_owner = "worker-1"
    engine = PolicyEngine()
    decision = engine.evaluate(valid_context)
    assert decision.decision == PolicyDecisionOutcome.DEFER
    assert decision.wait_reason == "WAITING_FOR_LOCK"
    assert "RESOURCE_LOCKED" in decision.reason_codes
