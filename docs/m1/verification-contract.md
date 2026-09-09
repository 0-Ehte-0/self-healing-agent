# M1 Verification Contract and Scenario Profiles

**Version:** 1.0  
**Status:** Frozen  
**Governing ADR:** [ADR-0005](file:///c:/dev/self-healing-agent/docs/decisions/ADR-0005-m1-verification-contract.md)

---

## 1. General Verification Contract

An incident may only transition from `VERIFYING` to `RESOLVED` when independent telemetry confirms that the target service has sustained verified operational health under real traffic for a full stabilization interval.

### 1.1 Stabilization Window Rules
1. **Duration:** 90 continuous seconds.
2. **Sampling Cadence:** Evaluated every 15 seconds.
3. **Minimum Evaluations:** At least 7 consecutive passing evaluation samples spanning 90 elapsed seconds are required.
4. **Reset Semantics:** Any failing check, missing telemetry signal, or stale observation immediately resets the stabilization timer to 0 seconds.
5. **Readiness Prerequisite:** Before the 90-second stabilization timer begins, `/health/ready` must return HTTP 200 with response time < 200ms across 3 consecutive checks (minimum 30 seconds warm-up).

### 1.2 Telemetry Freshness and Traffic Denominator
1. **Sample Freshness:** Telemetry samples must have a timestamp age <= 30 seconds. Stale samples produce `UNKNOWN` and reset the timer.
2. **Traffic Denominator:** Rate and error rate calculations require at least 20 business requests (e.g. `/jobs`) within the 60-second observation window. Probes (`/health/*`) and fault routes (`/_faults/*`) are excluded from business traffic. Zero traffic yields `UNKNOWN`, preventing false-positive resolution.

### 1.3 Composite Health Score (Display Only)
For operator visualization, an aggregate score $H \in [0, 1]$ is computed:
$$H = 0.25 \cdot S_{\text{alert}} + 0.25 \cdot S_{\text{error}} + 0.25 \cdot S_{\text{latency}} + 0.25 \cdot S_{\text{readiness}}$$
where:
- $S_{\text{alert}} = 1.0$ if no scenario alert is firing, else $0.0$.
- $S_{\text{error}} = \max(0.0, 1.0 - \frac{\text{error\_rate}}{\text{threshold}})$.
- $S_{\text{latency}} = \max(0.0, 1.0 - \frac{\text{p95\_latency}}{\text{threshold}})$.
- $S_{\text{readiness}} = 1.0$ if HTTP 200 and latency < 200ms, else $0.0$.

> [!IMPORTANT]
> The composite health score is strictly an explanatory visualization. Every mandatory check must pass individually; a high aggregate score can never override a failing individual check.

---

## 2. Target Scenario Profiles

| Metric / Check | SCN-001 (Crash) | SCN-002 (CPU Saturation) | SCN-003 (Unresponsive API) |
| :--- | :--- | :--- | :--- |
| **Target Binding** | Matches `docker_container_id` & `binding_generation` | Matches `docker_container_id` & `binding_generation` | Matches `docker_container_id` & `binding_generation` |
| **Container Status** | Docker status = `running` | Docker status = `running` | Docker status = `running` |
| **Readiness Probe** | 3 consecutive HTTP 200 checks (< 200ms) | 3 consecutive HTTP 200 checks (< 200ms) | 3 consecutive HTTP 200 checks (< 200ms) |
| **CPU Saturation** | Informational context | $\frac{\text{rate}(\text{demo\_api\_cpu\_seconds\_total}[1\text{m}])}{\text{cpu\_budget\_cores}} < 0.75$ across two consecutive 30s windows | Informational context |
| **p95 Request Latency** | Informational context | p95 latency < 0.200s on `/jobs` | p95 latency < 0.200s on `/jobs` |
| **HTTP Error Rate** | Error rate < 2% | Success rate > 98% (within baseline band) | 5xx error rate < 1% |
| **Prometheus Alerts** | `ContainerDown` NOT firing | `HighCpuSaturation` NOT firing | `ApiUnresponsive` NOT firing |
| **Stabilization Window**| Full 90 seconds (7 samples) | Full 90 seconds (7 samples) | Full 90 seconds (7 samples) |

---

## 3. Recovery Attribution and Fault Expiry Semantics

To prevent false attribution of healing to the agent:
1. If a fault expires automatically (via TTL) or is manually cleared via operator endpoint prior to the agent's restart execution, the incident cannot be resolved as agent-healed.
2. The verification outcome must record:
   - `injected_at`
   - `execution_dispatched_at`
   - `fault_cleared_at` (if manual clear occurred)
   - `fault_expired_at` (if auto-expiry occurred)
3. If recovery occurs without an execution record or after fault expiry, the incident outcome is marked `EXTERNAL_RECOVERY` or `INCONCLUSIVE` and excluded from successful self-healing metrics.
