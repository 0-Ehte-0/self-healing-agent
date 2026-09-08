# Verification Requirements — SCN-004

During the 90-second stabilization window:
1. `histogram_quantile(0.95, sum(rate(demo_api_http_request_duration_seconds_bucket[1m])) by (le)) < 0.100`.
2. Redis key `fault:latency` does not exist.
3. No active latency alerts present.