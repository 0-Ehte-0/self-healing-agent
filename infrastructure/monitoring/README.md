# Observability Layer (Phase 3)

The Observability Layer provides unified metrics, logs, traces, and automated alerting for the Autonomous Cloud Infrastructure Self-Healing Agent.

## Architecture

```text
               +---------------------------------------------+
               |                 Grafana                     |
               | (API Health, Resources, Incident Demo)      |
               +----------------------+----------------------+
                                      |
       +------------------------------+------------------------------+
       |                              |                              |
       v                              v                              v
+---------------+             +---------------+             +---------------+
|  Prometheus   |             |     Loki      |             |    Jaeger     |
| (Metrics &    |             | (Structured   |             | (Distributed  |
|  Alert Rules) |             |  Logs)        |             |  Traces)      |
+-------+-------+             +-------+-------+             +-------+-------+
        |                             |                             |
        |                             v                             ^
        |                       Fluent-bit                          |
        |                  (Container Log Shipper)                  |
        |                             ^                             |
        v                             |                     OTel Collector
   Alertmanager ----------------------+                     (4317 / 4318)
        | (Webhook)                   |                             ^
        v                             +--------------+              |
  Control-Plane                                      |              |
 (/webhooks/alertmanager)                            |              |
        ^                                            |              |
        |                                            |              |
+-------+--------------------------------------------+--------------+-------+
|  Workloads: demo-api, demo-worker, traffic-generator, fault-injector      |
+---------------------------------------------------------------------------+
```

## Scrape Targets (15s Interval)

| Target | Port / Path | Tier | Responsibilities |
| :--- | :--- | :--- | :--- |
| `demo-api` | `8000/metrics` | Workload | HTTP RPS, latency percentiles, error rates, DB pool connections |
| `demo-worker` | `9102/metrics` | Workload | Heartbeat timestamps, Redis Stream queue depth |
| `control-plane` | `8000/metrics` | Management | Ingestion rates, state transitions, API health |
| `node-exporter` | `9100/metrics` | Infrastructure | Host CPU, memory, filesystem, network I/O |
| `cadvisor` | `8080/metrics` | Infrastructure | Per-container CPU, resident memory working set |
| `prometheus` | `9090/metrics` | Monitoring | Scrape duration, TSDB head series, rule evaluation |

## Alert Rules & Scenario Coverage

### 1. Containers (`alerts/containers.yml`)
- **`ContainerDown`**: Fires when `up{job=~"demo-api|demo-worker"} == 0` for 15s. Maps to **SCN-001 (Container Crash)**.
- **`HighCpuSaturation`**: Fires when measured CPU consumption exceeds 75% of one core for 30s. SCN-002 currently runs one bounded CPU worker in the fault-injector; its metric includes child-process CPU time.
- **`HighMemoryUsage`**: Fires when the injector's measured resident memory exceeds 220 MiB for 30s. SCN-005 allocates memory in that container.

### 2. API (`alerts/api.yml`)
- **`ApiUnresponsive`**: Fires when the independent Blackbox readiness probe fails for 30s. The probe runs every 15s and has a 2s timeout, so a hung handler cannot leave a healthy signal indefinitely. Maps to **SCN-003 (Unresponsive API)**.
- **`ApiHighLatency`**: Fires when p95 latency > 1s for 30s. Maps to **SCN-004 (Latency Injection)**.
- **`PostgresPoolExhausted`**: Fires when the fault-injector holds at least eight actual leaked database connections for 15s. Maps to **SCN-006 (DB Connection Leak)**. The demo API also exports actual pool checkout counts for its dashboard.
- **`RedisUnreachable`**: Fires when Redis connectivity fails for 15s. Maps to **SCN-007 (Redis Outage)**.
- **`HighHttpErrorRate`**: Fires when HTTP 5xx error rate > 5% for 15s. Maps to **SCN-009 (Elevated Error Rate)**.
- **`ApplicationLivenessFailed`**: Fires when `/health/live` probe fails. Maps to **SCN-010 (Bad Deployment)**.
- **`ApplicationReadinessFailed`**: Fires when `/health/ready` probe fails. Maps to **SCN-010 (Bad Deployment)**.

