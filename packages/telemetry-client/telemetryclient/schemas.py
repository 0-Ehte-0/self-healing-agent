from datetime import UTC, datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class MetricValue(BaseModel):
    timestamp: datetime
    value: float


class MetricSample(BaseModel):
    metric: dict[str, str] = Field(default_factory=dict)
    values: list[MetricValue] = Field(default_factory=list)
    latest_value: float | None = None
    latest_timestamp: datetime | None = None


class MetricQueryResult(BaseModel):
    query: str
    status: str  # "success", "empty", "error", "unavailable"
    samples: list[MetricSample] = Field(default_factory=list)
    freshness_seconds: float | None = None
    is_truncated: bool = False
    byte_size: int = 0
    error_message: str | None = None


class AlertState(BaseModel):
    name: str
    state: str  # "firing", "pending", "inactive"
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    active_at: datetime | None = None
    value: str | None = None


class LogEntry(BaseModel):
    timestamp: datetime
    line: str
    labels: dict[str, str] = Field(default_factory=dict)


class LogQueryResult(BaseModel):
    query: str
    status: str  # "success", "empty", "error", "unavailable"
    entries: list[LogEntry] = Field(default_factory=list)
    total_lines: int = 0
    is_truncated: bool = False
    byte_size: int = 0
    freshness_seconds: float | None = None
    error_message: str | None = None


class ContainerInspectionResult(BaseModel):
    container_id: str
    name: str = ""
    state: str = (
        "unknown"  # "running", "exited", "dead", "restarting", "paused", "not_found", "unknown"
    )
    exit_code: int | None = None
    health_status: str | None = None  # "healthy", "unhealthy", "starting", "none"
    binding_generation: int | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: str = "success"  # "success", "error", "not_found"
    error_message: str | None = None
