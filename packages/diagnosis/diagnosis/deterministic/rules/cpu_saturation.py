from sharedmodels.enums import RootCause

from diagnosis.deterministic.rules.base import DiagnosticRule
from diagnosis.schemas import ObservationBundle, RuleEvaluationResult


class CpuSaturationRule(DiagnosticRule):
    """RULE-002: CPU Saturation within the managed demo container.

    Diagnoses CPU saturation ONLY when the bound target container's normalized CPU
    signal exceeds 75% across configured windows. Host or external injector spikes
    are explicitly checked and prevented from authorizing a container restart.
    """

    rule_id = "RULE-002-CPU-SATURATION"
    rule_version = "1.0"
    priority = 3

    def evaluate(self, obs: ObservationBundle) -> RuleEvaluationResult:
        evidence_ids = []
        contradictions = []

        is_running = obs.container_status == "running"
        is_alert_firing = "HighCpuSaturation" in obs.firing_alerts
        norm_cpu = obs.normalized_cpu

        for k, eid in obs.evidence_id_map.items():
            if (
                "prometheus:cpu_rate" in k
                or "prometheus:process_cpu" in k
                or "prometheus:active_alerts" in k
            ):
                evidence_ids.append(eid)

        # Contradiction check 1: Target is not running
        if not is_running and obs.container_status in ("exited", "dead"):
            contradictions.append(
                f"Container is {obs.container_status}; container crash takes precedence over CPU saturation."
            )
            return RuleEvaluationResult(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                matched=False,
                confidence=0.0,
                root_cause=RootCause.CPU_SATURATION,
                is_actionable=False,
                contradictory_findings=contradictions,
            )

        # Contradiction check 2: Alert claims CPU saturation, but target container CPU is low (< 0.50)
        # This catches host saturation or injector saturation that must NOT authorize demo restart
        if is_alert_firing and norm_cpu is not None and norm_cpu < 0.50:
            contradictions.append(
                f"Alert 'HighCpuSaturation' is firing, but target container CPU is {norm_cpu:.2f} (< 0.50). Suspected external host or injector CPU saturation, not target fault."
            )
            return RuleEvaluationResult(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                matched=False,
                confidence=0.0,
                root_cause=RootCause.CPU_SATURATION,
                is_actionable=False,
                contradictory_findings=contradictions,
                reasoning={
                    "normalized_cpu": norm_cpu,
                    "explanation": "Target container CPU does not substantiate the high CPU alert.",
                },
            )

        # Match condition: container is running and target CPU >= 0.75 (or alert firing with CPU >= 0.70)
        if is_running and (
            (norm_cpu is not None and norm_cpu >= 0.75)
            or (is_alert_firing and norm_cpu is not None and norm_cpu >= 0.70)
        ):
            confidence = 0.90 if is_alert_firing else 0.85

            return RuleEvaluationResult(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                matched=True,
                confidence=confidence,
                root_cause=RootCause.CPU_SATURATION,
                is_actionable=True,
                contributing_evidence_ids=evidence_ids,
                contradictory_findings=contradictions,
                reasoning={
                    "normalized_cpu": norm_cpu,
                    "raw_cpu_rate": obs.raw_cpu_seconds_rate,
                    "cpu_budget_cores": obs.cpu_budget_cores,
                    "firing_alerts": obs.firing_alerts,
                    "explanation": f"Target container normalized CPU utilization ({norm_cpu:.2f}) exceeds 0.75 threshold; restart candidate.",
                },
            )

        return RuleEvaluationResult(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            matched=False,
            confidence=0.0,
            root_cause=RootCause.CPU_SATURATION,
            is_actionable=False,
            contradictory_findings=contradictions,
        )
