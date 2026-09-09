import multiprocessing
from multiprocessing.synchronize import Event as SyncEvent

from fault_injector.faults.base import BaseFault


def _cpu_worker(stop_event: SyncEvent) -> None:
    while not stop_event.is_set():
        _ = [i * i for i in range(10000)]


class CpuStressFault(BaseFault):
    def __init__(self, scenario_id: str) -> None:
        super().__init__(scenario_id)
        self._processes: list[multiprocessing.Process] = []
        self._stop_event = multiprocessing.Event()

    async def inject(self) -> None:
        self._stop_event.clear()
        self._processes.clear()
        # One CPU is sufficient for the >75%-of-one-core scenario; bound host impact.
        num_cores = 1
        for _ in range(num_cores):
            p = multiprocessing.Process(target=_cpu_worker, args=(self._stop_event,))
            p.daemon = True
            p.start()
            self._processes.append(p)
        self._active = True

    async def clear(self) -> None:
        self._stop_event.set()
        for p in self._processes:
            p.join(timeout=1.0)
            if p.is_alive():
                p.terminate()
        self._processes.clear()
        self._active = False
