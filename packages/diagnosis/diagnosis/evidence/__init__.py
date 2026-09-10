"""Evidence collection, redaction, and container inspection boundary."""

from diagnosis.evidence.collector import EvidenceCollector
from diagnosis.evidence.container_reader import (
    ContainerReaderProtocol,
    DockerContainerReader,
    MockContainerReader,
)
from diagnosis.evidence.redactor import redact_payload, redact_string

__all__ = [
    "ContainerReaderProtocol",
    "DockerContainerReader",
    "EvidenceCollector",
    "MockContainerReader",
    "redact_payload",
    "redact_string",
]
