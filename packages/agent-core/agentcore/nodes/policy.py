import logging
from datetime import UTC, datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from app.db.models import Incident, RemediationPlan
from app.db.repositories.control_plane import unit_of_work
from policyengine import (
    AutomationMode,
    EvaluationContext,
    PolicyDecisionOutcome,
    PolicyEngine,
    PolicyEvaluationDecision,
    PolicyRuleSet,
)
from sharedmodels.enums import IncidentState
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentcore.graph.state import IncidentGraphState
from agentcore.nodes.protocols import PolicyEngineProtocol

logger = logging.getLogger(__name__)


async def evaluate_policy_node(
    state: IncidentGraphState,
    policy_engine: PolicyEngineProtocol | PolicyEngine | None = None,
    rules: PolicyRuleSet | None = None,
    context: EvaluationContext | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    actor: str = "worker:agent",
) -> dict[str, Any]:
    """Evaluates safety policies against proposed remediation plan.

    Implements the 10-gate sequence and returns authoritative decision and state.
    Persists decision and executes legal transitions when session_factory is present.
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

    # Extract facts from DB or state if context not pre-built
    if context is None:
        incident_id = UUID(state["incident_id"]) if state.get("incident_id") else plan_id
        plan_ver = state.get("plan_version", 1)
        c_hash = state.get("content_hash", "default-hash")
        cid = state.get("container_id") or state.get("target_container_id") or "demo-api-container"
        gen = state.get("binding_generation", 1)
        svc = state.get("service_name", "demo-api")
        act = state.get("action", "restart_container")
        rsk = state.get("risk", "LOW")
        conf = float(state.get("confidence", 0.90))
        rc = state.get("root_cause", "CONTAINER_STOPPED")
        attempts = state.get("attempts", 0)
        retries = state.get("retry_limit", 2)
        auto_mode_val = state.get("automation_mode")
        if auto_mode_val:
            auto_mode = AutomationMode(auto_mode_val)
        elif state.get("approval_required") is not None:
            auto_mode = (
                AutomationMode.APPROVAL_REQUIRED
                if state.get("approval_required")
                else AutomationMode.AUTOMATIC
            )
        else:
            auto_mode = AutomationMode.AUTOMATIC

        # If DB session available, fetch authoritative plan details
        if session_factory is not None and plan_id:
            async with unit_of_work(session_factory, actor=actor) as repo:
                db_plan = await repo.session.get(RemediationPlan, plan_id)
                if db_plan:
                    plan_ver = db_plan.version
                    c_hash = db_plan.content_hash
                    cid = db_plan.container_id or cid
                    gen = db_plan.binding_generation or gen
                    rsk = (
                        db_plan.risk.value if hasattr(db_plan.risk, "value") else str(db_plan.risk)
                    )
                if not auto_mode_val:
                    inc_check = await repo.session.get(Incident, incident_id)
                    if inc_check:
                        auto_mode = (
                            AutomationMode.APPROVAL_REQUIRED
                            if inc_check.approval_required
                            else AutomationMode.AUTOMATIC
                        )

        context = EvaluationContext(
            incident_id=incident_id,
            plan_id=plan_id,
            plan_version=plan_ver,
            content_hash=c_hash,
            container_id=cid,
            binding_generation=gen,
            service_name=svc,
            environment=state.get("environment", "local"),
            action=act,
            risk=rsk,
            confidence=conf,
            root_cause=rc,
            attempts=attempts,
            retry_limit=retries,
            automation_mode=auto_mode,
            current_time=datetime.now(UTC),
        )

    concrete_engine = engine if isinstance(engine, PolicyEngine) else PolicyEngine(rules)
    decision: PolicyEvaluationDecision = concrete_engine.evaluate(context, rules)

    decision_id = str(uuid4())
    cur_v = state.get("version", 1)
    incident_id = UUID(state["incident_id"]) if state.get("incident_id") else plan_id

    # Persist decision to DB and perform state transitions
    if session_factory is not None and incident_id and plan_id:
        async with unit_of_work(session_factory, actor=actor) as repo:
            # Check DB policy version
            import sqlalchemy as sa
            from app.db.models import Policy

            db_policy = await repo.session.scalar(
                sa.select(Policy)
                .where(Policy.action == "restart_container", Policy.enabled.is_(True))
                .order_by(Policy.version.desc())
                .limit(1)
            )
            pol_id = db_policy.id if db_policy else None
            pol_ver = db_policy.version if db_policy else 1

            persisted_decision = await repo.record_policy_decision(
                incident_id=incident_id,
                plan_id=plan_id,
                plan_version=context.plan_version,
                policy_id=pol_id,
                policy_version=pol_ver,
                content_hash=context.content_hash,
                container_id=context.container_id,
                binding_generation=context.binding_generation,
                decision=decision.decision.value,
                evaluated_facts=context.model_dump(mode="json"),
                rule_results=[
                    r.model_dump(mode="json") if hasattr(r, "model_dump") else r
                    for r in decision.rule_results
                ],
                reason_codes=decision.reason_codes,
            )
            decision_id = str(persisted_decision.id)

            inc = await repo.session.get(Incident, incident_id)
            if decision.decision == PolicyDecisionOutcome.REQUIRE_APPROVAL:
                if inc and inc.state == IncidentState.PLANNED:
                    if not inc.approval_required:
                        inc.approval_required = True
                        await repo.session.flush()
                    inc = await repo.transition(
                        incident_id, inc.version, IncidentState.PENDING_APPROVAL
                    )
                    cur_v = inc.version
            elif decision.decision == PolicyDecisionOutcome.DENY:
                if inc and inc.state == IncidentState.PLANNED:
                    inc = await repo.transition(incident_id, inc.version, IncidentState.ESCALATED)
                    cur_v = inc.version
                await repo.create_escalation_record(
                    incident_id=incident_id,
                    title="Policy Evaluation Denial",
                    summary=f"Remediation plan denied by policy: {', '.join(decision.reason_codes)}",
                    root_cause=context.root_cause,
                    escalation_reason=f"Policy denied: {', '.join(decision.reason_codes)}",
                    ticket_reference=f"POL-DENY-{incident_id.hex[:8].upper()}",
                )
            elif decision.decision == PolicyDecisionOutcome.DEFER and decision.defer_until:
                # Schedule future wake-up
                await repo.schedule_workflow(
                    incident_id=incident_id,
                    next_run_at=decision.defer_until,
                    wait_reason=decision.wait_reason or "COOLDOWN",
                )

    if decision.decision == PolicyDecisionOutcome.ALLOW:
        return {
            "approval_required": False,
            "status": "POLICY_EVALUATED",
            "policy_decision": "ALLOW",
            "policy_decision_id": decision_id,
            "wait_reason": None,
            "version": cur_v,
        }
    elif decision.decision == PolicyDecisionOutcome.REQUIRE_APPROVAL:
        return {
            "approval_required": True,
            "status": "PENDING_APPROVAL",
            "policy_decision": "REQUIRE_APPROVAL",
            "policy_decision_id": decision_id,
            "wait_reason": "AWAITING_APPROVAL",
            "version": cur_v,
        }
    elif decision.decision == PolicyDecisionOutcome.DEFER:
        return {
            "approval_required": False,
            "status": "POLICY_DEFERRED",
            "policy_decision": "DEFER",
            "policy_decision_id": decision_id,
            "wait_reason": decision.wait_reason or "COOLDOWN",
            "defer_until": decision.defer_until.isoformat() if decision.defer_until else None,
            "version": cur_v,
        }
    else:  # DENY
        return {
            "approval_required": False,
            "status": "POLICY_DENIED",
            "policy_decision": "DENY",
            "policy_decision_id": decision_id,
            "wait_reason": None,
            "escalation_reason": f"Policy evaluation denied remediation: {', '.join(decision.reason_codes)}",
            "version": cur_v,
        }
