"""Diagnosis and evidence collection package."""

from diagnosis.deterministic.engine import DeterministicDiagnosisEngine
from diagnosis.evidence.collector import EvidenceCollector
from diagnosis.evidence.container_reader import (
    ContainerReaderProtocol,
    DockerContainerReader,
    MockContainerReader,
)
from diagnosis.evidence.redactor import redact_payload, redact_string
from diagnosis.schemas import ObservationBundle, RuleEvaluationResult

__all__ = [
    "ContainerReaderProtocol",
    "DeterministicDiagnosisEngine",
    "DockerContainerReader",
    "EvidenceCollector",
    "MockContainerReader",
    "ObservationBundle",
    "RuleEvaluationResult",
    "redact_payload",
    "redact_string",
]
