# Expected Signals — SCN-001

## Metrics
- **Prometheus Probe:** `up{job="demo-api"} == 0`
- **HTTP Error Rate:** `rate(demo_api_http_requests_total{status=~"5.."}[1m])` drops to 0 while traffic client reports connection refused.
- **Traffic Generator:** `httpx.ConnectError` spikes.

## Logs (Loki)
- `{service="traffic-generator"} |= "ConnectError"`
- `{service="demo-api"} |= "Application shutdown"`

## Alerts
- `ContainerDown` (firing immediately on probe failure).