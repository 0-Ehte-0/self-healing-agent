# Expected Signals — SCN-002

## Metrics
- **CPU Utilization:** Host/container CPU usage increases above `80%`.
- **Latency:** `histogram_quantile(0.95, sum(rate(demo_api_http_request_duration_seconds_bucket[1m])) by (le)) > 0.50`
- **Request Duration:** Increase in p95 request duration by > 300% over baseline.

## Logs (Loki)
- `{service="demo-api"} | json | latency > 0.5`

## Alerts
- `HighCpuSaturation` (firing if CPU > 75% for > 60s).