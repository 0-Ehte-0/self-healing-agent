# Expected Signals — SCN-002

## Metrics
- **CPU Utilization:** Monotonically increasing `demo_api_cpu_seconds_total` Counter rate divided by `cpu_budget_cores` increases above `75%`:
  $$\frac{\text{rate}(\text{demo\_api\_cpu\_seconds\_total}[1\text{m}])}{\text{cpu\_budget\_cores}} > 0.75$$
- **Latency:** `histogram_quantile(0.95, sum(rate(demo_api_http_request_duration_seconds_bucket[1m])) by (le)) > 0.50`
- **Request Duration:** Increase in p95 request duration by > 300% over recorded healthy baseline band.

## Logs (Loki)
- `{service="demo-api"} | json | latency > 0.5`
- `{service="demo-api"} |= "CPU stress active"`

## Alerts
- `HighCpuSaturation` (firing when CPU saturation > 75% for > 30s).