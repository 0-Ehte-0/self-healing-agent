from abc import ABC, abstractmethod

from diagnosis.schemas import ObservationBundle, RuleEvaluationResult


class DiagnosticRule(ABC):
    """Abstract base class for deterministic diagnostic rules."""

    rule_id: str
    rule_version: str
    priority: int  # Lower number = higher evaluation precedence

    @abstractmethod
    def evaluate(self, obs: ObservationBundle) -> RuleEvaluationResult:
        """Evaluates observations and returns a structured RuleEvaluationResult."""
        pass
