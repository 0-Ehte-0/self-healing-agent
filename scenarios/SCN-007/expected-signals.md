# Expected Signals — SCN-007

## Metrics
- **Redis Ping:** Fails with timeout across `demo-api` and `demo-worker`.
- **API Readiness:** `/health/ready` returns 503 (`redis: false`).
- **Worker Heartbeat:** `demo_worker_heartbeat_seconds` stops updating.

## Logs (Loki)
- `{service="demo-api"} |= "ConnectionTimeoutError"`
- `{service="demo-worker"} |= "Error in consumer loop"`

## Alerts
- `RedisUnreachable` / `ServiceDependencyDown`.