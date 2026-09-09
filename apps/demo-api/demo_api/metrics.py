from prometheus_client import Counter, Gauge, Histogram

# Counts incoming HTTP requests by method, endpoint, status, and fault scenario.
# Helps the control plane detect traffic changes and calculate request error rates.
HTTP_REQUESTS_TOTAL = Counter(
    "demo_api_http_requests_total",
    "Total incoming HTTP requests",
    ["method", "endpoint", "status", "scenario_id"],
)

# Records HTTP request latency in predefined buckets, enabling latency
# percentile calculations and detection of slow or degraded API responses.
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "demo_api_http_request_duration_seconds",
    "HTTP request latency histogram in seconds",
    ["method", "endpoint", "scenario_id"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# Tracks the number of database connections currently checked out from the pool.
# Helps identify connection pressure, pool exhaustion, and database-related faults.
ACTIVE_DB_CONNECTIONS = Gauge(
    "demo_api_db_connections_active",
    "Active database pool connections",
)

# Tracks application liveness status (1 = healthy/live, 0 = unhealthy/failed).
HEALTH_LIVE_STATUS = Gauge(
    "demo_api_health_live_status",
    "Application liveness status",
)

# Tracks application readiness status (1 = ready, 0 = unready).
HEALTH_READY_STATUS = Gauge(
    "demo_api_health_ready_status",
    "Application readiness status",
)

# Tracks Redis connectivity from demo-api (1 = connected, 0 = unreachable).
REDIS_CONNECTED = Gauge(
    "demo_api_redis_connected",
    "Redis connection status",
)

# Monotonically increasing Counter for demo-api CPU seconds (including child processes)
DEMO_API_CPU_SECONDS_TOTAL = Counter(
    "demo_api_cpu_seconds_total",
    "Total CPU seconds consumed by demo-api and child processes",
)

# Configured CPU budget cores for demo-api container
DEMO_API_CPU_BUDGET_CORES = Gauge(
    "demo_api_cpu_budget_cores",
    "Configured CPU core budget for demo-api",
)
