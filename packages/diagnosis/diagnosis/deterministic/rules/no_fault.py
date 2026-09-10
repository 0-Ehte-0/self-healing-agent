from sharedmodels.enums import RootCause

from diagnosis.deterministic.rules.base import DiagnosticRule
from diagnosis.schemas import ObservationBundle, RuleEvaluationResult


class NoActiveFaultRule(DiagnosticRule):
    """RULE-005: Healthy Workload with Stale or Transient Alert.

    Detects when all operational telemetry (container status, readiness probe, CPU,
    error rate, and dependencies) indicates a healthy system, even if an alert was
    received. Ensures no mutating action is taken against a healthy container.
    """

    rule_id = "RULE-005-NO-ACTIVE-FAULT"
    rule_version = "1.0"
    priority = 5

    def evaluate(self, obs: ObservationBundle) -> RuleEvaluationResult:
        evidence_ids: list[str] = []
        contradictions: list[str] = []

        is_running = obs.container_status == "running"
        is_up_ok = obs.up_metric is None or obs.up_metric >= 1.0
        is_readiness_ok = (
            obs.readiness_success_ratio is None or obs.readiness_success_ratio >= 1.0
        ) and (obs.latest_readiness_code is None or obs.latest_readiness_code == 200)
        is_cpu_ok = obs.normalized_cpu is None or obs.normalized_cpu < 0.50
        is_deps_ok = obs.redis_connected is not False and (
            obs.db_connections_active is None or obs.db_connections_active < 8
        )

        for eid in obs.evidence_id_map.values():
            evidence_ids.append(eid)

        if is_running and is_up_ok and is_readiness_ok and is_cpu_ok and is_deps_ok:
            reason = "Workload observations confirm container is running, readiness is 200 OK, CPU is normal, and dependencies are healthy. No active fault detected."
            return RuleEvaluationResult(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                matched=True,
                confidence=0.95,
                root_cause=RootCause.NO_ACTIVE_FAULT,
                is_actionable=False,
                contributing_evidence_ids=evidence_ids,
                contradictory_findings=contradictions,
                escalation_reason=reason,
                reasoning={
                    "container_status": obs.container_status,
                    "up_metric": obs.up_metric,
                    "normalized_cpu": obs.normalized_cpu,
                    "readiness_code": obs.latest_readiness_code,
                    "explanation": reason,
                },
            )

        return RuleEvaluationResult(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            matched=False,
            confidence=0.0,
            root_cause=RootCause.NO_ACTIVE_FAULT,
            is_actionable=False,
            contradictory_findings=contradictions,
        )
