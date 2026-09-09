"""Capture measured healthy telemetry: --seconds 120 (compressed) or 86400 (full day)."""

import argparse
import gzip
import hashlib
import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parent
REQUIRED_JOBS = {
    "demo-api",
    "demo-worker",
    "control-plane",
    "node-exporter",
    "cAdvisor",
    "fault-injector",
    "demo-api-probes",
}
QUERIES = {
    "up": "up",
    "availability": 'probe_success{job="demo-api-probes"}',
    "error_rate": 'sum(rate(demo_api_http_requests_total{status=~"5..",endpoint="/jobs"}[1m])) / sum(rate(demo_api_http_requests_total{endpoint="/jobs"}[1m])) or vector(0)',
    "p95": 'histogram_quantile(0.95, sum by(le)(rate(demo_api_http_request_duration_seconds_bucket{endpoint="/jobs"}[1m])))',
    "queue_depth": "demo_worker_queue_depth",
    "alerts": 'ALERTS{alertstate="firing"}',
}


def validate_sample(sample: dict) -> None:
    up = sample["up"]
    jobs = {row["metric"]["job"] for row in up}
    if not REQUIRED_JOBS.issubset(jobs) or any(float(row["value"][1]) != 1 for row in up):
        raise ValueError("Missing or unhealthy scrape targets")
    for name, maximum in [("error_rate", 0.02), ("p95", 1.0), ("queue_depth", 10)]:
        rows = sample[name]
        if not rows:
            raise ValueError(f"Missing {name} telemetry")
        for row in rows:
            value = float(row["value"][1])
            if not math.isfinite(value) or value < 0 or value > maximum:
                raise ValueError(f"Unhealthy {name}: {value}")
    if len(sample["availability"]) != 2 or any(
        float(row["value"][1]) != 1 for row in sample["availability"]
    ):
        raise ValueError("Health probes are not both passing")
    if sample["alerts"]:
        raise ValueError("Firing alerts prevent healthy baseline capture")


def get_json(client: httpx.Client, url: str, **kwargs) -> dict:
    response = client.get(url, **kwargs)
    response.raise_for_status()
    return response.json()


def capture(seconds: int, interval: int, output: Path, prometheus: str, loki: str) -> dict:
    if seconds < 60 or interval < 1 or interval > seconds / 4:
        raise ValueError("Capture at least 60 seconds with at least five samples")
    started = time.time()
    samples = []
    with httpx.Client(timeout=15) as client:
        while True:
            sample = {"observed_at": time.time()}
            for name, query in QUERIES.items():
                response = get_json(client, f"{prometheus}/api/v1/query", params={"query": query})
                if response.get("status") != "success":
                    raise ValueError(f"Prometheus query failed: {name}")
                sample[name] = response["data"]["result"]
            validate_sample(sample)
            samples.append(sample)
            elapsed = time.time() - started
            print(f"Healthy baseline: {elapsed:.0f}/{seconds}s, {len(samples)} samples", flush=True)
            if elapsed >= seconds:
                break
            time.sleep(min(interval, seconds - elapsed))
        ended = time.time()
        logs = []
        cursor = int(started * 1e9)
        end_ns = int(ended * 1e9)
        while cursor <= end_ns:
            response = get_json(
                client,
                f"{loki}/loki/api/v1/query_range",
                params={
                    "query": '{service=~"demo-api|demo-worker"}',
                    "start": str(cursor),
                    "end": str(end_ns),
                    "limit": 5000,
                    "direction": "forward",
                },
            )
            page = [
                {"labels": stream["stream"], "timestamp_ns": stamp, "line": line}
                for stream in response["data"]["result"]
                for stamp, line in stream["values"]
            ]
            logs.extend(page)
            if len(page) < 5000:
                break
            latest = max(int(item["timestamp_ns"]) for item in page)
            if latest <= cursor:
                raise ValueError("Log page boundary exceeds limit; reduce capture traffic")
            cursor = latest
        logs = list({(item["timestamp_ns"], item["line"]): item for item in logs}.values())
        if {item["labels"].get("service") for item in logs} != {"demo-api", "demo-worker"}:
            raise ValueError("Both workload log streams are required")
        if any(item["labels"].get("level") in {"ERROR", "CRITICAL"} for item in logs):
            raise ValueError("Workload errors found during baseline")
    artifacts = {
        "metrics": (
            "metrics/baseline_metrics.json",
            json.dumps({"samples": samples, "queries": QUERIES}, indent=2),
            len(samples),
        ),
        "logs": (
            "logs/baseline_logs.jsonl.gz",
            "".join(json.dumps(item) + "\n" for item in logs),
            len(logs),
        ),
    }
    files = {}
    for key, (relative, content, count) in artifacts.items():
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = content.encode("utf-8")
        target.write_bytes(gzip.compress(encoded, mtime=0) if target.suffix == ".gz" else encoded)
        files[key] = {
            "path": relative,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "record_count": count,
            "size_bytes": target.stat().st_size,
        }
    manifest = {
        "manifest_version": "2.0",
        "source": "live_capture",
        "scenario_id": "BASELINE",
        "system_status": "HEALTHY",
        "started_at": datetime.fromtimestamp(started, UTC).isoformat(),
        "ended_at": datetime.fromtimestamp(ended, UTC).isoformat(),
        "duration_seconds": ended - started,
        "sample_interval_seconds": interval,
        "prometheus_url": prometheus,
        "loki_url": loki,
        "files": files,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--output", type=Path, default=BASE_DIR)
    parser.add_argument("--prometheus", default="http://localhost:9090")
    parser.add_argument("--loki", default="http://localhost:3100")
    args = parser.parse_args()
    capture(args.seconds, args.interval, args.output, args.prometheus, args.loki)
