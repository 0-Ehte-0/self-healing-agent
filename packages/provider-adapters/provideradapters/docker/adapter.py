import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from app.db.models import (
    Approval,
    AutomationControl,
    Execution,
    PolicyDecision,
    RemediationPlan,
    Resource,
)
from app.db.repositories.control_plane import ConflictError, unit_of_work
from app.services.resources.lock import (
    QuarantinedResourceError,
    ResourceLockedError,
    ResourceLockService,
)
from sharedmodels.enums import ExecutionStatus
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from provideradapters.base import (
    BaseProviderAdapter,
    ContainerNotFoundError,
    ContainerSnapshot,
    DockerDaemonUnreachableError,
    DockerTimeoutError,
    ExecutionIntent,
    ExecutionOutcome,
    ExecutionOutcomeStatus,
    PrecheckFailedError,
    ProviderAdapterError,
    ReconciliationOutcome,
    TargetRecreatedError,
    UncertainOutcomeError,
)
from provideradapters.docker.client import DockerClientWrapper

logger = logging.getLogger(__name__)


class DockerExecutionAdapter(BaseProviderAdapter):
    """Restricted Docker adapter enforcing prechecks, concurrency locks, and execution reconciliation."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        docker_client: DockerClientWrapper | Any = None,
        actor: str = "agent:docker_adapter",
    ):
        self.session_factory = session_factory
        if isinstance(docker_client, DockerClientWrapper):
            self.client = docker_client
        else:
            self.client = DockerClientWrapper(docker_client)
        self.actor = actor
        self._dispatch_locks: dict[str, asyncio.Lock] = {}

    def _get_dispatch_lock(self, container_id: str) -> asyncio.Lock:
        if container_id not in self._dispatch_locks:
            self._dispatch_locks[container_id] = asyncio.Lock()
        return self._dispatch_locks[container_id]

    async def precheck(self, intent: ExecutionIntent) -> dict[str, Any]:
        """Executes strict prechecks prior to dispatch while incident is in APPROVED or PLANNED.

        Raises PrecheckFailedError or TargetRecreatedError on failure.
        """
        # 1. Validate service name allowlist
        if intent.service_name != "demo-api":
            raise PrecheckFailedError(
                f"Service {intent.service_name!r} is not allowlisted for restart. Only 'demo-api' is permitted."
            )

        # 2. Inspect Docker container directly
        try:
            attrs = self.client.inspect_container(intent.container_id)
        except ContainerNotFoundError as e:
            raise TargetRecreatedError(
                f"Target container {intent.container_id} not found in Docker daemon: {e}"
            ) from e
        except Exception as e:
            raise PrecheckFailedError(f"Docker inspection precheck failed: {e}") from e

        config = attrs.get("Config", {})
        labels = config.get("Labels", {})

        # 3. Verify Docker labels
        managed_label = str(labels.get("self-healing.managed", "")).strip().lower()
        if managed_label != "true":
            raise PrecheckFailedError(
                f"Container {intent.container_id} is missing 'self-healing.managed=true' label (found {managed_label!r})"
            )

        service_label = str(labels.get("com.docker.compose.service", "")).strip()
        if service_label != "demo-api":
            raise PrecheckFailedError(
                f"Container {intent.container_id} compose service label is '{service_label}', expected 'demo-api'"
            )

        project_label = str(labels.get("com.docker.compose.project", "")).strip()
        if project_label and project_label != "self-healing-agent":
            raise PrecheckFailedError(
                f"Container {intent.container_id} compose project label is '{project_label}', expected 'self-healing-agent'"
            )

        # 4. Check DB Resource binding & generation
        async with unit_of_work(self.session_factory, actor=self.actor) as repo:
            resource = await repo.session.get(Resource, intent.resource_id)
            if not resource:
                raise PrecheckFailedError(f"Resource {intent.resource_id} not found in database")

            if resource.environment != "local":
                raise PrecheckFailedError(
                    f"Resource environment '{resource.environment}' is not permitted for local Docker mutations"
                )

            res_labels = resource.labels or {}
            db_container_id = res_labels.get("docker_container_id") or res_labels.get(
                "container_id"
            )
            db_generation = res_labels.get("binding_generation", 1)

            if not db_container_id:
                raise PrecheckFailedError("Resource record has no bound Docker container ID")

            if db_container_id != intent.container_id:
                raise TargetRecreatedError(
                    f"Target container was replaced in resource bindings: plan expects {intent.container_id}, DB has {db_container_id}"
                )

            if int(db_generation) != int(intent.binding_generation):
                raise TargetRecreatedError(
                    f"Target binding generation mismatch: plan expects {intent.binding_generation}, DB has {db_generation}"
                )

            # 5. Check latest policy decision & approvals
            latest_decision = await repo.get_latest_policy_decision(
                intent.incident_id, intent.plan_id
            )
            if not latest_decision:
                raise PrecheckFailedError("No persisted policy decision found for plan execution")

            if latest_decision.decision == "DENY":
                raise PrecheckFailedError(
                    f"Current policy decision is DENY: {latest_decision.reason_codes}"
                )
            elif latest_decision.decision == "DEFER":
                raise PrecheckFailedError(
                    f"Current policy decision is DEFER: {latest_decision.reason_codes}"
                )
            elif latest_decision.decision == "REQUIRE_APPROVAL":
                approval = await repo.session.scalar(
                    sa.select(Approval)
                    .where(
                        Approval.plan_id == intent.plan_id,
                        Approval.plan_version == intent.plan_version,
                        Approval.decision == "APPROVE",
                    )
                    .order_by(Approval.created_at.desc())
                    .limit(1)
                )
                if not approval:
                    raise PrecheckFailedError(
                        "Policy requires approval but no approved decision was recorded"
                    )
                now = datetime.now(UTC)
                exp = (
                    approval.expires_at
                    if approval.expires_at.tzinfo
                    else approval.expires_at.replace(tzinfo=UTC)
                )
                if exp <= now:
                    raise PrecheckFailedError(
                        f"Approval grant expired at {approval.expires_at.isoformat()}"
                    )

            # 6. Check automation control & emergency stop
            lock_service = ResourceLockService(repo)
            await lock_service.check_safety(intent.resource_id)

        pre_snapshot = self.client.take_snapshot(intent.container_id)
        return {
            "precheck_passed": True,
            "pre_snapshot": pre_snapshot.model_dump(),
        }

    async def execute(self, intent: ExecutionIntent) -> ExecutionOutcome:
        """Runs the complete mutation lifecycle with concurrency locking and outcome reconciliation."""
        start_time = time.monotonic()
        dispatch_lock = self._get_dispatch_lock(intent.container_id)

        # 0. Early idempotency check against PostgreSQL
        async with unit_of_work(self.session_factory, actor=self.actor) as repo:
            existing = await repo.session.scalar(
                sa.select(Execution).where(Execution.idempotency_key == intent.idempotency_key)
            )
            if existing is not None:
                if (existing.incident_id, existing.step_id, existing.resource_id) != (
                    intent.incident_id,
                    intent.step_id,
                    intent.resource_id,
                ) or (
                    existing.container_id is not None
                    and existing.container_id != intent.container_id
                ):
                    raise ConflictError("Idempotency key already belongs to a different execution")

                if existing.status in {ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED}:
                    logger.info(
                        "Reusing existing execution record %s (status=%s)",
                        existing.id,
                        existing.status,
                    )
                    outcome_status = (
                        ExecutionOutcomeStatus.SUCCEEDED
                        if existing.status == ExecutionStatus.SUCCEEDED
                        else ExecutionOutcomeStatus.FAILED
                    )
                    return ExecutionOutcome(
                        status=outcome_status,
                        execution_id=existing.id,
                        idempotency_key=intent.idempotency_key,
                        post_state=existing.result or {},
                        uncertainty_reason=existing.uncertainty_reason,
                    )
                elif existing.status == ExecutionStatus.RUNNING:
                    logger.info("Reconciling RUNNING execution %s from prior attempt", existing.id)
                    post_snapshot = self.client.take_snapshot(intent.container_id)
                    is_running = post_snapshot.status == "running"
                    outcome_status = (
                        ExecutionOutcomeStatus.SUCCEEDED
                        if is_running
                        else ExecutionOutcomeStatus.UNCERTAIN
                    )
                    uncertainty_reason = (
                        None
                        if is_running
                        else "Container not running after execution crash recovery"
                    )
                    fin_status = ExecutionStatus.SUCCEEDED if is_running else ExecutionStatus.FAILED
                    async with unit_of_work(self.session_factory, actor=self.actor) as repo:
                        await repo.finish_execution(
                            execution_id=existing.id,
                            expected_version=existing.version,
                            status=fin_status,
                            result=post_snapshot.model_dump(),
                            uncertainty_reason=uncertainty_reason,
                        )
                    return ExecutionOutcome(
                        status=outcome_status,
                        execution_id=existing.id,
                        idempotency_key=intent.idempotency_key,
                        post_state=post_snapshot.model_dump(),
                        uncertainty_reason=uncertainty_reason,
                    )

        # Pre-take snapshot
        pre_snapshot = self.client.take_snapshot(intent.container_id)
        intent.pre_state = pre_snapshot.model_dump()

        lock_token: UUID | None = None
        execution_id: UUID | None = None
        execution_version: int = 1

        async with dispatch_lock:
            # 1. Acquire PostgreSQL resource lease
            async with unit_of_work(self.session_factory, actor=self.actor) as repo:
                lock_service = ResourceLockService(repo)
                lock_token = await lock_service.acquire_lock(intent.resource_id, seconds=120)

                # 2. Record intent in PostgreSQL
                exec_record = await repo.record_execution(
                    incident_id=intent.incident_id,
                    plan_id=intent.plan_id,
                    step_id=intent.step_id,
                    resource_id=intent.resource_id,
                    attempt_number=intent.attempt_number,
                    container_id=intent.container_id,
                    binding_generation=intent.binding_generation,
                    lock_token=lock_token,
                    idempotency_key=intent.idempotency_key,
                    pre_state=intent.pre_state,
                )
                execution_id = exec_record.id

                # If already finished, reuse outcome
                if exec_record.status in {ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED}:
                    logger.info(
                        "Reusing existing execution record %s (status=%s)",
                        exec_record.id,
                        exec_record.status,
                    )
                    outcome_status = (
                        ExecutionOutcomeStatus.SUCCEEDED
                        if exec_record.status == ExecutionStatus.SUCCEEDED
                        else ExecutionOutcomeStatus.FAILED
                    )
                    return ExecutionOutcome(
                        status=outcome_status,
                        execution_id=exec_record.id,
                        idempotency_key=intent.idempotency_key,
                        post_state=exec_record.result or {},
                        uncertainty_reason=exec_record.uncertainty_reason,
                    )
                elif exec_record.status == ExecutionStatus.RUNNING:
                    logger.info(
                        "Reconciling RUNNING execution %s from prior attempt", exec_record.id
                    )
                    post_snapshot = self.client.take_snapshot(intent.container_id)
                    is_running = post_snapshot.status == "running"
                    outcome_status = (
                        ExecutionOutcomeStatus.SUCCEEDED
                        if is_running
                        else ExecutionOutcomeStatus.UNCERTAIN
                    )
                    uncertainty_reason = (
                        None
                        if is_running
                        else "Container not running after execution crash recovery"
                    )
                    fin_status = ExecutionStatus.SUCCEEDED if is_running else ExecutionStatus.FAILED
                    await repo.finish_execution(
                        execution_id=exec_record.id,
                        expected_version=exec_record.version,
                        status=fin_status,
                        result=post_snapshot.model_dump(),
                        uncertainty_reason=uncertainty_reason,
                    )
                    return ExecutionOutcome(
                        status=outcome_status,
                        execution_id=exec_record.id,
                        idempotency_key=intent.idempotency_key,
                        post_state=post_snapshot.model_dump(),
                        uncertainty_reason=uncertainty_reason,
                    )

                # Transition Execution to RUNNING
                running_record = await repo.finish_execution(
                    execution_id=execution_id,
                    expected_version=exec_record.version,
                    status=ExecutionStatus.RUNNING,
                    result={"dispatch_intent": intent.model_dump(mode="json")},
                )

                execution_version = running_record.version

            # 3. Apply mutation with bounded Docker timeout
            dispatch_time = datetime.now(UTC)
            uncertain_err: Exception | None = None
            try:
                self.client.restart_container(
                    intent.container_id, timeout_seconds=intent.timeout_seconds
                )
            except (DockerTimeoutError, DockerDaemonUnreachableError) as e:
                logger.warning(
                    "Docker restart experienced timeout/disconnect: %s. Initiating reconciliation.",
                    e,
                )
                uncertain_err = e
            except ContainerNotFoundError as e:
                logger.error("Container disappeared during restart: %s", e)
                async with unit_of_work(self.session_factory, actor=self.actor) as repo:
                    await repo.finish_execution(
                        execution_id=execution_id,
                        expected_version=execution_version,
                        status=ExecutionStatus.FAILED,
                        result={"error": str(e)},
                        uncertainty_reason=str(e),
                    )
                    lock_service = ResourceLockService(repo)
                    if lock_token:
                        await lock_service.release_lock(intent.resource_id, lock_token)
                return ExecutionOutcome(
                    status=ExecutionOutcomeStatus.FAILED,
                    execution_id=execution_id,
                    idempotency_key=intent.idempotency_key,
                    error=str(e),
                )
            except Exception as e:
                logger.error("Definitive Docker restart failure: %s", e)
                uncertain_err = e

            # 4. If an exception occurred during dispatch, attempt reconciliation
            if uncertain_err is not None:
                recon = await self.reconcile(
                    intent, dispatch_time=dispatch_time, pre_snapshot=pre_snapshot
                )
                if recon.reconciled_success:
                    logger.info(
                        "Reconciliation SUCCESS for %s: %s", intent.container_id, recon.reason
                    )
                    post_data = recon.post_snapshot.model_dump() if recon.post_snapshot else {}
                    async with unit_of_work(self.session_factory, actor=self.actor) as repo:
                        await repo.finish_execution(
                            execution_id=execution_id,
                            expected_version=execution_version,
                            status=ExecutionStatus.SUCCEEDED,
                            result={
                                "reconciled": True,
                                "reconciliation_reason": recon.reason,
                                "post_state": post_data,
                            },
                        )
                        lock_service = ResourceLockService(repo)
                        if lock_token:
                            await lock_service.release_lock(intent.resource_id, lock_token)

                    return ExecutionOutcome(
                        status=ExecutionOutcomeStatus.SUCCEEDED,
                        execution_id=execution_id,
                        idempotency_key=intent.idempotency_key,
                        post_state=post_data,
                        reconciled=True,
                        duration_ms=int((time.monotonic() - start_time) * 1000),
                    )
                else:
                    # Reconciliation cannot confirm safe execution: mark UNCERTAIN & quarantine
                    reason = recon.reason or str(uncertain_err)
                    logger.error(
                        "Reconciliation UNCERTAIN for %s: %s. Quarantining resource.",
                        intent.container_id,
                        reason,
                    )
                    async with unit_of_work(self.session_factory, actor=self.actor) as repo:
                        await repo.finish_execution(
                            execution_id=execution_id,
                            expected_version=execution_version,
                            status=ExecutionStatus.FAILED,
                            result={"uncertain": True, "uncertainty_reason": reason},
                            uncertainty_reason=reason,
                        )
                        lock_service = ResourceLockService(repo)
                        await lock_service.quarantine_resource(
                            intent.resource_id,
                            reason=f"Uncertain execution outcome: {reason}",
                        )

                    return ExecutionOutcome(
                        status=ExecutionOutcomeStatus.UNCERTAIN,
                        execution_id=execution_id,
                        idempotency_key=intent.idempotency_key,
                        uncertainty_reason=reason,
                        error=reason,
                        duration_ms=int((time.monotonic() - start_time) * 1000),
                    )

            # 5. Clean dispatch success
            post_snapshot = self.client.take_snapshot(intent.container_id)
            post_data = post_snapshot.model_dump()

            async with unit_of_work(self.session_factory, actor=self.actor) as repo:
                await repo.finish_execution(
                    execution_id=execution_id,
                    expected_version=execution_version,
                    status=ExecutionStatus.SUCCEEDED,
                    result=post_data,
                )
                lock_service = ResourceLockService(repo)
                if lock_token:
                    await lock_service.release_lock(intent.resource_id, lock_token)

            return ExecutionOutcome(
                status=ExecutionOutcomeStatus.SUCCEEDED,
                execution_id=execution_id,
                idempotency_key=intent.idempotency_key,
                post_state=post_data,
                reconciled=False,
                duration_ms=int((time.monotonic() - start_time) * 1000),
            )

    async def reconcile(
        self,
        intent: ExecutionIntent,
        dispatch_time: datetime | None = None,
        pre_snapshot: ContainerSnapshot | None = None,
    ) -> ReconciliationOutcome:
        """Reconciles uncertain execution by inspecting Docker inspect restart counts and timestamps."""
        try:
            post_snapshot = self.client.take_snapshot(intent.container_id)
        except Exception as e:
            return ReconciliationOutcome(
                reconciled_success=False,
                uncertain=True,
                reason=f"Unable to query container for reconciliation: {e}",
            )

        pre_count = pre_snapshot.restart_count if pre_snapshot else 0
        post_count = post_snapshot.restart_count

        # Check if RestartCount incremented
        if post_count > pre_count:
            return ReconciliationOutcome(
                reconciled_success=True,
                uncertain=False,
                reason=f"Container restart verified (RestartCount {pre_count} -> {post_count})",
                post_snapshot=post_snapshot,
            )

        # Check started_at timestamp if dispatch_time provided
        if dispatch_time and post_snapshot.started_at:
            try:
                # Docker started_at format e.g. "2026-09-10T14:05:00.123456789Z"
                started_dt = datetime.fromisoformat(post_snapshot.started_at.replace("Z", "+00:00"))
                if started_dt >= dispatch_time - timedelta(seconds=2):
                    return ReconciliationOutcome(
                        reconciled_success=True,
                        uncertain=False,
                        reason=f"Container restart verified (StartedAt {post_snapshot.started_at} is within dispatch window)",
                        post_snapshot=post_snapshot,
                    )
            except Exception as e:
                logger.debug("Failed parsing started_at for reconciliation: %s", e)

        return ReconciliationOutcome(
            reconciled_success=False,
            uncertain=True,
            reason=f"Could not prove restart: RestartCount did not increment ({pre_count} == {post_count}) and status is '{post_snapshot.status}'",
            post_snapshot=post_snapshot,
        )

    async def dry_run(self, intent: ExecutionIntent) -> ExecutionOutcome:
        """Simulates execution by executing prechecks without mutating physical infrastructure."""
        precheck_res = await self.precheck(intent)
        return ExecutionOutcome(
            status=ExecutionOutcomeStatus.SUCCEEDED,
            idempotency_key=intent.idempotency_key,
            post_state={"dry_run": True, "eligible": True, "precheck": precheck_res},
            duration_ms=10,
        )
