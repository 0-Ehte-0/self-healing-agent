from sharedmodels.enums import RootCause

from diagnosis.deterministic.rules.base import DiagnosticRule
from diagnosis.schemas import ObservationBundle, RuleEvaluationResult


class ApiUnresponsiveRule(DiagnosticRule):
    """RULE-003: Unresponsive API caused by a process-local fault.

    Diagnoses an unresponsive API only when the target container is running and
    readiness/probe failures are corroborated without an external dependency failure
    or CPU saturation explaining the condition.
    """

    rule_id = "RULE-003-API-UNRESPONSIVE"
    rule_version = "1.0"
    priority = 4

    def evaluate(self, obs: ObservationBundle) -> RuleEvaluationResult:
        evidence_ids = []
        contradictions = []

        is_running = obs.container_status == "running"
        is_alert_firing = "ApiUnresponsive" in obs.firing_alerts
        readiness_failed = (
            (obs.readiness_success_ratio is not None and obs.readiness_success_ratio < 0.5)
            or (obs.latest_readiness_code is not None and obs.latest_readiness_code != 200)
            or is_alert_firing
        )

        for k, eid in obs.evidence_id_map.items():
            if (
                "prometheus:probe_success" in k
                or "docker:inspect" in k
                or "prometheus:active_alerts" in k
            ):
                evidence_ids.append(eid)

        # Contradiction check 1: Readiness is 200 OK with 100% success
        if (
            obs.readiness_success_ratio is not None
            and obs.readiness_success_ratio >= 1.0
            and obs.latest_readiness_code == 200
        ):
            contradictions.append(
                "Readiness probe returns HTTP 200 with 100% success ratio; API is responsive."
            )
            return RuleEvaluationResult(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                matched=False,
                confidence=0.0,
                root_cause=RootCause.API_UNRESPONSIVE,
                is_actionable=False,
                contradictory_findings=contradictions,
            )

        # Contradiction check 2: CPU saturation explains the slow/failing API
        if obs.normalized_cpu is not None and obs.normalized_cpu >= 0.75:
            contradictions.append(
                f"Normalized CPU is {obs.normalized_cpu:.2f} >= 0.75; CPU saturation takes precedence."
            )
            return RuleEvaluationResult(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                matched=False,
                confidence=0.0,
                root_cause=RootCause.API_UNRESPONSIVE,
                is_actionable=False,
                contradictory_findings=contradictions,
            )

        # Contradiction check 3: Redis or PostgreSQL dependency explains the failure
        if obs.redis_connected is False or (
            obs.db_connections_active is not None and obs.db_connections_active >= 8
        ):
            contradictions.append(
                "External dependency (Redis/DB) is failing; dependency failure takes precedence."
            )
            return RuleEvaluationResult(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                matched=False,
                confidence=0.0,
                root_cause=RootCause.API_UNRESPONSIVE,
                is_actionable=False,
                contradictory_findings=contradictions,
            )

        # Match condition: running container with failing readiness probe and healthy dependencies
        if is_running and readiness_failed:
            confidence = 0.90 if is_alert_firing else 0.85

            return RuleEvaluationResult(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                matched=True,
                confidence=confidence,
                root_cause=RootCause.API_UNRESPONSIVE,
                is_actionable=True,
                contributing_evidence_ids=evidence_ids,
                contradictory_findings=contradictions,
                reasoning={
                    "container_status": obs.container_status,
                    "readiness_success_ratio": obs.readiness_success_ratio,
                    "latest_readiness_code": obs.latest_readiness_code,
                    "firing_alerts": obs.firing_alerts,
                    "explanation": "Target container is running but API readiness endpoint fails or times out without CPU or dependency saturation; restart candidate.",
                },
            )

        return RuleEvaluationResult(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            matched=False,
            confidence=0.0,
            root_cause=RootCause.API_UNRESPONSIVE,
            is_actionable=False,
            contradictory_findings=contradictions,
        )
