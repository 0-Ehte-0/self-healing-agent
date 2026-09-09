# M1 Entry-Check Audit Report

**Date:** 2026-09-10  
**Status:** PASSED  
**Milestone:** M1 — Deterministic Docker Vertical Slice  
**Specification Reference:** `autonomous_cloud_self_healing_agent_M1_plan.md` (Section 5)

---

## 1. Environment and Runtime Baseline

| Component | Verified Specification |
| :--- | :--- |
| **Git Branch** | `main` |
| **Git Working Tree** | Clean (`nothing to commit, working tree clean`) |
| **Baseline Commit SHA** | `1ad53ff` ("Phase 5 Implemented") |
| **Python Runtime** | `Python 3.12.2 (win32)` |
| **Virtual Environment** | `c:\dev\self-healing-agent\.venv` |
| **Docker Engine** | `Docker version 29.2.1, build a5c7197` |
| **Docker Compose** | `Docker Compose version v5.1.0` |

---

## 2. Baseline Data Provenance

| Artifact | Specification / Checksum |
| :--- | :--- |
| **Baseline Manifest Path** | `data/baseline/manifest.json` |
| **Manifest Version** | `2.0` |
| **Scenario ID** | `BASELINE` |
| **Capture Start Time** | `2026-09-09T14:54:43.189647+00:00` |
| **Capture End Time** | `2026-09-09T14:55:43.215643+00:00` |
| **Capture Duration** | `60.026s` |
| **Manifest SHA-256** | `f79b6b26035847a6b2cc065fe78a990f88a960b39c2f851e0ba789d5245239c3` |
| **Metrics Data File** | `data/baseline/metrics/baseline_metrics.json` (`a0803642b605379dc13af65e513d59075fb4c0e87c08db6a691ea78101cdb94e`) |
| **Logs Data File** | `data/baseline/logs/baseline_logs.jsonl.gz` (`1cb106a3eb9d31a33a76b20781f03883f79a105c10aa71d31985ea62b18fe854`) |

---

## 3. Test Execution Baselines

All available non-destructive unit, ingestion, and observability suites were executed:

| Test Suite | Command | Result | Notes |
| :--- | :--- | :--- | :--- |
| **Unit Tests** | `pytest tests/unit` | **11 PASSED** (0.04s) | State machine transitions verified |
| **Alertmanager Ingestion** | `pytest tests/integration/ingestion/test_alertmanager_ingestion.py` | **7 PASSED** (6.68s) | Auth, deduplication, redaction, correlation |
| **Generic Event Ingestion** | `pytest tests/integration/ingestion/test_generic_events.py` | **4 PASSED** (0.42s) | Ingestion contract compliance |
| **Database Integration** | `pytest tests/integration/test_phase4_database.py` | **8 SKIPPED** | Skipped awaiting live `TEST_DATABASE_URL` |
| **Observability Configs** | `pytest tests/observability/test_observability_configs.py` | **6 PASSED** (0.05s) | Config validation |
| **Prometheus Alerts** | `pytest tests/observability/test_prometheus_alerts.py` | **4 PASSED** (0.08s) | Rule syntax and scenario coverage |
| **Baseline Telemetry** | `pytest tests/observability/test_baseline_telemetry.py` | **8 PASSED** (0.08s) | Telemetry metrics validation |
| **Grafana Dashboards** | `pytest tests/observability/test_grafana_dashboards.py` | **2 PASSED** (0.08s) | Dashboard JSON integrity |

**Total passing tests:** 42 passed, 8 skipped.

---

## 4. Compose Stack Runtime Health

All 18 Docker Compose services are active and reachable:

| Service | Container Name | Port Mappings | Verified Health |
| :--- | :--- | :--- | :--- |
| `demo-api` | `self-healing-demo-api` | `8001:8000` | HTTP 200 on `/health/live` & `/health/ready` |
| `control-plane` | `self-healing-control-plane` | `8088:8000` | HTTP 200 on `/health/live` |
| `fault-injector` | `self-healing-fault-injector` | `8003:8000` | Active, token auth operational |
| `prometheus` | `self-healing-prometheus` | `9090:9090` | Up, scraping targets every 15s |
| `alertmanager` | `self-healing-alertmanager` | `9093:9093` | Healthy |
| `postgres` | `self-healing-postgres` | `5432:5432` | Healthy (pg_isready) |
| `redis` | `self-healing-redis` | `6379:6379` | Healthy (redis-cli ping) |
| `loki` | `self-healing-loki` | `3100:3100` | Active, accepting logs |
| `fluentbit` | `self-healing-fluentbit` | `24224:24224` | Active |
| `cadvisor` | `self-healing-cadvisor` | `8085:8080` | Healthy |
| `chromadb` | `self-healing-chromadb` | `8000:8000` | Healthy (heartbeat) |
| `node-exporter` | `self-healing-node-exporter` | `9100:9100` | Active |
| `otel-collector` | `self-healing-otel-collector` | `4317, 4318` | Active |
| `traffic-generator` | `self-healing-traffic-generator` | Internal | Active, generating 10 rps background load |
| `demo-worker` | `self-healing-demo-worker` | `8002:8000, 9102:9102`| Active |
| `grafana` | `self-healing-grafana` | `3000:3000` | Active |
| `jaeger` | `self-healing-jaeger` | `16686:16686` | Active |
| `blackbox` | `self-healing-agent-blackbox-1` | `9115` | Probing `/health/*` |

---

## 5. Target Allowlist and Boundaries

To ensure safe and restricted operation:
1. **Allowlisted Managed Target:** `demo-api` (labeled `self-healing.managed=true` via `compose.m1.yml`).
2. **Observable Unmanaged Target:** `demo-worker` (observable via Prometheus/logs, labeled `self-healing.managed=false`).
3. **Prohibited Targets:** All control plane, database, message queue, monitoring, logging, tracing, and infrastructure containers. Any attempt to target these containers will be rejected by policy and execution validation.
