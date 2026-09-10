# ADR-0007: Independent Telemetry Verification, Stabilization Windows, and Bounded Retry Integration

## Status
Accepted

## Context
Executing a Docker restart mutation does not by itself guarantee that an incident is resolved. Real workloads may experience delayed failures, probe flaps, lingering CPU saturation, or process hangs that restart does not clear. To resolve an incident honestly:
1. **Multi-Signal Verification:** Verification must independently query telemetry (container status, HTTP readiness, CPU saturation, latency, error rates, and Prometheus alerts).
2. **Continuous Stabilization Window:** The healthy stabilization interval is a continuous 90 seconds (at least 7 evaluations sampled every 15 seconds). Any failed check or missing/unknown data immediately resets the elapsed healthy timer to 0.
3. **Readiness Warm-Up Prerequisite:** The 90-second stabilization timer must not begin until `/health/ready` returns HTTP 200 within 200ms across three consecutive checks. The duration of this warm-up phase must be recorded separately.
4. **Post-Restart Counter Drops:** When a container restarts, Prometheus request counters drop to zero. Rates must wait for post-restart counter samples to become usable (minimum 2 post-restart samples) and require at least 20 business requests in the 60-second window.
5. **Bounded Retry Semantics:** `retry_limit = 2` permits at most 3 mutating dispatches (1 initial attempt + at most 2 retries). Every retry enforces a strict 600-second per-resource cooldown via persistent PostgreSQL `workflow_schedules`. If retries are exhausted or the 45-minute workflow deadline is exceeded, the incident transitions `VERIFYING -> ESCALATED`.
6. **External Recovery Attribution:** If an injected fault expires (via TTL) or is manually cleared before verification finishes, the recovery cannot be attributed to the agent. The run is labeled `EXTERNALLY_RECOVERED` or `INCONCLUSIVE` and excluded from the successful self-healing numerator.

## Decision
1. **Verification Profiles:** Define versioned scenario verification profiles (`SCN-001`, `SCN-002`, `SCN-003`) declaring exact thresholds, baseline bands, timeouts, and alert definitions.
2. **Evaluator and Independent Verifier:** Implement `IndependentVerifier` fulfilling `VerifierProtocol`. Evaluate every 15 seconds up to 300 seconds maximum duration. Track `warm_up_duration_seconds`, `stabilization_resets`, and `samples` (persisting all individual check evaluations in JSONB).
3. **Reset on Failure:** If any check returns `FAIL` or `UNKNOWN`, reset `elapsed_healthy_seconds = 0.0` and increment `stabilization_resets`.
4. **Atomic State Transitions:**
   - Passing verification transitions `VERIFYING -> RESOLVED`, storing `resolved_at`.
   - Failing verification with remaining retry budget and feasible deadline transitions `VERIFYING -> DIAGNOSED` (incrementing incident `attempts`) and pauses the workflow with `wait_reason = "COOLDOWN"`.
   - Failing verification with exhausted retries or exceeded deadline transitions `VERIFYING -> ESCALATED`, generating an `EscalationRecord` and `AttentionItem`.
   - Fault auto-expiry or external clearance before verification completion transitions `VERIFYING -> ESCALATED` with attribution `EXTERNALLY_RECOVERED`.
5. **Retry Wake-up Recheck:** At retry wake-up, inspect workload health. If the workload is already healthy without a new restart (e.g. cleared externally), record `NO_ACTIVE_FAULT` and escalate rather than executing an unneeded restart.

## Consequences
- **Positive:** Incidents are resolved only when true, sustained operational recovery is proven.
- **Positive:** Guarantees protection against runaway retry loops and false-positive recovery claims.
- **Positive:** Complete observation samples and stabilization reset events are persisted for forensic auditing.
- **Negative:** Full 90-second observation intervals and 600-second retry cooldowns require significant elapsed time for live verification; unit and integration test doubles must simulate time without sleeping.
