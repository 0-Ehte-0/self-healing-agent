# Expected Signals — SCN-009

## Metrics
- **5xx Rate:** `sum(rate(demo_api_http_requests_total{status="500"}[1m])) / sum(rate(demo_api_http_requests_total[1m])) >= 0.95`
- **Job Creations:** Drops to 0 successful transactions.

## Logs (Loki)
- `{service="demo-api"} |= "Simulated elevated business error"`

## Alerts
- `HighHttpErrorRate` (firing when error rate > 5%).