import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.db.models import Incident
from app.db.repositories.control_plane import unit_of_work
from sharedmodels.enums import IncidentState
from sqlalchemy.ext.asyncio import async_sessionmaker

from agentcore.graph.state import IncidentGraphState
from agentcore.nodes.protocols import VerifierProtocol
from agentcore.verification.scheduling import (
    DEFAULT_RESOURCE_COOLDOWN_SECONDS,
    compute_attempt_counters,
    is_retry_eligible,
)

logger = logging.getLogger(__name__)


async def verify_node(
    state: IncidentGraphState,
    verifier: VerifierProtocol | None = None,
    session_factory: async_sessionmaker | None = None,
    scheduler: Any | None = None,
    actor: str = "worker:verifier",
    cooldown_seconds: int = DEFAULT_RESOURCE_COOLDOWN_SECONDS,
) -> dict[str, Any]:
    """Verifies target health independently using fresh telemetry across a 90-second window.

    Per Section 11 & ADR-0007:
    - Verifies multi-signal telemetry independently (never reuses executor flag).
    - Requires 90-second continuous healthy stabilization window.
    - Resolves incident only when verified and attributable to agent healing.
    - On failure, schedules 600-second cooldown and transitions to DIAGNOSED if retry budget remains.
    - If retries are exhausted or fault was cleared externally, transitions safely to ESCALATED.
    """
    if verifier is None:
        from agentcore.verification.verifier import IndependentVerifier

        verifier = IndependentVerifier()

    incident_id_str = state.get("incident_id")
    if not incident_id_str:
        return {"status": "VERIFIED", "verification_passed": False, "retry_eligible": False}

    incident_id = UUID(incident_id_str)
    resource_id = UUID(state["resource_id"])
    execution_id_raw = state.get("execution_id") or state.get("current_execution_id")
    if not execution_id_raw and session_factory is not None:
        async with unit_of_work(session_factory, actor=actor) as repo:
            import sqlalchemy as sa
            from app.db.models import Execution

            latest_exec = await repo.session.scalar(
                sa.select(Execution)
                .where(Execution.incident_id == incident_id)
                .order_by(Execution.created_at.desc())
                .limit(1)
            )
            if latest_exec:
                execution_id_raw = str(latest_exec.id)

    execution_id = UUID(str(execution_id_raw or incident_id_str))

    target_container_id = state.get("target_container_id") or state.get("container_id")
    binding_generation = state.get("binding_generation")
    cause_to_scenario = {
        "CONTAINER_STOPPED": "SCN-001",
        "CPU_SATURATION": "SCN-002",
        "API_UNRESPONSIVE": "SCN-003",
    }
    rc = state.get("root_cause")
    scenario_id = (
        state.get("scenario_id")
        or state.get("verification_profile")
        or (cause_to_scenario.get(rc) if rc else None)
        or rc
    )

    # 1. Execute Independent Telemetry Verification
    verdict_dict = await verifier.verify(
        execution_id=execution_id,
        resource_id=resource_id,
        target_container_id=target_container_id,
        binding_generation=binding_generation,
        scenario_id=scenario_id,
    )

    passed = bool(verdict_dict.get("passed", False))
    attribution = verdict_dict.get("attribution", "AGENT_HEALED")
    if hasattr(attribution, "value"):
        attribution = attribution.value
    attribution_str = str(attribution)

    health_score = float(verdict_dict.get("health_score", 0.0))
    checks = verdict_dict.get("checks", {})
    samples = verdict_dict.get("samples", [])
    warm_up_duration = verdict_dict.get("warm_up_duration_seconds")
    stabilization_resets = int(verdict_dict.get("stabilization_resets", 0))
    window_start = verdict_dict.get("window_start") or datetime.now(UTC)
    window_end = verdict_dict.get("window_end") or datetime.now(UTC)
    profile_version = verdict_dict.get("profile_version", "1.0")
    failure_reason = verdict_dict.get("failure_reason")

    current_version = state.get("version", 1)
    attempts = state.get("attempts", 0)
    retry_limit = state.get("retry_limit", 2)

    # 2. Database Persistence and Transitions
    if session_factory:
        async with unit_of_work(session_factory, actor=actor) as repo:
            inc = await repo.session.get(Incident, incident_id)
            if inc:
                current_version = inc.version
                attempts = inc.attempts
                retry_limit = inc.retry_limit

            # Persist comprehensive verification result
            await repo.record_verification_result(
                execution_id=execution_id,
                incident_id=incident_id,
                passed=passed,
                window_start=window_start,
                window_end=window_end,
                health_score=health_score,
                checks=checks if isinstance(checks, dict) else {},
                profile_version=profile_version,
                attempt_number=attempts + 1,
                warm_up_duration_seconds=warm_up_duration,
                stabilization_resets=stabilization_resets,
                samples=[s.model_dump() if hasattr(s, "model_dump") else s for s in samples],
                attribution=attribution_str,
            )

            # Scenario A: Passing & Attributable to Agent
            if passed and attribution_str == "AGENT_HEALED":
                if inc and inc.state == IncidentState.VERIFYING:
                    inc = await repo.transition(incident_id, inc.version, IncidentState.RESOLVED)
                    current_version = inc.version
                logger.info(
                    f"Incident {incident_id} successfully verified and transitioned to RESOLVED."
                )
                return {
                    "status": "RESOLVED",
                    "version": current_version,
                    "verification_passed": True,
                    "retry_eligible": False,
                    "attribution": attribution_str,
                }

            # Scenario B: External Fault Clearance / Inconclusive
            if attribution_str in {"EXTERNALLY_RECOVERED", "INCONCLUSIVE"}:
                reason = failure_reason or "Fault expired or cleared externally before verification"
                if inc and inc.state == IncidentState.VERIFYING:
                    inc = await repo.transition(incident_id, inc.version, IncidentState.ESCALATED)
                    current_version = inc.version
                await repo.create_escalation_record(
                    incident_id=incident_id,
                    title="External Fault Clearance",
                    summary=f"Incident {incident_id} recovered externally: {reason}",
                    root_cause="EXTERNALLY_RECOVERED",
                    escalation_reason=reason,
                    ticket_reference=f"EXT-REC-{incident_id.hex[:8]}",
                )
                await repo.create_attention_item(
                    incident_id=incident_id,
                    severity="MEDIUM",
                    reason="External Recovery Not Attributable to Agent",
                    message=f"Incident {incident_id} was cleared externally; cannot attribute to agent healing.",
                )
                logger.warning(
                    f"Incident {incident_id} escalated due to external recovery: {reason}"
                )
                return {
                    "status": "ESCALATED",
                    "version": current_version,
                    "verification_passed": False,
                    "retry_eligible": False,
                    "attribution": attribution_str,
                    "last_error": reason,
                }

            # Scenario C: Verification Failed - Evaluate Bounded Retry
            eligible, eligibility_reason = is_retry_eligible(
                attempts=attempts,
                retry_limit=retry_limit,
                cooldown_seconds=cooldown_seconds,
            )

            if eligible:
                # Transition VERIFYING -> DIAGNOSED (which atomically increments inc.attempts in DB)
                if inc and inc.state == IncidentState.VERIFYING:
                    inc = await repo.transition(incident_id, inc.version, IncidentState.DIAGNOSED)
                    current_version = inc.version
                    attempts = inc.attempts

                # Schedule 600-second resource cooldown wake-up
                from agentcore.runtime.scheduler import WorkflowScheduler
                from agentcore.runtime.scheduling import schedule_retry_cooldown

                sched = scheduler or WorkflowScheduler(session_factory, actor=actor)
                await schedule_retry_cooldown(
                    scheduler=sched,
                    incident_id=incident_id,
                    incident_version=current_version,
                    cooldown_seconds=cooldown_seconds,
                    attempt=attempts,
                )
                logger.info(
                    f"Incident {incident_id} failed verification; scheduled {cooldown_seconds}s cooldown (attempt {attempts}/{retry_limit})."
                )
                return {
                    "status": "COOLDOWN_SCHEDULED",
                    "version": current_version,
                    "verification_passed": False,
                    "retry_eligible": True,
                    "wait_reason": "COOLDOWN",
                    "attempts": attempts,
                    "last_error": failure_reason or "Verification failed; retry scheduled",
                }
            else:
                # Retries exhausted or deadline exceeded -> Transition VERIFYING -> ESCALATED
                reason = failure_reason or eligibility_reason
                if inc and inc.state == IncidentState.VERIFYING:
                    inc = await repo.transition(incident_id, inc.version, IncidentState.ESCALATED)
                    current_version = inc.version
                await repo.create_escalation_record(
                    incident_id=incident_id,
                    title="Verification Failed - Retries Exhausted",
                    summary=f"Remediation verification failed: {reason}",
                    root_cause="VERIFICATION_FAILED",
                    escalation_reason=reason,
                    ticket_reference=f"VERIF-FAIL-{incident_id.hex[:8]}",
                )
                await repo.create_attention_item(
                    incident_id=incident_id,
                    severity="CRITICAL",
                    reason="Remediation Verification Failed",
                    message=f"Incident {incident_id} failed verification: {reason}",
                )
                logger.warning(f"Incident {incident_id} escalated: {reason}")
                return {
                    "status": "VERIFICATION_ESCALATED",
                    "version": current_version,
                    "verification_passed": False,
                    "retry_eligible": False,
                    "last_error": reason,
                }

    # In-memory / Mock fallback when session_factory is not provided
    counters = compute_attempt_counters(attempts, retry_limit)
    retry_eligible = (not passed) and (attempts < retry_limit)
    return {
        "status": "RESOLVED" if passed else "VERIFIED",
        "verification_passed": passed,
        "retry_eligible": retry_eligible,
        "attempts": attempts + 1 if not passed and retry_eligible else attempts,
        "attribution": attribution_str,
        "last_error": failure_reason,
    }
