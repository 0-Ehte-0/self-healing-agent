import logging
from datetime import UTC, datetime, timezone
from typing import Any, Protocol

from telemetryclient.schemas import ContainerInspectionResult

from diagnosis.evidence.redactor import redact_payload

logger = logging.getLogger(__name__)


class ContainerReaderProtocol(Protocol):
    """Protocol for read-only Docker container inspection."""

    async def inspect(
        self, container_id: str, binding_generation: int | None = None
    ) -> ContainerInspectionResult: ...


class DockerContainerReader:
    """Read-only container inspector interfacing with the Docker daemon."""

    def __init__(self, docker_client: Any = None):
        self._docker_client = docker_client

    def _get_client(self) -> Any:
        if self._docker_client is not None:
            return self._docker_client
        try:
            import docker  # type: ignore[import-untyped]

            return docker.from_env()
        except Exception as exc:
            logger.debug(f"Docker client initialization failed: {exc}")
            return None

    async def inspect(
        self, container_id: str, binding_generation: int | None = None
    ) -> ContainerInspectionResult:
        now = datetime.now(UTC)
        client = self._get_client()

        if client is None:
            return ContainerInspectionResult(
                container_id=container_id,
                state="unknown",
                binding_generation=binding_generation,
                observed_at=now,
                status="error",
                error_message="Docker client unavailable in runtime environment",
            )

        try:
            container = client.containers.get(container_id)
            attrs = container.attrs or {}
            raw_state = attrs.get("State", {})
            status_str = raw_state.get("Status", "unknown").lower()
            exit_code = raw_state.get("ExitCode")
            health_obj = raw_state.get("Health", {})
            health_status = health_obj.get("Status") if health_obj else None
            name = attrs.get("Name", "").lstrip("/")

            return ContainerInspectionResult(
                container_id=container_id,
                name=name,
                state=status_str,
                exit_code=exit_code,
                health_status=health_status,
                binding_generation=binding_generation,
                observed_at=now,
                status="success",
            )
        except Exception as exc:
            exc_str = str(exc).lower()
            if "not found" in exc_str or "404" in exc_str:
                return ContainerInspectionResult(
                    container_id=container_id,
                    state="not_found",
                    binding_generation=binding_generation,
                    observed_at=now,
                    status="not_found",
                    error_message=f"Container {container_id} not found",
                )
            logger.warning(f"Error inspecting container {container_id}: {exc}")
            return ContainerInspectionResult(
                container_id=container_id,
                state="unknown",
                binding_generation=binding_generation,
                observed_at=now,
                status="error",
                error_message=str(exc),
            )


class MockContainerReader:
    """Mock container reader for testing."""

    def __init__(self, states: dict[str, ContainerInspectionResult] | None = None):
        self.states = states or {}

    def set_state(self, container_id: str, result: ContainerInspectionResult) -> None:
        self.states[container_id] = result

    async def inspect(
        self, container_id: str, binding_generation: int | None = None
    ) -> ContainerInspectionResult:
        if container_id in self.states:
            res = self.states[container_id]
            if binding_generation is not None:
                res.binding_generation = binding_generation
            return res
        return ContainerInspectionResult(
            container_id=container_id,
            state="not_found",
            binding_generation=binding_generation,
            status="not_found",
        )
