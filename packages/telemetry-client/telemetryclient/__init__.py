"""Typed telemetry clients for Prometheus and Loki with resource scoping and limits."""

from telemetryclient.loki import LokiClient
from telemetryclient.prometheus import PrometheusClient
from telemetryclient.schemas import (
    AlertState,
    ContainerInspectionResult,
    LogEntry,
    LogQueryResult,
    MetricQueryResult,
    MetricSample,
    MetricValue,
)

__all__ = [
    "AlertState",
    "ContainerInspectionResult",
    "LogEntry",
    "LogQueryResult",
    "LokiClient",
    "MetricQueryResult",
    "MetricSample",
    "MetricValue",
    "PrometheusClient",
]