### 3. Worker (`alerts/worker.yml`)
- **`WorkerQueueBacklogHigh`**: Fires when consumer-group lag plus pending unacknowledged entries exceeds 50 for 30s. Acknowledged stream history does not count. Maps to **SCN-008 (Worker Pause)**.
- **`WorkerHeartbeatMissing`**: Fires when worker heartbeat lags > 15s. Maps to worker stall.

## Alertmanager Routing

Alertmanager is configured to group alerts by `['alertname', 'scenario_id', 'service']` and forward webhooks to:
```text
http://control-plane:8000/api/v1/webhooks/alertmanager
```
with automated resolution forwarding (`send_resolved: true`).

## Operational Dashboards (Grafana)

- **API Health (`api-health.json`)**: Request rate (RPS), HTTP 5xx error percentage, latency percentiles (p50, p90, p95, p99), active DB pool connections, service availability.
- **Resources (`resources.json`)**: Container and process CPU utilization, memory working set, Redis stream queue depth, worker heartbeat lag.
- **Incident Demo (`incident-demo.json`)**: Active firing alerts table, scenario ingress rates, structured Loki logs, and real-time composite health score:
  $$H = 0.35 A + 0.35 (1 - E) + 0.30 (1 - R)$$

## Baseline Telemetry Dataset

Located under `data/baseline/`:
- `data/baseline/metrics/baseline_metrics.json`: Measured time series samples, requiring healthy scrape targets and probes, error rate at most 2%, p95 at most 1s, queue depth at most 10, and no firing alerts.
- `data/baseline/logs/baseline_logs.jsonl.gz`: Losslessly compressed actual Loki workload log entries from the capture interval.
- `data/baseline/manifest.json`: Versioned manifest recording SHA-256 checksums and record counts.
- `data/baseline/capture_baseline.py`: Real capture script with a minimum 60s duration; missing/unhealthy telemetry fails the run. Default duration is 120s; `--seconds 86400` captures a full day. The manifest records the actual duration, source URLs, checksums and sample counts. There is no synthetic fallback.

## Log coverage and correlation

Every Compose service uses the JSON-file logging driver with Compose project/service attributes. Fluent Bit tails container logs from the Docker VM and filters them to this Compose project before forwarding; unrelated local containers are excluded. It stores tail offsets in a named volume and ignores files older than one day. Loki indexes service, container ID, scenario ID, correlation ID and level. The demo API supplies scenario/correlation IDs on logs and trace spans and propagates them to worker jobs. Infrastructure logs have `BASELINE`/`none` where those IDs are not applicable.

## Reproduce Phase 3 acceptance

```powershell
docker compose up -d --build
docker compose exec -T prometheus promtool check config /etc/prometheus/prometheus.yml
docker compose exec -T alertmanager amtool check-config /etc/alertmanager/alertmanager.yml
.\.venv\Scripts\python.exe data/baseline/capture_baseline.py --seconds 120
.\.venv\Scripts\python.exe tests/observability/run_live_scenarios.py
```

Let the stack warm up for at least a minute before capturing rates. The live runner injects each of the ten reversible faults, checks its expected firing alert, confirms webhook receipt via control-plane counters, checks correlated metrics/logs/traces, clears the fault, and waits for alert resolution and its webhook. SCN-001 restarts only the demo API service after its intentional crash. Each scenario is cleared in a `finally` block; do not run concurrent fault suites. The runner writes measured results to `evaluation/results/phase3-observability.json`, including failure details if a check fails. `--scenarios SCN-001` runs a selected subset. Overlapping symptoms may produce additional alerts.

This is observability acceptance. Incident ingestion/deduplication belongs to Phase 5, telemetry-based remediation verification to Phase 16, and the complete remediation lifecycle harness to Phase 20.
