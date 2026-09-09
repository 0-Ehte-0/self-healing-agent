from sharedmodels.enums import IncidentState as S

TRANSITIONS = {
    S.DETECTED: {S.TRIAGED},
    S.TRIAGED: {S.DIAGNOSED, S.ESCALATED},
    S.DIAGNOSED: {S.PLANNED, S.ESCALATED},
    S.PLANNED: {S.PENDING_APPROVAL, S.EXECUTING},
    S.PENDING_APPROVAL: {S.APPROVED, S.ESCALATED},
    S.APPROVED: {S.EXECUTING},
    S.EXECUTING: {S.VERIFYING, S.FAILED},
    S.VERIFYING: {S.RESOLVED, S.DIAGNOSED, S.ESCALATED},
    S.FAILED: {S.ROLLEDBACK, S.ESCALATED},
    S.RESOLVED: set(),
    S.ROLLEDBACK: set(),
    S.ESCALATED: set(),
}


def validate_transition(current: S, target: S) -> None:
    if target not in TRANSITIONS[current]:
        raise ValueError(f"Illegal incident transition: {current} -> {target}")
