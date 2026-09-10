import logging
from datetime import UTC, datetime, timezone
from typing import Any
from uuid import UUID

from policyengine import (
    AutomationMode,
    EvaluationContext,
    PolicyDecisionOutcome,
    PolicyEngine,
    PolicyEvaluationDecision,
    PolicyRuleSet,
)

from agentcore.graph.state import IncidentGraphState
from agentcore.nodes.protocols import PolicyEngineProtocol

logger = logging.getLogger(__name__)


async def evaluate_policy_node(
    state: IncidentGraphState,
    policy_engine: PolicyEngineProtocol | PolicyEngine | None = None,
    rules: PolicyRuleSet | None = None,
    context: EvaluationContext | None = None,
) -> dict[str, Any]:
    """Evaluates safety policies against proposed remediation plan.

    Implements the 10-gate sequence and returns authoritative decision and state.
    """
    engine = policy_engine or PolicyEngine(rules)

    plan_id_str = state.get("current_plan_id")
    plan_id = UUID(plan_id_str) if plan_id_str else None
    if not plan_id and not context:
        raise ValueError("Cannot evaluate policy without an active plan ID")

    # If an external async protocol is used (e.g. mock in tests)
    if hasattr(engine, "evaluate") and callable(engine.evaluate):
        import inspect

        if inspect.iscoroutinefunction(engine.evaluate):
            res = await engine.evaluate(plan_id=plan_id)
            if isinstance(res, dict):
                return {
                    "approval_required": res.get("approval_required", False),
                    "status": "POLICY_EVALUATED"
                    if not res.get("approval_required")
                    else "PENDING_APPROVAL",
                    "policy_decision": res.get("decision", "ALLOW"),
                    "wait_reason": "AWAITING_APPROVAL" if res.get("approval_required") else None,
                }

    # Use concrete PolicyEngine evaluation
    if context is None:
        incident_id = UUID(state["incident_id"]) if state.get("incident_id") else plan_id
        context = EvaluationContext(
            incident_id=incident_id,
            plan_id=plan_id,
            plan_version=state.get("plan_version", 1),
            content_hash=state.get("content_hash", "default-hash"),
            container_id=state.get("container_id", "demo-api-container"),
            binding_generation=state.get("binding_generation", 1),
            service_name=state.get("service_name", "demo-api"),
            environment=state.get("environment", "local"),
            action=state.get("action", "restart_container"),
            risk=state.get("risk", "LOW"),
            confidence=state.get("confidence", 0.90),
            root_cause=state.get("root_cause", "CONTAINER_STOPPED"),
            attempts=state.get("attempts", 0),
            retry_limit=state.get("retry_limit", 2),
            automation_mode=AutomationMode(state.get("automation_mode", "APPROVAL_REQUIRED")),
            current_time=datetime.now(UTC),
        )

    concrete_engine = engine if isinstance(engine, PolicyEngine) else PolicyEngine(rules)
    decision: PolicyEvaluationDecision = concrete_engine.evaluate(context, rules)

    if decision.decision == PolicyDecisionOutcome.ALLOW:
        return {
            "approval_required": False,
            "status": "POLICY_EVALUATED",
            "policy_decision": "ALLOW",
            "wait_reason": None,
        }
    elif decision.decision == PolicyDecisionOutcome.REQUIRE_APPROVAL:
        return {
            "approval_required": True,
            "status": "PENDING_APPROVAL",
            "policy_decision": "REQUIRE_APPROVAL",
            "wait_reason": "AWAITING_APPROVAL",
        }
    elif decision.decision == PolicyDecisionOutcome.DEFER:
        return {
            "approval_required": False,
            "status": "POLICY_DEFERRED",
            "policy_decision": "DEFER",
            "wait_reason": decision.wait_reason,
            "defer_until": decision.defer_until.isoformat() if decision.defer_until else None,
        }
    else:  # DENY
        return {
            "approval_required": False,
            "status": "ESCALATED",
            "policy_decision": "DENY",
            "wait_reason": None,
            "escalation_reason": f"Policy evaluation denied remediation: {', '.join(decision.reason_codes)}",
        }
