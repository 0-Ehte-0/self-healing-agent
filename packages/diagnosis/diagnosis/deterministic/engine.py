import logging
from uuid import UUID, uuid4

from sharedmodels.diagnosis import DiagnosisSchema
from sharedmodels.enums import RootCause

from diagnosis.deterministic.rules import (
    ApiUnresponsiveRule,
    ContainerStoppedRule,
    CpuSaturationRule,
    DependencyUnavailableRule,
    DiagnosticRule,
    NoActiveFaultRule,
)
from diagnosis.schemas import ObservationBundle, RuleEvaluationResult

logger = logging.getLogger(__name__)


class DeterministicDiagnosisEngine:
    """Evaluates telemetry observations against ordered deterministic diagnostic rules."""

    def __init__(self, rules: list[DiagnosticRule] | None = None):
        if rules is None:
            self.rules: list[DiagnosticRule] = [
                ContainerStoppedRule(),
                DependencyUnavailableRule(),
                CpuSaturationRule(),
                ApiUnresponsiveRule(),
                NoActiveFaultRule(),
            ]
        else:
            self.rules = rules

        # Sort rules by priority (ascending: 1 is evaluated first)
        self.rules.sort(key=lambda r: r.priority)

    async def diagnose(
        self,
        incident_id: UUID,
        obs: ObservationBundle,
        parent_diagnosis_id: UUID | None = None,
        actor: str = "agent:diagnosis_engine",
    ) -> DiagnosisSchema:
        """Applies the diagnostic decision table and returns a structured, typed DiagnosisSchema."""
        evidence_ids = list(obs.evidence_id_map.values())
        all_contradictions: list[str] = []

        # 1. Check for unavailable telemetry after bounded retries
        if obs.is_telemetry_unavailable or obs.completeness.get("prometheus") == "UNAVAILABLE":
            reason = "Mandatory telemetry sources are unavailable after bounded retries. Cannot safely diagnose."
            return DiagnosisSchema(
                id=uuid4(),
                incident_id=incident_id,
                root_cause=RootCause.INSUFFICIENT_EVIDENCE,
                confidence=0.0,
                evidence_ids=evidence_ids,
                rule_id="RULE-FALLBACK-TELEMETRY-UNAVAILABLE",
                rule_version="1.0",
                contradictory_findings=["Prometheus telemetry client returned UNAVAILABLE"],
                escalation_reason=reason,
                reasoning={"error": reason, "completeness": obs.completeness},
                is_actionable=False,
                parent_diagnosis_id=parent_diagnosis_id,
                actor=actor,
            )

        # 2. Check for stale telemetry data (exceeding 15m / 900s metric collection window)
        if obs.freshness_seconds is not None and obs.freshness_seconds > 900:
            reason = f"Telemetry freshness ({obs.freshness_seconds:.1f}s) exceeds allowable window (900s); data is stale. Insufficient evidence."
            return DiagnosisSchema(
                id=uuid4(),
                incident_id=incident_id,
                root_cause=RootCause.INSUFFICIENT_EVIDENCE,
                confidence=0.0,
                evidence_ids=evidence_ids,
                rule_id="RULE-FALLBACK-STALE-TELEMETRY",
                rule_version="1.0",
                contradictory_findings=[reason],
                escalation_reason=reason,
                reasoning={"error": reason, "freshness_seconds": obs.freshness_seconds},
                is_actionable=False,
                parent_diagnosis_id=parent_diagnosis_id,
                actor=actor,
            )

        # 2. Check for unknown resource / container not found
        if obs.container_status == "not_found":
            reason = f"Target resource/container {obs.container_id or 'unknown'} was not found in environment."
            return DiagnosisSchema(
                id=uuid4(),
                incident_id=incident_id,
                root_cause=RootCause.INSUFFICIENT_EVIDENCE,
                confidence=0.0,
                evidence_ids=evidence_ids,
                rule_id="RULE-FALLBACK-RESOURCE-NOT-FOUND",
                rule_version="1.0",
                contradictory_findings=[reason],
                escalation_reason=reason,
                reasoning={"error": reason, "container_id": obs.container_id},
                is_actionable=False,
                parent_diagnosis_id=parent_diagnosis_id,
                actor=actor,
            )

        # 3. Evaluate rules in strict priority order
        matching_results: list[RuleEvaluationResult] = []

        for rule in self.rules:
            result = rule.evaluate(obs)
            if result.contradictory_findings:
                all_contradictions.extend(result.contradictory_findings)

            if result.matched:
                matching_results.append(result)

        # 4. Decision table resolution
        if matching_results:
            # Top priority match wins
            chosen = matching_results[0]
            # Merge accumulated contradictions
            combined_contradictions = list(
                dict.fromkeys(chosen.contradictory_findings + all_contradictions)
            )

            return DiagnosisSchema(
                id=uuid4(),
                incident_id=incident_id,
                root_cause=chosen.root_cause,
                confidence=chosen.confidence,
                evidence_ids=chosen.contributing_evidence_ids or evidence_ids,
                rule_id=chosen.rule_id,
                rule_version=chosen.rule_version,
                contradictory_findings=combined_contradictions,
                escalation_reason=chosen.escalation_reason,
                reasoning=chosen.reasoning,
                is_actionable=chosen.is_actionable,
                parent_diagnosis_id=parent_diagnosis_id,
                actor=actor,
            )

        # 5. No rule matched: check if contradictory evidence explains the lack of match
        if all_contradictions:
            reason = "Conflicting evidence observations prevent a decisive root-cause determination; escalating."
            return DiagnosisSchema(
                id=uuid4(),
                incident_id=incident_id,
                root_cause=RootCause.AMBIGUOUS,
                confidence=0.40,
                evidence_ids=evidence_ids,
                rule_id="RULE-FALLBACK-AMBIGUOUS",
                rule_version="1.0",
                contradictory_findings=all_contradictions,
                escalation_reason=reason,
                reasoning={"contradictions": all_contradictions, "explanation": reason},
                is_actionable=False,
                parent_diagnosis_id=parent_diagnosis_id,
                actor=actor,
            )

        # 6. Unconfirmed alert / missing evidence fallback
        reason = "Alert was received but observations do not confirm an active fault; insufficient evidence for mutation."
        return DiagnosisSchema(
            id=uuid4(),
            incident_id=incident_id,
            root_cause=RootCause.INSUFFICIENT_EVIDENCE,
            confidence=0.0,
            evidence_ids=evidence_ids,
            rule_id="RULE-FALLBACK-INSUFFICIENT-EVIDENCE",
            rule_version="1.0",
            contradictory_findings=[],
            escalation_reason=reason,
            reasoning={"firing_alerts": obs.firing_alerts, "explanation": reason},
            is_actionable=False,
            parent_diagnosis_id=parent_diagnosis_id,
            actor=actor,
        )
