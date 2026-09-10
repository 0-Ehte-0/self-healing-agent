import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from sharedmodels.verification import (
    CheckObservation,
    CheckStatus,
    EvaluationSample,
    VerificationProfile,
)
from telemetryclient.prometheus import PrometheusClient

logger = logging.getLogger(__name__)


class TelemetryEvaluator:
    """Evaluates individual telemetry samples against a scenario verification profile."""

    def __init__(
        self,
        prometheus_client: PrometheusClient | None = None,
        readiness_client: httpx.AsyncClient | None = None,
        demo_api_base_url: str = "http://localhost:8000",
    ):
        self.prometheus_client = prometheus_client or PrometheusClient()
        self.readiness_client = readiness_client
        self.demo_api_base_url = demo_api_base_url.rstrip("/")

    async def evaluate_sample(
        self,
        profile: VerificationProfile,
        sample_index: int,
        elapsed_healthy_seconds: float,
        readiness_consecutive_successes: int,
        target_container_id: str | None = None,
        binding_generation: int | None = None,
        container_state: dict[str, Any] | None = None,
        container_started_at: datetime | None = None,
    ) -> EvaluationSample:
        """Executes one evaluation sample cycle across all required checks."""
        now = datetime.now(UTC)
        checks: dict[str, CheckObservation] = {}

        # 1. Target Container State & Identity Check
        target_check = self._evaluate_target_state(
            target_container_id=target_container_id,
            binding_generation=binding_generation,
            container_state=container_state,
            now=now,
        )
        checks["target_identity"] = target_check

        # 2. HTTP Readiness Check
        readiness_check = await self._evaluate_readiness(profile, now)
        checks["readiness"] = readiness_check

        # Update readiness consecutive success count
        if readiness_check.status == CheckStatus.PASS:
            readiness_consecutive_successes += 1
        else:
            readiness_consecutive_successes = 0

        # 3. Counter Reset & Post-Restart Warm-up Check
        counter_ready, counter_reason = self._check_counter_stability(
            container_started_at=container_started_at,
            now=now,
        )

        # 4. CPU Saturation Check
        cpu_check = await self._evaluate_cpu(profile, counter_ready, counter_reason, now)
        checks["cpu_saturation"] = cpu_check

        # 5. Business Traffic Error Rate & Volume Check
        traffic_check = await self._evaluate_traffic_error_rate(
            profile, counter_ready, counter_reason, now
        )
        checks["business_traffic"] = traffic_check

        # 6. Business Route Latency Check
        latency_check = await self._evaluate_latency(profile, counter_ready, counter_reason, now)
        checks["business_latency"] = latency_check

        # 7. Active Alerts Check
        alerts_check = await self._evaluate_alerts(profile, now)
        checks["alerts"] = alerts_check

        # Compute overall sample outcome and health score
        all_passed = all(c.status == CheckStatus.PASS for c in checks.values())
        health_score = self._compute_health_score(profile, checks)

        return EvaluationSample(
            timestamp=now,
            sample_index=sample_index,
            elapsed_healthy_seconds=elapsed_healthy_seconds,
            readiness_consecutive_successes=readiness_consecutive_successes,
            all_passed=all_passed,
            health_score=health_score,
            checks=checks,
        )

    def _evaluate_target_state(
        self,
        target_container_id: str | None,
        binding_generation: int | None,
        container_state: dict[str, Any] | None,
        now: datetime,
    ) -> CheckObservation:
        if not container_state:
            return CheckObservation(
                name="target_identity",
                observed_at=now,
                value=target_container_id or "unbound",
                threshold=target_container_id,
                status=CheckStatus.UNKNOWN,
                reason="No live container state observation available",
            )

        cid = container_state.get("container_id", "")
        gen = container_state.get("binding_generation", 1)
        status = container_state.get("status", "unknown")

        if target_container_id and cid != target_container_id:
            return CheckObservation(
                name="target_identity",
                observed_at=now,
                value=cid,
                threshold=target_container_id,
                status=CheckStatus.FAIL,
                reason=f"Container ID mismatch: observed {cid[:12]}, expected {target_container_id[:12]}",
            )

        if binding_generation and gen != binding_generation:
            return CheckObservation(
                name="target_identity",
                observed_at=now,
                value=gen,
                threshold=binding_generation,
                status=CheckStatus.FAIL,
                reason=f"Binding generation mismatch: observed {gen}, expected {binding_generation}",
            )

        if status.lower() not in {"running", "up"}:
            return CheckObservation(
                name="target_identity",
                observed_at=now,
                value=status,
                threshold="running",
                status=CheckStatus.FAIL,
                reason=f"Container is not running (status: {status})",
            )

        return CheckObservation(
            name="target_identity",
            observed_at=now,
            value=f"{status} (id={cid[:12] if cid else 'ok'})",
            threshold="running",
            status=CheckStatus.PASS,
            reason="Target container is running and binding matches",
        )

    async def _evaluate_readiness(
        self, profile: VerificationProfile, now: datetime
    ) -> CheckObservation:
        readiness_cfg = profile.readiness
        path = readiness_cfg.get("path", "/health/ready")
        url = f"{self.demo_api_base_url}{path}"
        max_lat = float(readiness_cfg.get("max_latency_seconds", 0.200))
        expected_status = int(readiness_cfg.get("expected_status", 200))

        client = self.readiness_client
        should_close = False
        if client is None:
            client = httpx.AsyncClient(timeout=max_lat + 2.0)
            should_close = True

        start_time = datetime.now(UTC)
        try:
            resp = await client.get(url)
            elapsed_sec = (datetime.now(UTC) - start_time).total_seconds()

            if resp.status_code != expected_status:
                return CheckObservation(
                    name="readiness",
                    observed_at=now,
                    value=resp.status_code,
                    threshold=expected_status,
                    status=CheckStatus.FAIL,
                    reason=f"Readiness probe returned HTTP {resp.status_code} (expected {expected_status})",
                )

            if elapsed_sec > max_lat:
                return CheckObservation(
                    name="readiness",
                    observed_at=now,
                    value=round(elapsed_sec, 4),
                    unit="seconds",
                    threshold=max_lat,
                    status=CheckStatus.FAIL,
                    reason=f"Readiness latency {elapsed_sec:.3f}s exceeded {max_lat}s threshold",
                )

            return CheckObservation(
                name="readiness",
                observed_at=now,
                value=round(elapsed_sec, 4),
                unit="seconds",
                threshold=max_lat,
                status=CheckStatus.PASS,
                reason=f"HTTP 200 within {elapsed_sec:.3f}s",
            )
        except Exception as exc:
            return CheckObservation(
                name="readiness",
                observed_at=now,
                status=CheckStatus.FAIL,
                reason=f"Readiness probe failed: {exc}",
            )
        finally:
            if should_close:
                await client.aclose()

    def _check_counter_stability(
        self,
        container_started_at: datetime | None,
        now: datetime,
    ) -> tuple[bool, str]:
        """Checks if post-restart scrape samples have stabilized (at least 15s post restart)."""
        if container_started_at is not None:
            elapsed_since_start = (now - container_started_at).total_seconds()
            if elapsed_since_start < 15.0:
                return (
                    False,
                    f"Waiting for post-restart counter stabilization ({elapsed_since_start:.1f}s / 15s elapsed)",
                )

        return True, "Counter stabilized"

    async def _evaluate_cpu(
        self,
        profile: VerificationProfile,
        counter_ready: bool,
        counter_reason: str,
        now: datetime,
    ) -> CheckObservation:
        if not counter_ready:
            return CheckObservation(
                name="cpu_saturation",
                observed_at=now,
                status=CheckStatus.UNKNOWN,
                reason=counter_reason,
            )

        cpu_cfg = profile.cpu
        max_sat = float(cpu_cfg.get("max_saturation", 0.75))
        cores = float(cpu_cfg.get("cpu_budget_cores", 1.0))
        query = f'rate(demo_api_cpu_seconds_total{{job="demo-api"}}[1m])'

        try:
            res = await self.prometheus_client.query_instant(query)
            if res.status != "success" or not res.samples or res.samples[0].latest_value is None:
                return CheckObservation(
                    name="cpu_saturation",
                    observed_at=now,
                    status=CheckStatus.UNKNOWN,
                    reason=f"CPU rate metric unavailable or empty ({res.status})",
                )

            # Check freshness
            if (
                res.freshness_seconds
                and res.freshness_seconds > profile.sample_freshness_limit_seconds
            ):
                return CheckObservation(
                    name="cpu_saturation",
                    observed_at=now,
                    freshness_seconds=res.freshness_seconds,
                    status=CheckStatus.UNKNOWN,
                    reason=f"Stale CPU metric sample ({res.freshness_seconds:.1f}s > {profile.sample_freshness_limit_seconds}s)",
                )

            raw_rate = res.samples[0].latest_value
            saturation = raw_rate / cores

            if saturation > max_sat:
                return CheckObservation(
                    name="cpu_saturation",
                    observed_at=now,
                    value=round(saturation, 4),
                    unit="ratio",
                    threshold=max_sat,
                    freshness_seconds=res.freshness_seconds,
                    status=CheckStatus.FAIL,
                    reason=f"CPU saturation {saturation:.2%} exceeds {max_sat:.2%} threshold",
                )

            return CheckObservation(
                name="cpu_saturation",
                observed_at=now,
                value=round(saturation, 4),
                unit="ratio",
                threshold=max_sat,
                freshness_seconds=res.freshness_seconds,
                status=CheckStatus.PASS,
                reason=f"CPU saturation {saturation:.2%} within {max_sat:.2%} threshold",
            )
        except Exception as exc:
            return CheckObservation(
                name="cpu_saturation",
                observed_at=now,
                status=CheckStatus.UNKNOWN,
                reason=f"Prometheus CPU query error: {exc}",
            )

    async def _evaluate_traffic_error_rate(
        self,
        profile: VerificationProfile,
        counter_ready: bool,
        counter_reason: str,
        now: datetime,
    ) -> CheckObservation:
        if not counter_ready:
            return CheckObservation(
                name="business_traffic",
                observed_at=now,
                status=CheckStatus.UNKNOWN,
                reason=counter_reason,
            )

        traffic_cfg = profile.traffic
        min_requests = int(traffic_cfg.get("min_requests_per_window", 20))
        max_error_rate = traffic_cfg.get("max_error_rate")
        baseline_success_min = traffic_cfg.get("baseline_success_rate_min")
        max_5xx_rate = traffic_cfg.get("max_5xx_error_rate")

        # Query total business requests rate
        total_query = 'sum(rate(demo_api_http_requests_total{job="demo-api",route="/jobs"}[1m]))'
        error_query = 'sum(rate(demo_api_http_requests_total{job="demo-api",route="/jobs",status=~"5.."}[1m]))'

        try:
            total_res = await self.prometheus_client.query_instant(total_query)
            if (
                total_res.status != "success"
                or not total_res.samples
                or total_res.samples[0].latest_value is None
            ):
                return CheckObservation(
                    name="business_traffic",
                    observed_at=now,
                    status=CheckStatus.UNKNOWN,
                    reason="Total business traffic metric unavailable",
                )

            # Check freshness
            if (
                total_res.freshness_seconds
                and total_res.freshness_seconds > profile.sample_freshness_limit_seconds
            ):
                return CheckObservation(
                    name="business_traffic",
                    observed_at=now,
                    freshness_seconds=total_res.freshness_seconds,
                    status=CheckStatus.UNKNOWN,
                    reason="Stale traffic metric sample",
                )

            req_per_sec = total_res.samples[0].latest_value
            # In a 60s window, approximate total requests:
            window_requests = req_per_sec * 60.0

            # Traffic denominator check: minimum 20 requests
            if window_requests < min_requests:
                return CheckObservation(
                    name="business_traffic",
                    observed_at=now,
                    value=round(window_requests, 1),
                    unit="requests/60s",
                    threshold=min_requests,
                    status=CheckStatus.UNKNOWN,
                    reason=f"Insufficient traffic denominator ({window_requests:.1f} < {min_requests} reqs in 60s window)",
                )

            # Check error rate
            err_rate = 0.0
            err_res = await self.prometheus_client.query_instant(error_query)
            if err_res.status != "success":
                return CheckObservation(
                    name="business_traffic",
                    observed_at=now,
                    status=CheckStatus.UNKNOWN,
                    reason=f"Prometheus error query status: {err_res.status}",
                )

            if err_res.samples and err_res.samples[0].latest_value is not None:
                err_per_sec = err_res.samples[0].latest_value
                if req_per_sec > 0:
                    err_rate = err_per_sec / req_per_sec

            # SCN-002: Check baseline success rate min (e.g. 0.98)
            if baseline_success_min is not None:
                success_rate = 1.0 - err_rate
                if success_rate < baseline_success_min:
                    return CheckObservation(
                        name="business_traffic",
                        observed_at=now,
                        value=round(success_rate, 4),
                        unit="success_ratio",
                        threshold=baseline_success_min,
                        status=CheckStatus.FAIL,
                        reason=f"Success rate {success_rate:.2%} below baseline band {baseline_success_min:.2%}",
                    )
                return CheckObservation(
                    name="business_traffic",
                    observed_at=now,
                    value=round(success_rate, 4),
                    unit="success_ratio",
                    threshold=baseline_success_min,
                    status=CheckStatus.PASS,
                    reason=f"Success rate {success_rate:.2%} meets baseline band (>= {baseline_success_min:.2%})",
                )

            # SCN-003: Check 5xx error rate max (e.g. 0.01)
            if max_5xx_rate is not None:
                if err_rate > max_5xx_rate:
                    return CheckObservation(
                        name="business_traffic",
                        observed_at=now,
                        value=round(err_rate, 4),
                        unit="5xx_ratio",
                        threshold=max_5xx_rate,
                        status=CheckStatus.FAIL,
                        reason=f"5xx error rate {err_rate:.2%} exceeds threshold {max_5xx_rate:.2%}",
                    )
                return CheckObservation(
                    name="business_traffic",
                    observed_at=now,
                    value=round(err_rate, 4),
                    unit="5xx_ratio",
                    threshold=max_5xx_rate,
                    status=CheckStatus.PASS,
                    reason=f"5xx error rate {err_rate:.2%} within threshold {max_5xx_rate:.2%}",
                )

            # SCN-001 / default: Check general error rate max (e.g. 0.02)
            threshold = float(max_error_rate or 0.02)
            if err_rate > threshold:
                return CheckObservation(
                    name="business_traffic",
                    observed_at=now,
                    value=round(err_rate, 4),
                    unit="error_ratio",
                    threshold=threshold,
                    status=CheckStatus.FAIL,
                    reason=f"Error rate {err_rate:.2%} exceeds threshold {threshold:.2%}",
                )

            return CheckObservation(
                name="business_traffic",
                observed_at=now,
                value=round(err_rate, 4),
                unit="error_ratio",
                threshold=threshold,
                status=CheckStatus.PASS,
                reason=f"Error rate {err_rate:.2%} within threshold {threshold:.2%}",
            )
        except Exception as exc:
            return CheckObservation(
                name="business_traffic",
                observed_at=now,
                status=CheckStatus.UNKNOWN,
                reason=f"Traffic error rate query error: {exc}",
            )

    async def _evaluate_latency(
        self,
        profile: VerificationProfile,
        counter_ready: bool,
        counter_reason: str,
        now: datetime,
    ) -> CheckObservation:
        max_lat = profile.traffic.get("max_p95_latency_seconds")
        if max_lat is None:
            return CheckObservation(
                name="business_latency",
                observed_at=now,
                status=CheckStatus.PASS,
                reason="Latency histogram not currently required or active for this profile",
            )

        if not counter_ready:
            return CheckObservation(
                name="business_latency",
                observed_at=now,
                status=CheckStatus.UNKNOWN,
                reason=counter_reason,
            )

        query = 'histogram_quantile(0.95, sum(rate(demo_api_http_request_duration_seconds_bucket{job="demo-api",route="/jobs"}[1m])) by (le))'

        try:
            res = await self.prometheus_client.query_instant(query)
            if res.status != "success":
                return CheckObservation(
                    name="business_latency",
                    observed_at=now,
                    status=CheckStatus.UNKNOWN,
                    reason=f"Prometheus latency query status: {res.status}",
                )

            if not res.samples or res.samples[0].latest_value is None:
                return CheckObservation(
                    name="business_latency",
                    observed_at=now,
                    status=CheckStatus.UNKNOWN,
                    reason="Prometheus returned no latency samples",
                )

            val = res.samples[0].latest_value
            if val > max_lat:
                return CheckObservation(
                    name="business_latency",
                    observed_at=now,
                    value=round(val, 4),
                    unit="seconds",
                    threshold=max_lat,
                    status=CheckStatus.FAIL,
                    reason=f"p95 latency {val:.3f}s exceeds threshold {max_lat}s",
                )

            return CheckObservation(
                name="business_latency",
                observed_at=now,
                value=round(val, 4),
                unit="seconds",
                threshold=max_lat,
                status=CheckStatus.PASS,
                reason=f"p95 latency {val:.3f}s within threshold {max_lat}s",
            )
        except Exception as exc:
            return CheckObservation(
                name="business_latency",
                observed_at=now,
                status=CheckStatus.UNKNOWN,
                reason=f"Prometheus latency query error: {exc}",
            )

    async def _evaluate_alerts(
        self, profile: VerificationProfile, now: datetime
    ) -> CheckObservation:
        prohibited = set(profile.alerts.get("prohibited_active_alerts", []))
        if not prohibited:
            return CheckObservation(
                name="alerts",
                observed_at=now,
                status=CheckStatus.PASS,
                reason="No prohibited alerts defined for profile",
            )

        try:
            active_alerts = await self.prometheus_client.get_active_alerts()
            firing = [
                a.name
                for a in active_alerts
                if a.name in prohibited and a.state.lower() == "firing"
            ]

            if firing:
                return CheckObservation(
                    name="alerts",
                    observed_at=now,
                    value=",".join(firing),
                    status=CheckStatus.FAIL,
                    reason=f"Prohibited alerts currently firing: {', '.join(firing)}",
                )

            return CheckObservation(
                name="alerts",
                observed_at=now,
                value="none_firing",
                status=CheckStatus.PASS,
                reason="All monitored alerts are non-firing",
            )
        except Exception as exc:
            return CheckObservation(
                name="alerts",
                observed_at=now,
                status=CheckStatus.UNKNOWN,
                reason=f"Failed to query Prometheus active alerts: {exc}",
            )

    def _compute_health_score(
        self, profile: VerificationProfile, checks: dict[str, CheckObservation]
    ) -> float:
        """Computes weighted health score in [0.0, 1.0]."""
        weights = profile.weights
        w_a = weights.get("alerts", 0.25)
        w_e = weights.get("error_rate", 0.25)
        w_l = weights.get("latency", 0.25)
        w_r = weights.get("readiness", 0.25)

        total_weight = w_a + w_e + w_l + w_r
        if total_weight <= 0:
            total_weight = 1.0

        def check_score(name: str) -> float:
            c = checks.get(name)
            if not c or c.status != CheckStatus.PASS:
                return 0.0
            return 1.0

        score = (
            w_a * check_score("alerts")
            + w_e * check_score("business_traffic")
            + w_l * check_score("business_latency")
            + w_r * check_score("readiness")
        ) / total_weight

        return max(0.0, min(1.0, round(score, 4)))
