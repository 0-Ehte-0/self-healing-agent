import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from sharedmodels.verification import (
    CheckObservation,
    CheckStatus,
    EvaluationSample,
    RecoveryAttribution,
    VerificationProfile,
    VerificationVerdict,
)

from agentcore.verification.evaluator import TelemetryEvaluator
from agentcore.verification.profiles import DEFAULT_PROFILE, load_verification_profile

logger = logging.getLogger(__name__)


class VerifierProtocol(Protocol):
    async def verify(
        self,
        execution_id: UUID,
        resource_id: UUID,
        **kwargs: Any,
    ) -> dict[str, Any]: ...


class IndependentVerifier(VerifierProtocol):
    """Independently samples telemetry over a 90-second continuous healthy window."""

    def __init__(
        self,
        evaluator: TelemetryEvaluator | None = None,
        clock_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], Any] | None = None,
        fault_status_provider: Callable[[str], Any] | None = None,
    ):
        self.evaluator = evaluator or TelemetryEvaluator()
        self.clock_fn = clock_fn or (lambda: datetime.now(UTC))
        self.sleep_fn = sleep_fn or asyncio.sleep
        self.fault_status_provider = fault_status_provider

    async def verify(
        self,
        execution_id: UUID,
        resource_id: UUID,
        profile: VerificationProfile | None = None,
        profile_name: str | None = None,
        target_container_id: str | None = None,
        binding_generation: int | None = None,
        container_state: dict[str, Any] | None = None,
        container_started_at: datetime | None = None,
        scenario_id: str | None = None,
        injected_at: float | None = None,
        ttl_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Fulfills VerifierProtocol and returns verdict payload."""
        verdict = await self.run_verification(
            execution_id=execution_id,
            resource_id=resource_id,
            profile=profile,
            profile_name=profile_name,
            target_container_id=target_container_id,
            binding_generation=binding_generation,
            container_state=container_state,
            container_started_at=container_started_at,
            scenario_id=scenario_id,
            injected_at=injected_at,
            ttl_seconds=ttl_seconds,
        )
        return verdict.model_dump()

    async def run_verification(
        self,
        execution_id: UUID,
        resource_id: UUID,
        profile: VerificationProfile | None = None,
        profile_name: str | None = None,
        target_container_id: str | None = None,
        binding_generation: int | None = None,
        container_state: dict[str, Any] | None = None,
        container_started_at: datetime | None = None,
        scenario_id: str | None = None,
        injected_at: float | None = None,
        ttl_seconds: float | None = None,
    ) -> VerificationVerdict:
        """Executes the complete independent verification loop."""
        verif_profile = profile or load_verification_profile(profile_name or scenario_id)

        interval_sec = float(verif_profile.evaluation_interval_seconds)
        stabilization_target_sec = float(verif_profile.stabilization_window_seconds)
        min_samples_required = int(verif_profile.min_consecutive_passing_samples)
        max_duration_sec = float(verif_profile.max_verification_duration_seconds)
        req_readiness_count = int(verif_profile.readiness.get("required_consecutive_successes", 3))

        window_start = self.clock_fn()
        samples: list[EvaluationSample] = []

        warm_up_started_at = window_start
        warm_up_duration_seconds: float | None = None
        readiness_consecutive_successes = 0
        prerequisite_satisfied = False

        elapsed_healthy_seconds = 0.0
        consecutive_healthy_evaluations = 0
        stabilization_resets = 0
        sample_index = 0

        logger.info(
            f"Starting independent verification [profile={verif_profile.profile_id}, target={stabilization_target_sec}s stabilization, execution={execution_id}]"
        )

        while True:
            current_time = self.clock_fn()
            elapsed_total = (current_time - window_start).total_seconds()

            # Check overall timeout limit
            if elapsed_total >= max_duration_sec:
                logger.warning(
                    f"Verification session reached max duration {max_duration_sec}s without sustained recovery."
                )
                break

            # Evaluate current sample
            sample = await self.evaluator.evaluate_sample(
                profile=verif_profile,
                sample_index=sample_index,
                elapsed_healthy_seconds=elapsed_healthy_seconds,
                readiness_consecutive_successes=readiness_consecutive_successes,
                target_container_id=target_container_id,
                binding_generation=binding_generation,
                container_state=container_state,
                container_started_at=container_started_at,
            )
            samples.append(sample)
            readiness_consecutive_successes = sample.readiness_consecutive_successes

            # Phase 1: Readiness Prerequisite Warm-up (3 consecutive HTTP 200s < 200ms)
            if not prerequisite_satisfied:
                if readiness_consecutive_successes >= req_readiness_count:
                    prerequisite_satisfied = True
                    warm_up_duration_seconds = (
                        self.clock_fn() - warm_up_started_at
                    ).total_seconds()
                    logger.info(
                        f"Readiness prerequisite satisfied after {warm_up_duration_seconds:.2f}s warm-up ({req_readiness_count} consecutive successes)."
                    )
                else:
                    # Still in warm-up phase; stabilization timer has not begun
                    elapsed_healthy_seconds = 0.0
                    consecutive_healthy_evaluations = 0

            # Phase 2: Stabilization Observation (only once prerequisite met)
            if prerequisite_satisfied:
                if sample.all_passed:
                    elapsed_healthy_seconds += interval_sec
                    consecutive_healthy_evaluations += 1

                    # Check if healthy stabilization threshold reached
                    if (
                        elapsed_healthy_seconds >= stabilization_target_sec
                        and consecutive_healthy_evaluations >= min_samples_required
                    ):
                        logger.info(
                            f"Verification stabilization achieved: {elapsed_healthy_seconds:.1f}s continuous health across {consecutive_healthy_evaluations} samples."
                        )
                        break
                else:
                    # Any check failure or UNKNOWN resets the healthy stabilization window
                    if elapsed_healthy_seconds > 0.0 or consecutive_healthy_evaluations > 0:
                        stabilization_resets += 1
                        logger.info(
                            f"Stabilization timer reset #{stabilization_resets} after {elapsed_healthy_seconds:.1f}s due to check failure: {[k for k, c in sample.checks.items() if c.status != CheckStatus.PASS]}"
                        )
                    elapsed_healthy_seconds = 0.0
                    consecutive_healthy_evaluations = 0

            # Wait before next evaluation
            sample_index += 1
            await self.sleep_fn(interval_sec)

        window_end = self.clock_fn()
        passed = (
            elapsed_healthy_seconds >= stabilization_target_sec
            and consecutive_healthy_evaluations >= min_samples_required
        )

        # Check Fault Expiry & Recovery Attribution (Section 11)
        attribution = RecoveryAttribution.AGENT_HEALED
        failure_reason = None
        status = "RESOLVED" if passed else "VERIFICATION_FAILED"

        external_clearance = await self._check_external_clearance(
            scenario_id=scenario_id,
            injected_at=injected_at,
            ttl_seconds=ttl_seconds,
            window_end=window_end,
        )
        if external_clearance:
            passed = False
            attribution = RecoveryAttribution.EXTERNALLY_RECOVERED
            status = "EXTERNALLY_RECOVERED"
            failure_reason = (
                f"Fault expired or cleared externally before verification completed ({external_clearance}). "
                "Recovery cannot be attributed to agent mutation."
            )
            logger.warning(failure_reason)
        elif not passed:
            failure_reason = (
                f"Verification timed out or failed to maintain {stabilization_target_sec}s continuous health. "
                f"Achieved {elapsed_healthy_seconds:.1f}s ({consecutive_healthy_evaluations}/{min_samples_required} samples)."
            )

        # Compute average / final health score
        final_health_score = samples[-1].health_score if samples else 0.0

        return VerificationVerdict(
            passed=passed,
            status=status,
            attribution=attribution,
            profile_id=verif_profile.profile_id,
            profile_version=verif_profile.version,
            window_start=window_start,
            window_end=window_end,
            total_evaluations=len(samples),
            consecutive_healthy_evaluations=consecutive_healthy_evaluations,
            elapsed_healthy_seconds=elapsed_healthy_seconds,
            warm_up_duration_seconds=warm_up_duration_seconds,
            stabilization_resets=stabilization_resets,
            health_score=final_health_score,
            checks=samples[-1].checks if samples else {},
            samples=samples,
            failure_reason=failure_reason,
        )

    async def _check_external_clearance(
        self,
        scenario_id: str | None,
        injected_at: float | None,
        ttl_seconds: float | None,
        window_end: datetime,
    ) -> str | None:
        """Verifies whether the fault expired or was cleared externally prior to verification completion."""
        # 1. Check TTL expiry timestamp if provided
        if injected_at is not None and ttl_seconds is not None:
            expires_at = injected_at + ttl_seconds
            if window_end.timestamp() >= expires_at:
                return f"Injected fault TTL expired at {datetime.fromtimestamp(expires_at, tz=UTC).isoformat()}"

        # 2. Check dynamic fault status provider if configured
        if self.fault_status_provider and scenario_id:
            try:
                res = await self.fault_status_provider(scenario_id)
                if isinstance(res, dict):
                    rec = res.get("record", {})
                    st = res.get("status") or (rec.get("status") if isinstance(rec, dict) else None)
                    if st in {"expired", "cleared"}:
                        cleared_at = rec.get("cleared_at") if isinstance(rec, dict) else None
                        return f"Fault status is '{st}' (cleared_at={cleared_at})"
            except Exception as exc:
                logger.warning(f"Error checking fault status provider: {exc}")

        return None
