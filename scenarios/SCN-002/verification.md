# Verification Requirements — SCN-002

During the 90-second post-remediation stabilization window (7 consecutive evaluations at 15-second intervals):
1. Target binding matches current `docker_container_id` and `binding_generation`.
2. Container status is `running`.
3. `/health/ready` returns HTTP 200 within 200ms for three consecutive checks prior to stabilization timer start.
4. CPU saturation $\frac{\text{rate}(\text{demo\_api\_cpu\_seconds\_total}[1\text{m}])}{\text{cpu\_budget\_cores}}$ drops and remains below `75%` across two consecutive 30-second windows.
5. p95 request latency returns below SLO (`< 0.200s`) on business route `/jobs`.
6. API success rate returns to baseline band (`> 98%`) with $\ge 20$ requests per 60-second window.
7. `HighCpuSaturation` alert evaluates to non-firing in Prometheus.