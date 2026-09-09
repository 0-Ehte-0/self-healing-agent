import asyncio
import os
import time
from typing import Any

import psutil
from fastapi import Depends, FastAPI, HTTPException, Response, status
from fault_injector.auth import verify_fault_token
from fault_injector.config import get_settings
from fault_injector.faults.baddeployment import BadDeploymentFault
from fault_injector.faults.base import BaseFault
from fault_injector.faults.cpustress import CpuStressFault
from fault_injector.faults.crash import ProcessCrashFault
from fault_injector.faults.dbleak import DbConnectionLeakFault
from fault_injector.faults.errorrate import ElevatedErrorRateFault
from fault_injector.faults.healthhang import HealthHangFault
from fault_injector.faults.latency import LatencyFault
from fault_injector.faults.memorypressure import MemoryPressureFault
from fault_injector.faults.redisdisconnect import RedisDisconnectFault
from fault_injector.faults.workerpause import WorkerPauseFault
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from pydantic import BaseModel

settings = get_settings()
app = FastAPI(title="fault-injector")
CPU_SECONDS = Gauge(
    "fault_injector_cpu_seconds_total", "CPU seconds including fault child processes"
)
DB_CONNECTIONS = Gauge(
    "fault_injector_db_connections_active", "Actual connections held by the database leak scenario"
)


@app.get("/metrics")
async def metrics() -> Response:
    process = psutil.Process(os.getpid())
    times = process.cpu_times()
    cpu = times.user + times.system
    for child in process.children(recursive=True):
        try:
            child_times = child.cpu_times()
            cpu += child_times.user + child_times.system
        except psutil.NoSuchProcess:
            pass
    CPU_SECONDS.set(cpu)
    fault = ACTIVE_FAULTS.get("SCN-006")
    DB_CONNECTIONS.set(len(fault._connections) if isinstance(fault, DbConnectionLeakFault) else 0)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


FAULT_REGISTRY: dict[str, type[BaseFault]] = {
    "SCN-001": ProcessCrashFault,
    "SCN-002": CpuStressFault,
    "SCN-003": HealthHangFault,
    "SCN-004": LatencyFault,
    "SCN-005": MemoryPressureFault,
    "SCN-006": DbConnectionLeakFault,
    "SCN-007": RedisDisconnectFault,
    "SCN-008": WorkerPauseFault,
    "SCN-009": ElevatedErrorRateFault,
    "SCN-010": BadDeploymentFault,
}


class FaultRecord(BaseModel):
    scenario_id: str
    injected_at: float
    ttl_seconds: int


ACTIVE_FAULTS: dict[str, BaseFault] = {}
FAULT_TIMERS: dict[str, asyncio.Task] = {}
FAULT_METADATA: dict[str, FaultRecord] = {}


async def _auto_clear_job(scenario_id: str, ttl: int) -> None:
    try:
        await asyncio.sleep(ttl)
        if scenario_id in ACTIVE_FAULTS:
            await ACTIVE_FAULTS[scenario_id].clear()
            ACTIVE_FAULTS.pop(scenario_id, None)
            FAULT_METADATA.pop(scenario_id, None)
    except asyncio.CancelledError:
        pass


@app.post("/faults/{scenario_id}/inject", dependencies=[Depends(verify_fault_token)])
async def inject_fault(scenario_id: str, ttl_seconds: int = 600) -> dict[str, Any]:
    if scenario_id not in FAULT_REGISTRY:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown scenario ID: {scenario_id}",
        )

    if scenario_id in ACTIVE_FAULTS:
        return {"status": "already_active", "scenario_id": scenario_id}

    fault_instance = FAULT_REGISTRY[scenario_id](scenario_id=scenario_id)
    await fault_instance.inject()
    ACTIVE_FAULTS[scenario_id] = fault_instance
    FAULT_METADATA[scenario_id] = FaultRecord(
        scenario_id=scenario_id,
        injected_at=time.time(),
        ttl_seconds=ttl_seconds,
    )

    task = asyncio.create_task(_auto_clear_job(scenario_id, ttl_seconds))
    FAULT_TIMERS[scenario_id] = task

    return {"status": "injected", "scenario_id": scenario_id, "ttl_seconds": ttl_seconds}


@app.post("/faults/{scenario_id}/clear", dependencies=[Depends(verify_fault_token)])
async def clear_fault(scenario_id: str) -> dict[str, Any]:
    if scenario_id not in ACTIVE_FAULTS:
        return {"status": "not_active", "scenario_id": scenario_id}

    if scenario_id in FAULT_TIMERS:
        FAULT_TIMERS[scenario_id].cancel()
        FAULT_TIMERS.pop(scenario_id, None)

    await ACTIVE_FAULTS[scenario_id].clear()
    ACTIVE_FAULTS.pop(scenario_id, None)
    FAULT_METADATA.pop(scenario_id, None)

    return {"status": "cleared", "scenario_id": scenario_id}


@app.get("/faults/active", dependencies=[Depends(verify_fault_token)])
async def list_active_faults() -> list[FaultRecord]:
    return list(FAULT_METADATA.values())
