# ADR-0003: M1 Incident State Machine Safe Escalation Transitions

## Status
Accepted

## Context
The v4 incident state machine defined transitions:
```text
DETECTED -> TRIAGED -> DIAGNOSED -> PLANNED
PLANNED -> EXECUTING (auto-approval)
PLANNED -> PENDING_APPROVAL -> APPROVED -> EXECUTING
EXECUTING -> VERIFYING -> RESOLVED
VERIFYING -> DIAGNOSED (retry)
VERIFYING -> ESCALATED
EXECUTING -> FAILED -> ESCALATED
```
However, the state machine lacked safe terminal transitions when:
1. An incident reaches `PLANNED`, but policy evaluation denies execution (e.g. prohibited action, confidence below threshold, or automation disabled).
2. An incident reaches `APPROVED`, but before execution can dispatch, the approval expires, policy changes, emergency stop triggers, or the container ID becomes invalid.

In both cases, there was no legal edge to transition the incident to `ESCALATED`. Forcing the incident into `EXECUTING` would falsely claim execution started when it was denied or invalid.

## Decision
We amend the authoritative incident state machine across repository guards, database constraints, shared models, workflow nodes, and documentation to add two explicit safe transitions:
1. `PLANNED -> ESCALATED`: Invoked when policy evaluation rejects a plan, confidence is insufficient for remediation, or target eligibility fails.
2. `APPROVED -> ESCALATED`: Invoked when an approved plan cannot be dispatched due to approval grant expiry (30-minute timeout), policy revocation, emergency kill-switch activation, or target container mismatch.

Direct transition from any state to `RESOLVED` without verified telemetry remains strictly prohibited. `FAILED -> ROLLEDBACK` remains reserved for future actions supporting genuine rollback; M1 restart does not report fictitious rollbacks.

## Consequences
- **Positive:** Eliminates deadlock or fraudulent execution transitions when policy denies an action or an approval becomes stale.
- **Positive:** Preserves full auditability of policy rejections and expired approvals.
- **Negative:** Enforcing database check constraints and repository transition tables must be updated simultaneously in M1-B.
