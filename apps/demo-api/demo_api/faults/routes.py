import os
import signal
from typing import Any

from demo_api.faults.auth import verify_fault_token
from demo_api.faults.cpu import cpu_manager
from demo_api.faults.hang import hang_manager
from fastapi import APIRouter, Depends, Query

router = APIRouter(prefix="/_faults", tags=["faults"], dependencies=[Depends(verify_fault_token)])


@router.post("/crash")
async def trigger_crash() -> dict[str, str]:
    """SCN-001: Immediately terminates the container process with SIGTERM."""
    # Schedule signal emission on next tick to allow HTTP response flush if possible
    os.kill(os.getpid(), signal.SIGTERM)
    return {"status": "crashing"}


@router.post("/cpu/inject")
async def inject_cpu_stress(cores: int = Query(1, ge=1, le=8)) -> dict[str, Any]:
    """SCN-002: Spawns internal CPU stress child processes bounded to target container."""
    return cpu_manager.start(num_cores=cores)


@router.post("/cpu/clear")
async def clear_cpu_stress() -> dict[str, Any]:
    """SCN-002: Terminates internal CPU stress child processes."""
    return cpu_manager.stop()


@router.get("/cpu/status")
async def cpu_stress_status() -> dict[str, Any]:
    """Returns current status of internal CPU stress workers."""
    return cpu_manager.get_status()


@router.post("/hang/inject")
async def inject_hang() -> dict[str, Any]:
    """SCN-003: Sets process-local hang affecting /health/ready and application endpoints."""
    return hang_manager.inject()


@router.post("/hang/clear")
async def clear_hang() -> dict[str, Any]:
    """SCN-003: Clears process-local hang and cancels blocked sleep tasks."""
    return hang_manager.clear()


@router.get("/hang/status")
async def hang_status() -> dict[str, Any]:
    """Returns current status of process-local hang."""
    return hang_manager.get_status()
