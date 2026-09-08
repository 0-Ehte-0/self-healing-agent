from abc import ABC, abstractmethod


class BaseFault(ABC):
    def __init__(self, scenario_id: str) -> None:
        self.scenario_id = scenario_id
        self._active: bool = False

    @abstractmethod
    async def inject(self) -> None:
        pass

    @abstractmethod
    async def clear(self) -> None:
        pass

    def is_active(self) -> bool:
        return self._active
