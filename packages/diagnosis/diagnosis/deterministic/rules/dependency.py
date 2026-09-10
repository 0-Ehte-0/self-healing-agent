from sharedmodels.enums import RootCause

from diagnosis.deterministic.rules.base import DiagnosticRule
from diagnosis.schemas import ObservationBundle, RuleEvaluationResult


class DependencyUnavailableRule(DiagnosticRule):
    """RULE-004: Redis / PostgreSQL Dependency Unavailable.

    Evaluated with high priority to ensure external infrastructure outages
    are not erroneously diagnosed as container-internal crashes or hangs.
    """

    rule_id = "RULE-004-DEPENDENCY-UNAVAILABLE"
    rule_version = "1.0"
    priority = 2

    def evaluate(self, obs: ObservationBundle) -> RuleEvaluationResult:
        evidence_ids: list[str] = []
        contradictions: list[str] = []

        is_redis_down = obs.redis_connected is False or "RedisUnreachable" in obs.firing_alerts
        is_db_pool_exhausted = (
            obs.db_connections_active is not None and obs.db_connections_active >= 8
        ) or "PostgresPoolExhausted" in obs.firing_alerts

        # Check for dependency errors in recent logs
        log_dep_error = any(
            "redis" in line.lower()
            or "postgres" in line.lower()
            or "connection pool" in line.lower()
            for line in obs.recent_log_errors
        )

        for k, eid in obs.evidence_id_map.items():
            if (
                "prometheus:redis_connected" in k
                or "prometheus:db_connections" in k
                or "loki:query" in k
                or "prometheus:active_alerts" in k
            ):
                evidence_ids.append(eid)

        if is_redis_down or is_db_pool_exhausted or log_dep_error:
            dep_name = "Redis" if is_redis_down else "PostgreSQL"
            if is_redis_down and is_db_pool_exhausted:
                dep_name = "Redis and PostgreSQL"

            reason = f"Underlying external dependency ({dep_name}) is unavailable or exhausted. Escalate; no database or Redis mutation allowed in M1."

            return RuleEvaluationResult(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                matched=True,
                confidence=0.90,
                root_cause=RootCause.DEPENDENCY_UNAVAILABLE,
                is_actionable=False,
                contributing_evidence_ids=evidence_ids,
                contradictory_findings=contradictions,
                escalation_reason=reason,
                reasoning={
                    "redis_connected": obs.redis_connected,
                    "db_connections_active": obs.db_connections_active,
                    "firing_alerts": obs.firing_alerts,
                    "log_errors": obs.recent_log_errors[:3],
                    "explanation": reason,
                },
            )

        return RuleEvaluationResult(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            matched=False,
            confidence=0.0,
            root_cause=RootCause.DEPENDENCY_UNAVAILABLE,
            is_actionable=False,
        )
