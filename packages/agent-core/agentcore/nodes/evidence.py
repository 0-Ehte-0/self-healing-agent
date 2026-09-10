import logging
from typing import Any
from uuid import UUID

from diagnosis.evidence.collector import EvidenceCollector

from agentcore.graph.state import IncidentGraphState
from agentcore.nodes.protocols import EvidenceCollectorProtocol

logger = logging.getLogger(__name__)


async def collect_evidence_node(
    state: IncidentGraphState,
    collector: EvidenceCollectorProtocol | None = None,
) -> dict[str, Any]:
    """Collects bounded, redacted evidence from Prometheus, Loki, and container inspection.

    Skips collection if evidence or diagnosis has already been committed in PostgreSQL.
    """
    if state.get("current_diagnosis_id") or state.get("evidence_ids"):
        logger.info(
            "Evidence or diagnosis already exists for incident %s; skipping re-collection on resume.",
            state.get("incident_id"),
        )
        return {"status": "EVIDENCE_COLLECTED"}

    if collector is None:
        collector = EvidenceCollector()

    incident_id = UUID(state["incident_id"])
    resource_id = UUID(state["resource_id"])

    res = await collector.collect(
        incident_id=incident_id,
        resource_id=resource_id,
        target_service="demo-api",
    )

    evidence_ids: list[str] = []
    if hasattr(res, "items"):
        evidence_ids = [str(item.id) for item in res.items]
    elif isinstance(res, dict) and "items" in res:
        evidence_ids = [str(item.get("id")) for item in res["items"]]
    elif isinstance(res, dict) and "evidence_ids" in res:
        evidence_ids = res["evidence_ids"]

    return {"status": "EVIDENCE_COLLECTED", "evidence_ids": evidence_ids}
