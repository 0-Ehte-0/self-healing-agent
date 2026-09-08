# Expected Signals — SCN-006

## Metrics
- **Postgres Active Connections:** `pg_stat_activity` count reaches configured max pool limit.
- **DB Connection Timeout:** `demo-api` reports timeout acquiring connection from pool.
- **Readiness Failure:** `/health/ready` fails dependency probe on `database`.

## Logs (Loki)
- `{service="demo-api"} |= "QueuePool limit of size"`

## Alerts
- `PostgresPoolExhausted` or `DatabaseConnectionTimeout`.