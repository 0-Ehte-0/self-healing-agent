# ADR-0004: Execution Attempt Limits, Cooldown Semantics, and Uncertain Outcomes

## Status
Accepted

## Context
Automated remediation actions mutate physical infrastructure. Without strict bounds, an agent could trigger rapid mutation loops, crash loops, or duplicate executions across worker failures:
1. Attempt counters: In v4, `attempts` incremented on retry transitions (`VERIFYING -> DIAGNOSED`). This created ambiguity over whether `retry_limit=2` meant 2 attempts total or 1 initial attempt plus 2 retries (3 attempts total).
2. Resource cooldown: Consecutive restarts without cooldown can mask stabilization delays, induce cascade failures, or crash dependencies.
3. Non-atomic external dispatch: Calling Docker daemon restart is not transactional. If the agent worker dies immediately after issuing the Docker call, the outcome is indeterminate.

## Decision
1. **Attempt Bound Definition:** `retry_limit = 2` permits at most 3 mutating dispatches for a single incident (1 initial execution + at most 2 retries). The data model and UI distinguish `attempt_number` (1, 2, or 3) from `retries_used` (0, 1, or 2).
2. **Resource Cooldown Enforcement:** A strict 600-second per-resource cooldown applies to every restart mutation, including retries within the same incident. No retry may bypass cooldown.
3. **Idempotent Dispatch Key:** Every execution intent generates an idempotency key derived from `(incident_id, plan_version, step_id, attempt_number)`.
4. **Handling Uncertain Outcomes:** If Docker dispatch times out, returns an ambiguous response, or the worker crashes before capturing the outcome, the execution is marked `UNCERTAIN`. Blind retries are prohibited; reconciliation inspects Docker container state and inspect timestamps. If safe continuation cannot be proven, the incident transitions `EXECUTING -> FAILED -> ESCALATED`.

## Consequences
- **Positive:** Guaranteed protection against runaway restart loops.
- **Positive:** Clear audit trail and operator visibility for failed or uncertain dispatches.
- **Negative:** A 600-second cooldown per retry extends multi-attempt test durations; tests must use isolated timestamps or dedicated test profiles.
