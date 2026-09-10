import logging
import re
from typing import Any

from provideradapters.base import (
    ContainerNotFoundError,
    ContainerSnapshot,
    DockerDaemonUnreachableError,
    DockerTimeoutError,
    ProviderAdapterError,
)

logger = logging.getLogger(__name__)

HEX_CONTAINER_ID_PATTERN = re.compile(r"^[a-fA-F0-9]{12,64}$")

SENSITIVE_KEY_NAMES = {
    "password",
    "passwd",
    "pwd",
    "secret",
    "client_secret",
    "token",
    "auth_token",
    "access_token",
    "refresh_token",
    "session_token",
    "bearer",
    "api_key",
    "apikey",
    "private_key",
    "privkey",
    "credential",
    "credentials",
    "authorization",
}


def redact_labels(labels: dict[str, Any]) -> dict[str, Any]:
    """Redacts any sensitive key-values from container labels."""
    sanitized: dict[str, Any] = {}
    for k, v in labels.items():
        key_str = str(k).lower().replace("-", "_")
        if any(term in key_str for term in SENSITIVE_KEY_NAMES):
            sanitized[k] = "[REDACTED]"
        elif isinstance(v, str) and ("secret" in v.lower() or "token" in v.lower()):
            sanitized[k] = "[REDACTED]"
        else:
            sanitized[k] = v
    return sanitized


class DockerClientWrapper:
    """Restricted Docker client wrapper enforcing timeouts, read-only inspection, and single-container restart."""

    def __init__(self, client: Any = None):
        if client is not None:
            self.client = client
        else:
            try:
                # pyrefly: ignore [untyped-import]
                import docker

                self.client = docker.from_env()
            except Exception as e:
                logger.warning("Docker client initialization failed: %s", e)
                self.client = None

    def _ensure_client(self) -> Any:
        if self.client is None:
            raise DockerDaemonUnreachableError(
                "Docker daemon is unreachable or client is not initialized"
            )
        return self.client

    def inspect_container(self, container_id: str) -> dict[str, Any]:
        """Queries Docker daemon for container attributes by exact ID."""
        if not HEX_CONTAINER_ID_PATTERN.match(container_id):
            raise ValueError(f"Invalid container hex ID format: {container_id!r}")

        client = self._ensure_client()
        try:
            # pyrefly: ignore [bad-argument-type]
            container = client.containers.get(container_id)
            return dict(container.attrs)
        except Exception as e:
            err_str = str(e).lower()
            if "not found" in err_str or "404" in err_str:
                raise ContainerNotFoundError(
                    f"Container {container_id} not found in Docker daemon"
                ) from e
            if (
                "connection refused" in err_str
                or "cannot connect" in err_str
                or "unreachable" in err_str
            ):
                raise DockerDaemonUnreachableError(f"Docker daemon connection error: {e}") from e
            raise ProviderAdapterError(f"Failed to inspect container {container_id}: {e}") from e

    def take_snapshot(self, container_id: str) -> ContainerSnapshot:
        """Extracts bounded, strictly redacted inspection snapshot."""
        attrs = self.inspect_container(container_id)

        config = attrs.get("Config", {})
        state = attrs.get("State", {})
        labels = redact_labels(config.get("Labels", {}))

        health_info = state.get("Health", {})
        health_status = health_info.get("Status") if isinstance(health_info, dict) else None

        return ContainerSnapshot(
            container_id=attrs.get("Id", container_id),
            name=attrs.get("Name", "").lstrip("/"),
            status=state.get("Status", "unknown"),
            created_at=attrs.get("Created"),
            started_at=state.get("StartedAt"),
            finished_at=state.get("FinishedAt"),
            exit_code=state.get("ExitCode"),
            restart_count=attrs.get("RestartCount", 0),
            image=config.get("Image"),
            health=health_status,
            labels=labels,
        )

    def restart_container(self, container_id: str, timeout_seconds: int = 15) -> None:
        """Issues restart command for the exact container ID with a bounded timeout."""
        if not HEX_CONTAINER_ID_PATTERN.match(container_id):
            raise ValueError(f"Invalid container hex ID format: {container_id!r}")

        client = self._ensure_client()
        try:
            # pyrefly: ignore [bad-argument-type]
            container = client.containers.get(container_id)
            container.restart(timeout=timeout_seconds)
            logger.info(
                "Successfully restarted container %s (timeout=%ds)", container_id, timeout_seconds
            )
        except Exception as e:
            err_str = str(e).lower()
            if "timeout" in err_str or "timed out" in err_str:
                raise DockerTimeoutError(
                    f"Docker restart timed out after {timeout_seconds}s for container {container_id}"
                ) from e
            if "not found" in err_str or "404" in err_str:
                raise ContainerNotFoundError(
                    f"Container {container_id} not found during restart"
                ) from e
            if "connection refused" in err_str or "cannot connect" in err_str:
                raise DockerDaemonUnreachableError(
                    f"Docker daemon connection dropped during restart: {e}"
                ) from e
            raise ProviderAdapterError(f"Docker restart failed for {container_id}: {e}") from e
