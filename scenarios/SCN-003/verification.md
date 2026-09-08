# Verification Requirements — SCN-003

During the 90-second stabilization window:
1. `/health/ready` returns HTTP 200 within 200ms for three consecutive checks.
2. p95 latency is below 0.200s.
3. 5xx error rate drops below 1%.
4. Readiness failure alerts are cleared.