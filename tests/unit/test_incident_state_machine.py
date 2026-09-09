import pytest
from app.domain.incidents.state_machine import validate_transition
from sharedmodels.enums import IncidentState


@pytest.mark.parametrize(
    "current,target",
    [
        ("DETECTED", "EXECUTING"),
        ("DETECTED", "RESOLVED"),
        ("EXECUTING", "RESOLVED"),
        ("FAILED", "RESOLVED"),
        ("RESOLVED", "DETECTED"),
        ("ESCALATED", "DIAGNOSED"),
        ("ROLLEDBACK", "EXECUTING"),
        ("PENDING_APPROVAL", "EXECUTING"),
    ],
)
def test_forbidden_shortcuts_and_terminal_reentry(current, target):
    with pytest.raises(ValueError):
        validate_transition(IncidentState(current), IncidentState(target))


@pytest.mark.parametrize(
    "path",
    [
        ["DETECTED", "TRIAGED", "DIAGNOSED", "PLANNED", "EXECUTING", "FAILED", "ROLLEDBACK"],
        ["DETECTED", "TRIAGED", "ESCALATED"],
        ["DETECTED", "TRIAGED", "DIAGNOSED", "ESCALATED"],
    ],
)
def test_documented_failure_paths(path):
    for current, target in zip(path, path[1:], strict=False):
        validate_transition(IncidentState(current), IncidentState(target))
