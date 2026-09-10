import logging
from typing import Any
from uuid import UUID

from diagnosis.deterministic.engine import DeterministicDiagnosisEngine
from diagnosis.schemas import ObservationBundle

from agentcore.graph.state import IncidentGraphState
from agentcore.nodes.protocols import DiagnosisEngineProtocol

logger = logging.getLogger(__name__)


async def diagnose_node(
    state: IncidentGraphState,
    engine: DiagnosisEngineProtocol | None = None,
) -> dict[str, Any]:
    """Applies deterministic diagnosis rules to identify root cause.

    Routes to DIAGNOSED if actionable, or DIAGNOSIS_ESCALATED if unsupported/ambiguous.
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
    if obs is None or not isinstance(obs, ObservationBundle):
        obs = ObservationBundle(
            resource_id=resource_id,
            service_name="demo-api",
            container_id=state.get("target_container_id"),
            binding_generation=state.get("binding_generation"),
        )
        if state.get("evidence_ids"):
            obs.evidence_id_map = {
                f"evidence_{i}": eid for i, eid in enumerate(state["evidence_ids"])
            }

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

    if is_actionable:
        return {
            "current_diagnosis_id": diag_id,
            "status": "DIAGNOSED",
            "is_actionable": True,
        }
    else:
        return {
            "current_diagnosis_id": diag_id,
            "status": "DIAGNOSIS_ESCALATED",
            "is_actionable": False,
            "last_error": escalation_reason or "Diagnosis is non-actionable or unsupported",
        }
