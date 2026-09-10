import logging
from typing import Any
from uuid import UUID

from app.db.models import Incident, Resource
from app.db.repositories.control_plane import unit_of_work
from diagnosis.evidence.collector import EvidenceCollector
from sharedmodels.enums import IncidentState
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentcore.graph.state import IncidentGraphState
from agentcore.nodes.protocols import EvidenceCollectorProtocol

logger = logging.getLogger(__name__)


async def collect_evidence_node(
    state: IncidentGraphState,
    collector: EvidenceCollectorProtocol | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    actor: str = "worker:agent",
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

    cid = state.get("target_container_id") or state.get("container_id")
    gen = state.get("binding_generation", 1)
    service_name = state.get("service_name") or "demo-api"
    cur_v = state.get("version", 1)

    # 1. Discover and sync container identity if DB session is available
    if session_factory is not None:
        async with unit_of_work(session_factory, actor=actor) as repo:
            resource = await repo.session.get(Resource, resource_id)
            if resource:
                labels = resource.labels or {}
                service_name = labels.get("compose_service") or resource.name or "demo-api"
                cid = labels.get("docker_container_id") or labels.get("container_id")
                gen = labels.get("binding_generation", 1)

                if not cid:
                    try:
                        from app.services.discovery.docker import DockerResourceDiscovery

                        discovery = DockerResourceDiscovery()
                        synced = await discovery.sync_resource_binding(
                            repo, service_name=service_name
                        )
                        if synced and synced.labels:
                            cid = synced.labels.get("docker_container_id") or synced.labels.get(
                                "container_id"
                            )
                            gen = synced.labels.get("binding_generation", 1)
                    except Exception as e:
                        logger.debug("Docker discovery skipped or failed: %s", e)

    # 2. Collect evidence bundle
    res = await collector.collect(
        incident_id=incident_id,
        resource_id=resource_id,
        target_service=service_name,
        target_container_id=cid,
        binding_generation=int(gen) if gen else 1,
        actor=actor,
    )

    evidence_ids: list[str] = []
    if hasattr(res, "items"):
        evidence_ids = [str(item.id) for item in res.items]
    elif isinstance(res, dict) and "items" in res:
        evidence_ids = [str(item.get("id")) for item in res["items"]]
    elif isinstance(res, dict) and "evidence_ids" in res:
        evidence_ids = res["evidence_ids"]

    # 3. Persist evidence items to database and advance DETECTED -> TRIAGED
    if session_factory is not None:
        async with unit_of_work(session_factory, actor=actor) as repo:
            inc = await repo.session.get(Incident, incident_id)
            if inc and inc.state == IncidentState.DETECTED:
                inc = await repo.transition(incident_id, inc.version, IncidentState.TRIAGED)
                cur_v = inc.version

            if hasattr(res, "items"):
                for item in res.items:
                    try:
                        await repo.record_evidence(
                            incident_id=incident_id,
                            kind=item.kind,
                            source=item.source,
                            content=item.content,
                            observed_at=item.observed_at,
                            unit=item.unit,
                            binding_generation=item.binding_generation,
                            evidence_id=item.id,
                        )
                    except Exception as e:
                        logger.debug("Evidence record already recorded or skipped: %s", e)

    # 4. Generate ObservationBundle for downstream diagnosis rules
    obs_bundle = None
    if hasattr(collector, "to_observation_bundle") and hasattr(res, "items"):
        try:
            obs_bundle = collector.to_observation_bundle(
                res,
                service_name=service_name,
                container_id=cid,
                binding_generation=int(gen) if gen else 1,
            )
        except Exception as e:
            logger.debug("to_observation_bundle conversion error: %s", e)

    return {
        "status": "EVIDENCE_COLLECTED",
        "version": cur_v,
        "evidence_ids": evidence_ids,
        "target_container_id": cid,
        "container_id": cid,
        "binding_generation": int(gen) if gen else 1,
        "service_name": service_name,
        "observation_bundle": obs_bundle,
    }
