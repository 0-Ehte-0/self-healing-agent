import logging
import multiprocessing
import os
import time
from multiprocessing.synchronize import Event as SyncEvent

import psutil
from demo_api.config import get_settings
from demo_api.metrics import DEMO_API_CPU_BUDGET_CORES, DEMO_API_CPU_SECONDS_TOTAL

logger = logging.getLogger(__name__)


def _cpu_busy_loop(stop_event: SyncEvent) -> None:
    """Tight arithmetic loop executing in a child process."""
    while not stop_event.is_set():
        _ = [i * i for i in range(10000)]


class CpuStressManager:
    """Manages container-internal CPU stress child processes for SCN-002."""

    def __init__(self) -> None:
        self._processes: list[multiprocessing.Process] = []
        self._stop_event = multiprocessing.Event()
        self._active: bool = False
        self._injected_at: float | None = None
        self._last_cpu_sample: float = 0.0
        self._initialized: bool = False

    def is_active(self) -> bool:
        # Clean up any dead processes
        self._processes = [p for p in self._processes if p.is_alive()]
        if not self._processes and self._active:
            self._active = False
        return self._active

    def get_status(self) -> dict:
        return {
            "active": self.is_active(),
            "injected_at": self._injected_at,
            "worker_processes": len(self._processes),
        }

    def start(self, num_cores: int = 1) -> dict:
        if self.is_active():
            return {"status": "already_active", **self.get_status()}

        self._stop_event.clear()
        self._processes.clear()
        for _ in range(max(1, num_cores)):
            p = multiprocessing.Process(target=_cpu_busy_loop, args=(self._stop_event,))
            p.daemon = True
            p.start()
            self._processes.append(p)

        self._active = True
        self._injected_at = time.time()
        logger.info(f"Started {len(self._processes)} CPU stress worker(s) inside demo-api container")
        return {"status": "started", **self.get_status()}

    def stop(self) -> dict:
        if not self._processes and not self._active:
            return {"status": "not_active"}

        self._stop_event.set()
        for p in self._processes:
            p.join(timeout=1.0)
            if p.is_alive():
                p.terminate()
        self._processes.clear()
        self._active = False
        self._injected_at = None
        logger.info("Stopped all CPU stress workers inside demo-api")
        return {"status": "stopped"}

    def update_metrics(self) -> None:
        """Collects CPU seconds from demo-api and child processes, incrementing the Prometheus Counter."""
        settings = get_settings()
        DEMO_API_CPU_BUDGET_CORES.set(settings.cpu_budget_cores)

        try:
            current_pid = os.getpid()
            process = psutil.Process(current_pid)
            times = process.cpu_times()
            total_cpu = times.user + times.system

            for child in process.children(recursive=True):
                try:
                    ctimes = child.cpu_times()
                    total_cpu += ctimes.user + ctimes.system
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            if not self._initialized:
                # Initialize base count
                self._last_cpu_sample = total_cpu
                DEMO_API_CPU_SECONDS_TOTAL.inc(total_cpu)
                self._initialized = True
            else:
                delta = total_cpu - self._last_cpu_sample
                if delta > 0:
                    DEMO_API_CPU_SECONDS_TOTAL.inc(delta)
                    self._last_cpu_sample = total_cpu
        except Exception as e:
            logger.warning(f"Error collecting CPU metrics: {e}")


cpu_manager = CpuStressManager()
