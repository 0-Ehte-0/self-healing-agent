# ADR-0005: M1 Independent Telemetry Verification Contract

## Status
Accepted

## Context
A remediation action cannot be considered successful simply because the Docker command exited with code 0. Resolution requires independent, multi-signal proof that the managed workload has achieved sustained health under real traffic.

## Decision
We freeze the M1 verification contract:
1. **Stabilization Window:** The healthy stabilization interval is 90 continuous seconds. Evaluation occurs every 15 seconds, requiring at least 7 consecutive passing evaluation samples spanning the full 90 seconds. Any failing or unknown sample immediately resets the elapsed healthy timer to 0.
2. **Readiness Prerequisite:** Before the 90-second stabilization timer begins, `/health/ready` must return HTTP 200 with response time < 200ms across 3 consecutive checks.
3. **Target CPU Saturation Formula:** CPU usage is measured from `demo-api`'s monotonically increasing `demo_api_cpu_seconds_total` Counter. CPU saturation is normalized against the target's assigned CPU core budget:
   $$\text{CPU Saturation} = \frac{\text{rate}(\text{demo\_api\_cpu\_seconds\_total}[1\text{m}])}{\text{cpu\_budget\_cores}}$$
   Saturation must remain below 0.75 (75%) across two consecutive 30-second windows.
4. **Latency and Error Thresholds:**
   - p95 business latency must be < 0.200s.
   - HTTP error rate must be < 2% (SCN-001), success rate > 98% (SCN-002), and 5xx error rate < 1% (SCN-003).
5. **Traffic Denominator Requirement:** Rate and error rate calculations require at least 20 business requests within the 60-second observation window. Probes (`/health/*`) are excluded from business traffic. If traffic is absent, status is `UNKNOWN`, which cannot satisfy verification.
6. **Sample Freshness:** Telemetry samples older than 30 seconds are rejected as stale.
7. **Alert Clearance:** Target Prometheus alerts (`ContainerDown`, `HighCpuSaturation`, `ApiUnresponsive`) must be evaluated as non-firing.

## Consequences
- **Positive:** Guarantees that incidents are resolved only when true operational health is restored.
- **Positive:** Zero tolerance for fabricated success or premature resolution.
- **Negative:** Verification requires a minimum 90-second observation period, increasing acceptance run duration.
