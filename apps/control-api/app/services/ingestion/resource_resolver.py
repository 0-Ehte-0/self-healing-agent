import logging
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from app.db.models import Resource
from app.db.repositories.control_plane import ControlPlaneRepository

logger = logging.getLogger(__name__)


class ResourceResolver:
    """Resolves database Resource records from incoming alert/event metadata without insecure fallbacks."""

    def __init__(self, repo: ControlPlaneRepository):
        self.repo = repo

    async def resolve(
        self,
        *,
        resource_id: UUID | None = None,
        external_id: str | None = None,
        name: str | None = None,
        service: str | None = None,
        job: str | None = None,
        labels: dict[str, Any] | None = None,
    ) -> Resource:
        labels = labels or {}

        # 1. Direct resource_id match
        if resource_id is not None:
            res = await self.repo.session.get(Resource, resource_id)
            if res:
                return res

        # 2. Extract potential service/name hints (incoming alert labels)
        svc = service or labels.get("service") or labels.get("app")
        jb = job or labels.get("job")
        ext_id = external_id or labels.get("resource_external_id")
        res_name = name or labels.get("resource_name") or svc

        # 3. Match by explicit external_id
        if ext_id:
            res = await self.repo.session.scalar(
                sa.select(Resource).where(Resource.external_id == ext_id)
            )
            if res:
                return res

        # Match by service-derived external_id (e.g. self-healing:demo-api)
        if svc:
            res = await self.repo.session.scalar(
                sa.select(Resource).where(
                    Resource.external_id.in_([
                        f"self-healing:{svc}",
                        f"compose:local:self-healing:{svc}",
                        svc,
                    ])
                )
            )
            if res:
                return res

        # 4. Match by resource name
        if res_name:
            res = await self.repo.session.scalar(
                sa.select(Resource).where(Resource.name == res_name)
            )
            if res:
                return res

        # 5. Matching heuristics for known Prometheus jobs
        if jb:
            if "demo-api" in jb:
                res = await self.repo.session.scalar(
                    sa.select(Resource).where(Resource.name == "demo-api")
                )
                if res:
                    return res
            elif "demo-worker" in jb:
                res = await self.repo.session.scalar(
                    sa.select(Resource).where(Resource.name == "demo-worker")
                )
                if res:
                    return res

        # 6. Fallback: Never return arbitrary first_res or correlate to demo-api!
        # Auto-provision an explicitly unmanaged, quarantined placeholder namespaced by provider/env/project.
        target_name = res_name or svc or (f"job-{jb}" if jb else "unknown-resource")
        target_ext = f"compose:local:self-healing:{target_name}"

        existing_unmanaged = await self.repo.session.scalar(
            sa.select(Resource).where(Resource.external_id == target_ext)
        )
        if existing_unmanaged:
            return existing_unmanaged

        # Safe provision: alert labels can NEVER set managed=True or grant mutation privileges
        new_res = Resource(
            provider="compose",
            external_id=target_ext,
            name=target_name,
            environment="local",
            labels={
                "auto_provisioned": True,
                "quarantined": True,
                "unmanaged": True,
                "raw_service_hint": str(svc),
                "raw_job_hint": str(jb),
            },
            managed=False,
        )
        await self.repo.add(new_res)
        logger.warning(
            f"Alert resolved to newly provisioned unmanaged resource '{target_name}' ({new_res.id}). "
            f"Arbitrary fallback prevented."
        )
        return new_res
