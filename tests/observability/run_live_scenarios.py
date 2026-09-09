"""Phase 3 acceptance runner for the local Compose demo (injects reversible faults).

Run explicitly: python tests/observability/run_live_scenarios.py
This checks telemetry and notifications; it does not implement the Phase 20 remediation workflow.
"""

import argparse
import asyncio
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = {
    "SCN-001": "ContainerDown",
    "SCN-002": "HighCpuSaturation",
    "SCN-003": "ApiUnresponsive",
    "SCN-004": "ApiHighLatency",
    "SCN-005": "HighMemoryUsage",
    "SCN-006": "PostgresPoolExhausted",
    "SCN-007": "RedisUnreachable",
    "SCN-008": "WorkerQueueBacklogHigh",
    "SCN-009": "HighHttpErrorRate",
    "SCN-010": "ApplicationLivenessFailed",
}


async def get(client, url, **kwargs):
    response = await client.get(url, **kwargs)
    response.raise_for_status()
    return response.json()


async def query(client, expr):
    return (await get(client, "http://localhost:9090/api/v1/query", params={"query": expr}))[
        "data"
    ]["result"]


async def wait_until(check, label, timeout=240):
    deadline = time.monotonic() + timeout
    last_progress = time.monotonic()
    while time.monotonic() < deadline:
        if await check():
            return
        if time.monotonic() - last_progress >= 30:
            print(f"Waiting for {label} ({deadline - time.monotonic():.0f}s remaining)", flush=True)
            last_progress = time.monotonic()
        await asyncio.sleep(3)
    raise AssertionError(f"Timed out waiting for {label}")


async def traffic(client, scenario, correlation):
    while True:
        try:
            await client.post(
                "http://localhost:8001/jobs",
                json={"payload": "phase3-acceptance"},
                headers={"X-Scenario-ID": scenario, "X-Correlation-ID": correlation},
                timeout=3,
            )
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.5)


async def notification_count(client, alert, scenario, status):
    rows = await query(
        client,
        f'control_plane_alert_notifications_total{{alertname="{alert}",scenario_id="{scenario}",status="{status}"}}',
    )
    return sum(float(row["value"][1]) for row in rows)


async def run_scenario(client, scenario):
    alert = EXPECTED[scenario]
    correlation = str(uuid4())
    headers = {"X-Fault-Token": os.getenv("FAULT_INJECTOR_SECRET", "injector-secret-token")}

    async def clean():
        return not await query(client, 'ALERTS{alertstate="firing"}')

    await wait_until(clean, "healthy alert baseline", 300)
    before_firing = await notification_count(client, alert, scenario, "firing")
    before_resolved = await notification_count(client, alert, scenario, "resolved")
    started = time.time()
    task = asyncio.create_task(traffic(client, scenario, correlation))
    injected = False
    try:
        # Allow a correlated trace to flush before the intentional process-crash scenario.
        await asyncio.sleep(8)
        injected = True
        response = await client.post(
            f"http://localhost:8003/faults/{scenario}/inject?ttl_seconds=600",
            headers=headers,
            timeout=45,
        )
        response.raise_for_status()
        if response.json()["status"] != "injected":
            raise AssertionError(
                "Scenario was already active; acceptance requires an isolated injection"
            )
        print(f"{scenario}: injected; waiting for {alert}", flush=True)
        expr = f'ALERTS{{alertname="{alert}",scenario_id="{scenario}",alertstate="firing"}}'

        async def firing():
            return bool(await query(client, expr))

        await wait_until(firing, f"{scenario} {alert} firing")
        fired_at = time.time()

        async def firing_delivered():
            return await notification_count(client, alert, scenario, "firing") > before_firing

        await wait_until(firing_delivered, "firing webhook")
        metric_rows = await query(
            client, f'max_over_time(demo_api_http_requests_total{{scenario_id="{scenario}"}}[5m])'
        )
        if not metric_rows:
            raise AssertionError("No scenario-correlated request metrics")
        log_data = await get(
            client,
            "http://localhost:3100/loki/api/v1/query_range",
            params={
                "query": f'{{scenario_id="{scenario}",correlation_id="{correlation}"}}',
                "start": str(int(started * 1e9)),
                "limit": 20,
            },
        )
        if not log_data["data"]["result"]:
            raise AssertionError("No scenario/correlation-indexed logs")
        trace_data = await get(
            client,
            "http://localhost:16686/api/traces",
            params={
                "service": "demo-api",
                "tags": json.dumps({"scenario_id": scenario, "correlation_id": correlation}),
                "start": int(started * 1e6),
                "end": int(time.time() * 1e6),
                "limit": 20,
            },
        )
        if not trace_data["data"]:
            raise AssertionError("No scenario-correlated traces")
    finally:
        try:
            if injected:
                response = await client.post(
                    f"http://localhost:8003/faults/{scenario}/clear", headers=headers, timeout=45
                )
                response.raise_for_status()
        finally:
            if scenario == "SCN-001":
                subprocess.run(
                    ["docker", "compose", "start", "demo-api"],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                )
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    print(f"{scenario}: cleared; waiting for resolution notification", flush=True)

    async def resolved():
        return (
            not await query(client, expr)
            and await notification_count(client, alert, scenario, "resolved") > before_resolved
        )

    await wait_until(resolved, f"{scenario} resolved webhook", 360)
    await wait_until(clean, "all alerts reset", 360)
    return {
        "scenario_id": scenario,
        "alert": alert,
        "correlation_id": correlation,
        "injected_at": started + 8,
        "fired_at": fired_at,
        "resolved_at": time.time(),
        "metrics": metric_rows,
        "logs": log_data["data"]["result"],
        "trace_ids": [trace["traceID"] for trace in trace_data["data"]],
        "firing_webhook": True,
        "resolved_webhook": True,
        "passed": True,
    }


async def main(scenarios, output):
    report = {
        "source": "live_compose",
        "started_at": datetime.now(UTC).isoformat(),
        "results": [],
        "passed": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for scenario in scenarios:
                report["results"].append(await run_scenario(client, scenario))
                output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        report["passed"] = True
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", nargs="+", choices=list(EXPECTED), default=list(EXPECTED))
    parser.add_argument(
        "--output", type=Path, default=ROOT / "evaluation/results/phase3-observability.json"
    )
    args = parser.parse_args()
    asyncio.run(main(args.scenarios, args.output))
