import logging
from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from agentcore.graph.state import IncidentGraphState
from agentcore.nodes import (
    collect_evidence_node,
    diagnose_node,
    escalate_node,
    evaluate_policy_node,
    execute_node,
    plan_node,
    resolve_node,
    verify_node,
    wait_for_approval_node,
)
from agentcore.runtime.policy import NodeRetryPolicy

logger = logging.getLogger(__name__)


def build_incident_workflow(
    checkpointer: BaseCheckpointSaver | None = None,
    node_overrides: dict[str, Callable] | None = None,
    retry_policy: NodeRetryPolicy | None = None,
) -> Any:
    """Builds the canonical LangGraph incident state machine.

    Includes ADR-0003 safe escalation edges: PLANNED -> ESCALATED and APPROVED -> ESCALATED.
    Integrates safe read retries via NodeRetryPolicy.
    """
    overrides = node_overrides or {}
    policy = retry_policy or NodeRetryPolicy()

    # Wrap each node with policy execution
    def wrap_node(name: str, fn: Callable) -> Callable:
        target_fn = overrides.get(name, fn)

        async def _wrapped(state: IncidentGraphState) -> dict[str, Any]:
            return await policy.execute_with_policy(name, target_fn, state)

        return _wrapped

    # pyrefly: ignore [bad-specialization]
    workflow = StateGraph(IncidentGraphState)

    # 1. Register canonical nodes
    workflow.add_node("collect_evidence", wrap_node("collect_evidence", collect_evidence_node))
    workflow.add_node("diagnose", wrap_node("diagnose", diagnose_node))
    workflow.add_node("plan", wrap_node("plan", plan_node))
    workflow.add_node("evaluate_policy", wrap_node("evaluate_policy", evaluate_policy_node))
    workflow.add_node("wait_for_approval", wrap_node("wait_for_approval", wait_for_approval_node))
    workflow.add_node("execute", wrap_node("execute", execute_node))
    workflow.add_node("verify", wrap_node("verify", verify_node))
    workflow.add_node("escalate", wrap_node("escalate", escalate_node))
    workflow.add_node("resolve", wrap_node("resolve", resolve_node))

    # 2. Wire standard edges
    workflow.add_edge(START, "collect_evidence")
    workflow.add_edge("collect_evidence", "diagnose")

    # Conditional routing from diagnose (unsupported or ambiguous diagnoses escalate)
    def route_after_diagnose(state: IncidentGraphState) -> str:
        if state.get("status") == "DIAGNOSIS_ESCALATED" or state.get("is_actionable") is False:
            return "escalate"
        return "plan"

    workflow.add_conditional_edges(
        "diagnose",
        route_after_diagnose,
        {
            "plan": "plan",
            "escalate": "escalate",
        },
    )

    # Conditional routing from plan (safe escalation if planning fails or escalates)
    def route_after_plan(state: IncidentGraphState) -> str:
        if state.get("status") == "PLANNING_ESCALATED":
            return "escalate"
        return "evaluate_policy"

    workflow.add_conditional_edges(
        "plan",
        route_after_plan,
        {
            "evaluate_policy": "evaluate_policy",
            "escalate": "escalate",
        },
    )

    # 3. Conditional routing from evaluate_policy
    def route_after_policy(state: IncidentGraphState) -> str:
        # Policy rejection safe edge (ADR-0003)
        if state.get("status") == "POLICY_DENIED":
            return "escalate"
        if state.get("approval_required") is True and state.get("is_approved") is not True:
            return "wait_for_approval"
        return "execute"

    workflow.add_conditional_edges(
        "evaluate_policy",
        route_after_policy,
        {
            "escalate": "escalate",
            "wait_for_approval": "wait_for_approval",
            "execute": "execute",
        },
    )

    # 4. Conditional routing from wait_for_approval
    def route_after_approval_wait(state: IncidentGraphState) -> str:
        if state.get("wait_reason") == "AWAITING_APPROVAL":
            # Interrupt / pause execution until approval arrives
            return END
        if state.get("is_approved") is True:
            return "execute"
        # Approval rejected or expired safe edge (ADR-0003)
        return "escalate"

    workflow.add_conditional_edges(
        "wait_for_approval",
        route_after_approval_wait,
        {
            END: END,
            "execute": "execute",
            "escalate": "escalate",
        },
    )

    # 5. Conditional routing from execute
    def route_after_execute(state: IncidentGraphState) -> str:
        if state.get("status") == "EXECUTED":
            return "verify"
        return "escalate"

    workflow.add_conditional_edges(
        "execute",
        route_after_execute,
        {
            "verify": "verify",
            "escalate": "escalate",
        },
    )

    # 6. Conditional routing from verify
    def route_after_verify(state: IncidentGraphState) -> str:
        if state.get("verification_passed") is True:
            return "resolve"
        if state.get("retry_eligible") is True:
            # Bounded retry loop back to diagnosis
            return "diagnose"
        return "escalate"

    workflow.add_conditional_edges(
        "verify",
        route_after_verify,
        {
            "resolve": "resolve",
            "diagnose": "diagnose",
            "escalate": "escalate",
        },
    )

    # 7. Terminal edges
    workflow.add_edge("escalate", END)
    workflow.add_edge("resolve", END)

    return workflow.compile(checkpointer=checkpointer)
