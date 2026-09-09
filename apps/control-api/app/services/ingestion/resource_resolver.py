import logging
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from app.db.models import Resource
from app.db.repositories.control_plane import ControlPlaneRepository

logger = logging.getLogger(__name__)


class ResourceResolver:
    """Resolves or provisions database Resource records from incoming alert/event metadata."""

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

        # 1. Direct resource_id
        if resource_id is not None:
            res = await self.repo.session.get(Resource, resource_id)
            if res:
                return res

        # 2. Extract potential service/name hints
        svc = service or labels.get("service") or labels.get("app")
        jb = job or labels.get("job")
        ext_id = external_id or labels.get("resource_external_id")
        res_name = name or labels.get("resource_name") or svc

        # 3. Match by external_id
        if ext_id:
            res = await self.repo.session.scalar(
                sa.select(Resource).where(Resource.external_id == ext_id)
            )
            if res:
                return res

        if svc:
            res = await self.repo.session.scalar(
                sa.select(Resource).where(Resource.external_id.in_([f"self-healing:{svc}", svc]))
            )
            if res:
                return res

        # 4. Match by name
        if res_name:
            res = await self.repo.session.scalar(
                sa.select(Resource).where(Resource.name == res_name)
            )
            if res:
                return res

        # 5. Fallback heuristics for known compose services
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

        # 6. If no resource found, find any existing resource or provision an unmanaged placeholder
        first_res = await self.repo.session.scalar(sa.select(Resource).limit(1))
        if first_res:
            return first_res

        # Provision fallback resource so events are never lost
        target_name = res_name or svc or "default-resource"
        target_ext = f"self-healing:{target_name}"
        new_res = Resource(
            provider="compose",
            external_id=target_ext,
            name=target_name,
            environment="local",
            labels={"auto_provisioned": True},
            managed=False,
        )
        await self.repo.add(new_res)
        logger.info(f"Auto-provisioned resource: {target_name} ({new_res.id})")
        return new_res
