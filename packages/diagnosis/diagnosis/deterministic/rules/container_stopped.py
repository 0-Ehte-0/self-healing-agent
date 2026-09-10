from sharedmodels.enums import RootCause

from diagnosis.deterministic.rules.base import DiagnosticRule
from diagnosis.schemas import ObservationBundle, RuleEvaluationResult


class ContainerStoppedRule(DiagnosticRule):
    """RULE-001: Container Crash / Stopped.

    Prioritized over secondary readiness failures. A stopped container prevents
    all API communication and must be diagnosed directly as CONTAINER_STOPPED.
    """

    rule_id = "RULE-001-CONTAINER-STOPPED"
    rule_version = "1.0"
    priority = 1

    def evaluate(self, obs: ObservationBundle) -> RuleEvaluationResult:
        evidence_ids = []
        contradictions = []

        is_stopped_state = obs.container_status in ("exited", "dead")
        is_up_zero = obs.up_metric is not None and obs.up_metric == 0.0
        is_alert_firing = "ContainerDown" in obs.firing_alerts

        # Collect evidence IDs if available
        for k, eid in obs.evidence_id_map.items():
            if "docker:inspect" in k or "prometheus:up" in k or "prometheus:active_alerts" in k:
                evidence_ids.append(eid)

        # Contradiction check: container running with healthy probes
        if (
            obs.container_status == "running"
            and obs.up_metric == 1.0
            and obs.latest_readiness_code == 200
        ):
            contradictions.append(
                "Container inspection shows 'running' with up=1 and readiness=200; cannot be stopped."
            )
            return RuleEvaluationResult(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                matched=False,
                confidence=0.0,
                root_cause=RootCause.CONTAINER_STOPPED,
                is_actionable=False,
                contradictory_findings=contradictions,
                reasoning={"reason": "Contradicted by running state and passing readiness probe"},
            )

        # Match condition: container is stopped or up=0 with non-running status
        if is_stopped_state or (is_up_zero and obs.container_status != "running"):
            confidence = 0.95
            if not is_alert_firing:
                confidence = 0.90  # Still highly confident from container inspection

            return RuleEvaluationResult(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                matched=True,
                confidence=confidence,
                root_cause=RootCause.CONTAINER_STOPPED,
                is_actionable=True,
                contributing_evidence_ids=evidence_ids,
                contradictory_findings=contradictions,
                reasoning={
                    "container_status": obs.container_status,
                    "exit_code": obs.exit_code,
                    "up_metric": obs.up_metric,
                    "firing_alerts": obs.firing_alerts,
                    "explanation": "Exact target container is stopped/exited; verified candidate for restart remediation.",
                },
            )

        return RuleEvaluationResult(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            matched=False,
            confidence=0.0,
            root_cause=RootCause.CONTAINER_STOPPED,
            is_actionable=False,
            contradictory_findings=contradictions,
        )
