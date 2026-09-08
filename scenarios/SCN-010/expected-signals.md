# Expected Signals — SCN-010

## Metrics
- **Liveness Failures:** `/health/live` returns HTTP 500.
- **Readiness Failures:** `/health/ready` returns HTTP 500.
- **Container Health Gauge:** Container transitions from healthy to unhealthy.

## Logs (Loki)
- `{service="demo-api"} |= "Deployment configuration invalid"`

## Alerts
- `ApplicationLivenessFailed` and `ApplicationReadinessFailed`.