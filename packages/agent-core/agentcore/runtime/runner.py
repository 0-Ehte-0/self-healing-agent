import logging
from typing import Any
from uuid import UUID

from app.db.models import Incident
from app.db.repositories.control_plane import ConflictError, unit_of_work
from sharedmodels.enums import IncidentState
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentcore.graph.builder import build_incident_workflow
from agentcore.graph.state import IncidentGraphState
from agentcore.runtime.checkpointer import PostgresCheckpointSaver
from agentcore.runtime.lease import IncidentLeaseManager
from agentcore.runtime.policy import NodeRetryPolicy
from agentcore.runtime.scheduler import WorkflowScheduler

logger = logging.getLogger(__name__)


class WorkflowRunner:
    """Resumable LangGraph runner coordinating incident leases, checkpoints, and DB reconciliation."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        worker_id: str = "worker:agent",
        checkpointer: PostgresCheckpointSaver | None = None,
        scheduler: WorkflowScheduler | None = None,
        lease_manager: IncidentLeaseManager | None = None,
        retry_policy: NodeRetryPolicy | None = None,
        node_overrides: dict[str, Any] | None = None,
    ):
        self.session_factory = session_factory
        self.worker_id = worker_id
        self.checkpointer = checkpointer or PostgresCheckpointSaver(session_factory)
        self.scheduler = scheduler or WorkflowScheduler(session_factory, actor=worker_id)
        self.lease_manager = lease_manager or IncidentLeaseManager(session_factory, worker_id)
        self.retry_policy = retry_policy or NodeRetryPolicy()
        self.node_overrides = node_overrides or {}
        if "plan" not in self.node_overrides:
            from functools import partial

            from agentcore.nodes.planning import plan_node

            self.node_overrides["plan"] = partial(
                plan_node, session_factory=self.session_factory, actor=self.worker_id
            )
        if "execute" not in self.node_overrides:
            from functools import partial

            from agentcore.nodes.execution import execute_node

            self.node_overrides["execute"] = partial(
                execute_node, session_factory=self.session_factory, actor=self.worker_id
            )
        self.workflow = build_incident_workflow(
            checkpointer=self.checkpointer,
            node_overrides=self.node_overrides,
            retry_policy=self.retry_policy,
        )

    async def run_incident(
        self,
        incident_id: UUID,
        expected_version: int | None = None,
        resume_metadata: dict[str, Any] | None = None,
    ) -> IncidentGraphState | None:
        """Executes or resumes an incident workflow under an exclusive lease."""
        # 1. Inspect DB first to verify incident and deduplicate stale versions (Step 6)
        async with unit_of_work(self.session_factory, actor=self.worker_id) as repo:
            incident = await repo.session.get(Incident, incident_id)
            if incident is None:
                raise LookupError(f"Incident {incident_id} not found in database")

            if incident.state in {
                IncidentState.RESOLVED,
                IncidentState.ESCALATED,
                IncidentState.ROLLEDBACK,
            }:
                logger.info(
                    f"Incident {incident_id} is already in terminal state {incident.state}. Skipping."
                )
                return None

            if expected_version is not None and incident.version > expected_version:
                logger.info(
                    f"Incident {incident_id} already at version {incident.version} > expected {expected_version}. Skipping duplicate."
                )
                return None

            # 2. Reconcile against committed PostgreSQL records (Step 9)
            reconstruction = await repo.reconstruct(incident_id)
            current_version = incident.version
            correlation_key = incident.correlation_key
            resource_id_str = str(incident.resource_id)
            attempts = incident.attempts
            retry_limit = incident.retry_limit
            approval_req = incident.approval_required

            latest_diag = (
                str(reconstruction["diagnoses"][-1].id) if reconstruction["diagnoses"] else None
            )
            latest_plan = str(reconstruction["plans"][-1].id) if reconstruction["plans"] else None
            is_approved = incident.state == IncidentState.APPROVED or any(
                a.decision == "APPROVE" for a in reconstruction["approvals"]
            )

        # 3. Acquire exclusive processing lease (Step 7)
        async with self.lease_manager.hold(
            incident_id, expected_version=current_version
        ) as lease_token:
            # 4. Prepare initial / resumed LangGraph state
            initial_state: IncidentGraphState = {
                "incident_id": str(incident_id),
                "version": current_version,
                "correlation_key": correlation_key,
                "resource_id": resource_id_str,
                "current_diagnosis_id": latest_diag,
                "current_plan_id": latest_plan,
                "evidence_ids": [str(e.id) for e in reconstruction["evidence"]]
                if reconstruction["evidence"]
                else None,
                "attempts": attempts,
                "retry_limit": retry_limit,
                "approval_required": approval_req,
                "is_approved": is_approved,
                "wait_reason": None,
                "wait_until": None,
                "status": "RUNNING",
            }

            if resume_metadata:
                for k, v in resume_metadata.items():
                    initial_state[k] = v  # type: ignore[literal-required]

            # 5. Verify lease is held before running graph
            if not await self.lease_manager.verify(incident_id, lease_token, current_version):
                raise ConflictError("Lease ownership lost before graph execution")

            config = {
                "configurable": {
                    "thread_id": str(incident_id),
                    "checkpoint_ns": "",
                }
            }

            # 6. Run workflow asynchronously
            result_state = await self.workflow.ainvoke(initial_state, config=config)

            # 7. Check if graph paused on approval or cooldown (Step 8)
            if result_state.get("wait_reason") == "AWAITING_APPROVAL":
                async with unit_of_work(self.session_factory, actor=self.worker_id) as repo:
                    inc_now = await repo.session.get(Incident, incident_id)
                    if inc_now and inc_now.state == IncidentState.PLANNED:
                        inc_now = await repo.transition(
                            incident_id, inc_now.version, IncidentState.PENDING_APPROVAL
                        )
                        current_version = inc_now.version
                        result_state["version"] = inc_now.version

                await self.scheduler.schedule_wakeup(
                    incident_id=incident_id,
                    version=result_state.get("version", current_version),
                    delay_seconds=1800,  # 30 min approval timeout window
                    wait_reason="AWAITING_APPROVAL",
                    resume_metadata={"is_resumed_from_approval": True},
                )
                logger.info(f"Incident {incident_id} paused awaiting approval. Lease released.")

            return result_state
