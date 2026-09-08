# Expected Signals — SCN-003

## Metrics
- **Readiness Probes:** Client timeout on `/health/ready` (> 5s timeout).
- **5xx / Timeout Rate:** 5xx response code increase or connection timeout metrics.

## Logs (Loki)
- `{service="demo-api"} |= "readiness probe timeout"`

## Alerts
- `ApiUnresponsive` or `ReadinessProbeFailing`.