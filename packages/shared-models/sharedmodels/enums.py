from enum import StrEnum


class IncidentState(StrEnum):
    DETECTED = "DETECTED"
    TRIAGED = "TRIAGED"
    DIAGNOSED = "DIAGNOSED"
    PLANNED = "PLANNED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"
    ROLLEDBACK = "ROLLEDBACK"


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ExecutionStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ROLLEDBACK = "ROLLEDBACK"


class EventSource(StrEnum):
    ALERTMANAGER = "ALERTMANAGER"
    GENERIC = "GENERIC"
    ISOLATION_FOREST = "ISOLATION_FOREST"
    CLOUDWATCH = "CLOUDWATCH"


class UserRole(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    APPROVER = "approver"
    ADMIN = "admin"
