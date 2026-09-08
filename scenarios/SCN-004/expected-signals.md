# Expected Signals — SCN-004

## Metrics
- **p95 Latency:** `histogram_quantile(0.95, sum(rate(demo_api_http_request_duration_seconds_bucket[1m])) by (le)) >= 2.0`
- **Throughput:** Drop in requests per second due to client connection saturation.

## Logs (Loki)
- `{service="demo-api"} | json | latency >= 2.0`

## Alerts
- `ApiHighLatency` (firing when p95 > 1.0s for 1m).