from typing import Any, Protocol
from uuid import UUID


class EvidenceCollectorProtocol(Protocol):
    async def collect(
        self,
        incident_id: UUID,
        resource_id: UUID,
        **kwargs: Any,
    ) -> Any: ...


class DiagnosisEngineProtocol(Protocol):
    async def diagnose(
        self,
        incident_id: UUID,
        obs: Any = None,
        **kwargs: Any,
    ) -> Any: ...


class PlannerProtocol(Protocol):
    async def plan(self, incident_id: UUID, diagnosis_id: UUID) -> dict[str, Any]: ...


class PolicyEngineProtocol(Protocol):
    async def evaluate(self, plan_id: UUID, environment: str = "local") -> dict[str, Any]: ...


class ExecutorProtocol(Protocol):
    async def execute_step(
        self,
        incident_id: UUID,
        step_id: UUID,
        resource_id: UUID,
        attempt: int,
    ) -> dict[str, Any]: ...


class VerifierProtocol(Protocol):
    async def verify(self, execution_id: UUID, resource_id: UUID) -> dict[str, Any]: ...
