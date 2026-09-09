# Expected Signals — SCN-003

## Metrics
- **Readiness Probes:** Blackbox probe timeout or failure on `/health/ready` (`probe_success{job="demo-api-probes",instance=~".*/health/ready"} == 0`).
- **Liveness Probes:** Liveness probe succeeds (`probe_success{job="demo-api-probes",instance=~".*/health/live"} == 1`).
- **5xx / Timeout Rate:** Application endpoints time out or return 503 while hang is active.

## Logs (Loki)
- `{service="demo-api"} |= "Readiness check hanging"`
- `{service="demo-api"} |= "process-local hang active"`

## Alerts
- `ApiUnresponsive` (firing when readiness probe fails for > 30s).