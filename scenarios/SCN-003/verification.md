# Verification Requirements — SCN-003

During the 90-second post-remediation stabilization window (7 consecutive evaluations at 15-second intervals):
1. Target binding matches current `docker_container_id` and `binding_generation`.
2. Container status is `running`.
3. `/health/ready` returns HTTP 200 within 200ms for three consecutive checks prior to stabilization timer start.
4. p95 latency is below 0.200s on business route `/jobs`.
5. 5xx error rate drops and remains below 1% with $\ge 20$ requests per 60-second window.
6. `ApiUnresponsive` and readiness failure alerts evaluate to non-firing in Prometheus.