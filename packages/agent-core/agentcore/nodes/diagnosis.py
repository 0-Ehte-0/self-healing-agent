import logging
from typing import Any
from uuid import UUID, uuid4

from app.db.models import Diagnosis, Incident
from app.db.repositories.control_plane import unit_of_work
from diagnosis.deterministic.engine import DeterministicDiagnosisEngine
from diagnosis.schemas import ObservationBundle
from sharedmodels.enums import IncidentState
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentcore.graph.state import IncidentGraphState
from agentcore.nodes.protocols import DiagnosisEngineProtocol

logger = logging.getLogger(__name__)

CAUSE_TO_SCENARIO = {
    "CONTAINER_STOPPED": "SCN-001",
    "CPU_SATURATION": "SCN-002",
    "API_UNRESPONSIVE": "SCN-003",
}


async def diagnose_node(
    state: IncidentGraphState,
    engine: DiagnosisEngineProtocol | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    actor: str = "worker:agent",
) -> dict[str, Any]:
    """Applies deterministic diagnosis rules to identify root cause.

    Routes to DIAGNOSED if actionable, or DIAGNOSIS_ESCALATED if unsupported/ambiguous.
    Persists diagnosis to database and executes legal state transitions when session_factory is present.
    """
    if state.get("current_diagnosis_id"):
        logger.info(
            "Diagnosis %s already committed in database for incident %s; skipping re-diagnosis on resume.",
            state.get("current_diagnosis_id"),
            state.get("incident_id"),
        )
        return {
            "current_diagnosis_id": state["current_diagnosis_id"],
            "status": "DIAGNOSED",
            "is_actionable": True,
        }

    if engine is None:
        engine = DeterministicDiagnosisEngine()

    incident_id = UUID(state["incident_id"])
    resource_id = UUID(state["resource_id"])

    # Prepare observation bundle
    obs = state.get("observation_bundle")
    cid = state.get("target_container_id") or state.get("container_id")
    gen = state.get("binding_generation", 1)
    svc = state.get("service_name") or "demo-api"

    if obs is None:
        obs = ObservationBundle(
            resource_id=resource_id,
            service_name=svc,
            container_id=cid,
            binding_generation=gen,
        )
        if state.get("evidence_ids"):
            obs.evidence_id_map = {
                f"evidence_{i}": eid for i, eid in enumerate(state["evidence_ids"])
            }
    elif isinstance(obs, dict):
        obs = ObservationBundle.model_validate(obs)

    parent_id = UUID(state["parent_diagnosis_id"]) if state.get("parent_diagnosis_id") else None

    diag = await engine.diagnose(
        incident_id=incident_id,
        obs=obs,
        parent_diagnosis_id=parent_id,
    )

    diag_id = str(diag.id) if hasattr(diag, "id") else str(diag.get("id"))
    is_actionable = (
        getattr(diag, "is_actionable", False)
        if hasattr(diag, "is_actionable")
        else diag.get("is_actionable", False)
    )
    escalation_reason = (
        getattr(diag, "escalation_reason", None)
        if hasattr(diag, "escalation_reason")
        else diag.get("escalation_reason")
    )

    root_cause_val = getattr(diag, "root_cause", None) or diag.get("root_cause")
    root_cause_str = (
        root_cause_val.value if hasattr(root_cause_val, "value") else str(root_cause_val)
    )
    scenario_id = CAUSE_TO_SCENARIO.get(root_cause_str, "SCN-001")
    confidence_val = float(getattr(diag, "confidence", 0.95))

    cur_v = state.get("version", 1)

    # Persist diagnosis and execute legal state transitions
    if session_factory is not None:
        async with unit_of_work(session_factory, actor=actor) as repo:
            inc = await repo.session.get(Incident, incident_id)

            # Collect evidence UUIDs
            raw_evidence_ids = (
                diag.evidence_ids
                if hasattr(diag, "evidence_ids")
                else (state.get("evidence_ids") or [])
            )
            evidence_uuids = [UUID(str(e)) for e in raw_evidence_ids if isinstance(e, (str, UUID))]

            diag_record = Diagnosis(
                id=UUID(diag_id) if diag_id else uuid4(),
                incident_id=incident_id,
                root_cause=root_cause_str,
                confidence=confidence_val,
                evidence_ids=[str(e) for e in evidence_uuids],
                rule_id=getattr(diag, "rule_id", "DET-001"),
                rule_version=getattr(diag, "rule_version", "1.0"),
                contradictory_findings=getattr(diag, "contradictory_findings", []),
                escalation_reason=escalation_reason,
                parent_diagnosis_id=parent_id,
                reasoning={
                    "rule_id": getattr(diag, "rule_id", "DET-001"),
                    "rule_version": getattr(diag, "rule_version", "1.0"),
                    "is_actionable": is_actionable,
                    "escalation_reason": escalation_reason,
                },
                actor=actor,
            )
            await repo.add(diag_record)
            diag_id = str(diag_record.id)

            if is_actionable:
                if inc and inc.state in {IncidentState.DETECTED, IncidentState.TRIAGED}:
                    if inc.state == IncidentState.DETECTED:
                        inc = await repo.transition(incident_id, inc.version, IncidentState.TRIAGED)
                    inc = await repo.transition(incident_id, inc.version, IncidentState.DIAGNOSED)
                    cur_v = inc.version
            else:
                if inc and inc.state in {IncidentState.DETECTED, IncidentState.TRIAGED}:
                    if inc.state == IncidentState.DETECTED:
                        inc = await repo.transition(incident_id, inc.version, IncidentState.TRIAGED)
                    inc = await repo.transition(incident_id, inc.version, IncidentState.ESCALATED)
                    cur_v = inc.version
                await repo.create_escalation_record(
                    incident_id=incident_id,
                    title=f"Diagnosis Escalation: {root_cause_str}",
                    summary=escalation_reason or f"Non-actionable diagnosis: {root_cause_str}",
                    root_cause=root_cause_str,
                    escalation_reason=escalation_reason,
                    ticket_reference=f"DIAG-ESC-{incident_id.hex[:8].upper()}",
                )

    if is_actionable:
        return {
            "current_diagnosis_id": diag_id,
            "status": "DIAGNOSED",
            "is_actionable": True,
            "root_cause": root_cause_str,
            "scenario_id": scenario_id,
            "confidence": confidence_val,
            "version": cur_v,
        }
    else:
        return {
            "current_diagnosis_id": diag_id,
            "status": "DIAGNOSIS_ESCALATED",
            "is_actionable": False,
            "root_cause": root_cause_str,
            "scenario_id": scenario_id,
            "confidence": confidence_val,
            "version": cur_v,
            "last_error": escalation_reason or "Diagnosis is non-actionable or unsupported",
            "escalation_reason": escalation_reason,
        }
