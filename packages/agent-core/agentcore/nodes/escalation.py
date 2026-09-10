import logging
from typing import Any
from uuid import UUID

from app.db.repositories.control_plane import unit_of_work
from sharedmodels.enums import IncidentState
from sqlalchemy.ext.asyncio import async_sessionmaker

from agentcore.graph.state import IncidentGraphState

logger = logging.getLogger(__name__)


async def escalate_node(
    state: IncidentGraphState,
    session_factory: async_sessionmaker | None = None,
    actor: str = "worker:agent",
) -> dict[str, Any]:
    """Transitions incident to ESCALATED and records terminal audit state."""
    incident_id_str = state.get("incident_id")
    if not incident_id_str:
        return {"status": "ESCALATED"}

    incident_id = UUID(incident_id_str)
    version = state.get("version", 1)

    if session_factory:
        try:
            async with unit_of_work(session_factory, actor=actor) as repo:
                from app.db.models import Incident

                inc = await repo.session.get(Incident, incident_id)
                if inc and inc.state == IncidentState.ESCALATED:
                    return {"status": "ESCALATED", "version": inc.version}

                updated = await repo.transition(
                    incident_id=incident_id,
                    expected_version=version,
                    target=IncidentState.ESCALATED,
                )
                logger.info(
                    f"Incident {incident_id} successfully escalated at version {updated.version}."
                )
                return {"status": "ESCALATED", "version": updated.version}
        except Exception as exc:
            logger.error(
                f"Failed to persist ESCALATED transition for incident {incident_id}: {exc}"
            )

    return {"status": "ESCALATED"}
