import logging
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from app.db.models import Resource
from app.db.repositories.control_plane import ControlPlaneRepository

logger = logging.getLogger(__name__)


class DockerResourceDiscovery:
    """Discovers local Docker Compose containers and binds exact container ID and generation to Resource records."""

    def __init__(self, docker_client: Any = None) -> None:
        if docker_client is not None:
            self.client = docker_client
        else:
            try:
                # pyrefly: ignore [untyped-import]
                import docker
                self.client = docker.from_env()
            except Exception as e:
                logger.warning(f"Failed to initialize Docker client: {e}")
                self.client = None

    def discover_container(
        self, compose_service: str = "demo-api", compose_project: str | None = None
    ) -> dict[str, Any] | None:
        """Finds container matching compose service label and inspects exact runtime ID and labels."""
        if not self.client:
            logger.warning("Docker client unavailable for container discovery")
            return None

        try:
            filters = {"label": [f"com.docker.compose.service={compose_service}"]}
            if compose_project:
                filters["label"].append(f"com.docker.compose.project={compose_project}")

            # pyrefly: ignore [bad-argument-type]
            containers = self.client.containers.list(all=True, filters=filters)
            if not containers:
                logger.info(f"No container found with service label: {compose_service}")
                return None

            # Prefer running container if multiple exist
            target = containers[0]
            for c in containers:
                if c.status == "running":
                    target = c
                    break

            labels = target.labels or {}
            is_managed = labels.get("self-healing.managed", "").strip().lower() == "true"

            return {
                "container_id": target.id,
                "container_name": target.name,
                "status": target.status,
                "labels": labels,
                "managed": is_managed,
                "compose_service": labels.get("com.docker.compose.service", compose_service),
                "compose_project": labels.get("com.docker.compose.project", "self-healing-agent"),
                "created_at": target.attrs.get("Created"),
            }
        except Exception as e:
            logger.error(f"Error querying Docker containers for service {compose_service}: {e}")
            return None

    async def sync_resource_binding(
        self, repo: ControlPlaneRepository, service_name: str = "demo-api"
    ) -> Resource | None:
        """Binds logical resource to exact runtime Docker container ID, bumping binding_generation on change."""
        container_info = self.discover_container(compose_service=service_name)
        if not container_info:
            return None

        # Look up resource in database
        resource = await repo.session.scalar(
            sa.select(Resource).where(
                Resource.provider == "compose",
                Resource.name == service_name,
            )
        )
        if not resource:
            # Check by external_id
            resource = await repo.session.scalar(
                sa.select(Resource).where(
                    Resource.provider == "compose",
                    Resource.external_id == f"self-healing:{service_name}",
                )
            )

        if not resource:
            logger.warning(f"Cannot sync binding: Resource record for {service_name} not found in database")
            return None

        labels = dict(resource.labels or {})
        prev_container_id = labels.get("docker_container_id")
        prev_generation = labels.get("binding_generation", 0)

        new_container_id = container_info["container_id"]
        if prev_container_id != new_container_id:
            new_generation = prev_generation + 1
            logger.info(
                f"Container identity changed for {service_name}: {prev_container_id} -> {new_container_id} "
                f"(bumping binding_generation to {new_generation})"
            )
        else:
            new_generation = prev_generation if prev_generation > 0 else 1

        labels.update({
            "docker_container_id": new_container_id,
            "binding_generation": new_generation,
            "container_name": container_info["container_name"],
            "container_status": container_info["status"],
            "compose_project": container_info["compose_project"],
            "compose_service": container_info["compose_service"],
            "discovered_at": datetime.now(UTC).isoformat(),
        })
        resource.labels = labels

        # Restrict managed=True strictly to demo-api with verified self-healing.managed=true label
        if service_name == "demo-api" and container_info["managed"]:
            resource.managed = True
        else:
            resource.managed = False

        await repo.session.flush()
        return resource
