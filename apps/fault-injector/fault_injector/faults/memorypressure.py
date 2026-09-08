import gc

from fault_injector.faults.base import BaseFault


class MemoryPressureFault(BaseFault):
    def __init__(self, scenario_id: str, megabytes: int = 256) -> None:
        super().__init__(scenario_id)
        self.megabytes = megabytes
        self._allocated_chunks: list[bytearray] = []

    async def inject(self) -> None:
        self._allocated_chunks.clear()
        chunk_size = 10 * 1024 * 1024
        chunks_count = max(1, self.megabytes // 10)
        for _ in range(chunks_count):
            self._allocated_chunks.append(bytearray(chunk_size))
        self._active = True

    async def clear(self) -> None:
        self._allocated_chunks.clear()
        gc.collect()
        self._active = False
