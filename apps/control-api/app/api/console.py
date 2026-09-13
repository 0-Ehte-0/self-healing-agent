"""Authenticated, bounded operator read models and local demo controls.

SSE is an invalidation feed. Clients always re-fetch authoritative snapshots;
an event cursor is never treated as a complete history of committed transactions.
"""

import asyncio
import json
import math
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

import httpx
import sqlalchemy as sa
from app.core.config import get_settings
from app.db import models as m
from app.db.repositories.control_plane import unit_of_work
from app.db.session import AsyncSessionLocal
from app.services.auth import (
    get_current_session_and_user,
    require_csrf,
    require_operator,
    require_viewer,
)
from app.services.auth.session_service import SessionService
from app.services.redaction.redactor import redact_sensitive_data
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter(
    prefix="/api/v1/console", tags=["console"], dependencies=[Depends(require_viewer)]
)
Auth = Annotated[tuple[m.User, m.UserSession], Depends(get_current_session_and_user)]
TERMINAL = {"RESOLVED", "ESCALATED", "CLOSED"}


def row(record):
    if record is None:
        return None
    # Only called on the operational model whitelist below, never user/session rows.
    return redact_sensitive_data(
        jsonable_encoder(
            {a.key: getattr(record, a.key) for a in sa.inspect(record).mapper.column_attrs}
        )
    )


async def rows(session, statement, limit=50):
    return [row(r) for r in (await session.scalars(statement.limit(limit))).all()]


@router.get("/snapshot")
async def snapshot(response: Response):
    response.headers["Cache-Control"] = "no-store"
    async with AsyncSessionLocal() as db:
        incidents = await rows(
            db, sa.select(m.Incident).order_by(m.Incident.created_at.desc(), m.Incident.id.desc())
        )
        counts = (
            await db.execute(
                sa.select(m.Incident.state, sa.func.count()).group_by(m.Incident.state)
            )
        ).all()
        return {
            "server_time": datetime.now(UTC),
            "cursor": await db.scalar(sa.select(sa.func.max(m.AuditEntry.sequence))) or 0,
            "incidents": incidents,
            "counts": {str(s.value if hasattr(s, "value") else s): c for s, c in counts},
            "resources": await rows(db, sa.select(m.Resource).order_by(m.Resource.name), 100),
            "automation": row(await db.get(m.AutomationControl, "global")),
            "demo_controls_enabled": get_settings().DEMO_CONTROLS_ENABLED
            and get_settings().ENV != "production",
        }


@router.get("/incidents")
async def incidents(
    state: str | None = None, offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100)
):
    stmt = sa.select(m.Incident)
    if state:
        from sharedmodels.enums import IncidentState

        try:
            stmt = stmt.where(m.Incident.state == IncidentState(state))
        except ValueError:
            raise HTTPException(422, "Unknown incident state") from None
    async with AsyncSessionLocal() as db:
        total = await db.scalar(sa.select(sa.func.count()).select_from(stmt.subquery()))
        return {
            "items": await rows(
                db,
                stmt.order_by(m.Incident.created_at.desc(), m.Incident.id.desc()).offset(offset),
                limit,
            ),
            "total": total,
            "offset": offset,
            "limit": limit,
        }


COLLECTIONS = {
    "evidence": m.EvidenceItem,
    "diagnoses": m.Diagnosis,
    "plans": m.RemediationPlan,
    "executions": m.Execution,
    "verifications": m.VerificationResult,
    "policy_decisions": m.PolicyDecision,
    "attention": m.AttentionItem,
    "escalations": m.EscalationRecord,
    "dry_runs": m.DryRunRecord,
    "schedules": m.WorkflowSchedule,
    "observations": m.VerificationObservation,
}


@router.get("/incidents/{incident_id}")
async def incident_detail(incident_id: UUID):
    async with AsyncSessionLocal() as db:
        incident = await db.get(m.Incident, incident_id)
        if incident is None:
            raise HTTPException(404, "Incident not found")
        result = {
            "incident": row(incident),
            "resource": row(await db.get(m.Resource, incident.resource_id)),
            "server_time": datetime.now(UTC),
        }
        for name, model in COLLECTIONS.items():
            result[name] = await rows(
                db,
                sa.select(model)
                .where(model.incident_id == incident_id)
                .order_by(model.created_at.desc(), model.id.desc()),
                50,
            )
        plan_ids = sa.select(m.RemediationPlan.id).where(
            m.RemediationPlan.incident_id == incident_id
        )
        result["steps"] = await rows(
            db,
            sa.select(m.RemediationStep)
            .where(m.RemediationStep.plan_id.in_(plan_ids))
            .order_by(m.RemediationStep.created_at.desc(), m.RemediationStep.position),
            100,
        )
        result["approvals"] = await rows(
            db,
            sa.select(m.Approval)
            .where(m.Approval.plan_id.in_(plan_ids))
            .order_by(m.Approval.created_at.desc()),
            50,
        )
        result["events"] = await rows(
            db,
            sa.select(m.Event)
            .join(m.IncidentEvent, m.IncidentEvent.event_id == m.Event.id)
            .where(m.IncidentEvent.incident_id == incident_id)
            .order_by(m.Event.created_at.desc()),
            50,
        )
        result["collection_limit"] = 50
        return result


@router.get("/incidents/{incident_id}/records/{collection}")
async def records(
    incident_id: UUID,
    collection: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    model = COLLECTIONS.get(collection)
    if model is None:
        raise HTTPException(404, "Unknown collection")
    async with AsyncSessionLocal() as db:
        stmt = sa.select(model).where(model.incident_id == incident_id)
        return {
            "items": await rows(
                db, stmt.order_by(model.created_at.desc(), model.id.desc()).offset(offset), limit
            ),
            "total": await db.scalar(sa.select(sa.func.count()).select_from(stmt.subquery())),
        }


@router.get("/incidents/{incident_id}/timeline")
async def timeline(
    incident_id: UUID, before: int | None = Query(None, gt=0), limit: int = Query(50, ge=1, le=100)
):
    async with AsyncSessionLocal() as db:
        stmt = sa.select(m.AuditEntry).where(m.AuditEntry.incident_id == incident_id)
        if before:
            stmt = stmt.where(m.AuditEntry.sequence < before)
        items = await rows(db, stmt.order_by(m.AuditEntry.sequence.desc()), limit + 1)
        return {
            "items": items[:limit],
            "next_cursor": items[limit - 1]["sequence"] if len(items) > limit else None,
        }


@router.get("/resources")
async def resources(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    async with AsyncSessionLocal() as db:
        return {
            "items": await rows(
                db,
                sa.select(m.Resource).order_by(m.Resource.name, m.Resource.id).offset(offset),
                limit,
            ),
            "locks": await rows(db, sa.select(m.ResourceLock), 100),
        }


@router.get("/policies")
async def policies(offset: int = Query(0, ge=0)):
    async with AsyncSessionLocal() as db:
        return {
            "items": await rows(
                db,
                sa.select(m.Policy).order_by(m.Policy.name, m.Policy.version.desc()).offset(offset),
                50,
            )
        }


@router.get("/system")
async def system():
    from app.api.system import get_system_status

    try:
        return redact_sensitive_data(await asyncio.wait_for(get_system_status(None), 6))
    except TimeoutError:
        raise HTTPException(503, "System health check timed out") from None


QUERIES = {
    "rps": 'sum(rate(demo_api_http_requests_total{endpoint="/jobs"}[1m]))',
    "errors": '100 * (sum(rate(demo_api_http_requests_total{endpoint="/jobs",status=~"5.."}[1m])) or (sum(rate(demo_api_http_requests_total{endpoint="/jobs"}[1m])) * 0)) / clamp_min(sum(rate(demo_api_http_requests_total{endpoint="/jobs"}[1m])), 0.001)',
    "latency": '1000 * histogram_quantile(0.95, sum by(le)(rate(demo_api_http_request_duration_seconds_bucket{endpoint="/jobs"}[1m])))',
    "cpu": "100 * sum(rate(demo_api_cpu_seconds_total[1m])) / clamp_min(sum(demo_api_cpu_budget_cores), 0.001)",
    "ready": "max(demo_api_health_ready_status)",
    "redis": "max(demo_api_redis_connected)",
}


@router.get("/telemetry")
async def telemetry(minutes: int = Query(10, ge=2, le=60)):
    now = datetime.now(UTC).timestamp()
    async with httpx.AsyncClient(timeout=4) as client:

        async def query(name, expression):
            try:
                res = await client.get(
                    get_settings().PROMETHEUS_URL + "/api/v1/query_range",
                    params={
                        "query": expression,
                        "start": now - minutes * 60,
                        "end": now,
                        "step": 15,
                    },
                )
                res.raise_for_status()
                payload = res.json()
                if payload.get("status") != "success":
                    raise ValueError("Query failed")
                series = payload["data"]["result"]
                points = (
                    [
                        [t, float(v) if math.isfinite(float(v)) else None]
                        for t, v in series[0]["values"]
                    ]
                    if series
                    else []
                )
                return name, {
                    "points": points,
                    "available": bool(points),
                    "latest": points[-1][1] if points else None,
                }
            except (httpx.HTTPError, ValueError, KeyError, IndexError):
                return name, {"points": [], "available": False, "latest": None}

        signals = dict(await asyncio.gather(*(query(k, v) for k, v in QUERIES.items())))
    return {
        "server_time": datetime.now(UTC),
        "signals": signals,
        "window_minutes": minutes,
        "animation": "Flow animation scaled to observed request rate; not per-request tracing.",
    }


@router.get("/stream")
async def stream(request: Request, auth: Auth):
    user, session = auth

    async def events():
        while not await request.is_disconnected():
            # Revalidate persisted sessions on every tick, including disabled accounts.
            async with unit_of_work(AsyncSessionLocal, actor="system:console-stream") as repo:
                valid = await SessionService.validate_session(repo, session.session_token)
                current_user = await repo.session.get(m.User, user.id)
                if not valid or not current_user or not current_user.enabled:
                    yield "event: expired\ndata: {}\n\n"
                    return
                cursor = (
                    await repo.session.scalar(sa.select(sa.func.max(m.AuditEntry.sequence))) or 0
                )
            yield f"id: {cursor}\nevent: snapshot\ndata: {json.dumps({'cursor': cursor})}\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


class FaultCommand(BaseModel):
    scenario_id: Literal["SCN-001", "SCN-002", "SCN-003"]
    action: Literal["inject", "clear"]
    idempotency_key: UUID


@router.get("/faults")
async def faults():
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            res = await client.get(
                get_settings().FAULT_INJECTOR_URL + "/faults/active",
                headers={"X-Fault-Token": get_settings().FAULT_INJECTOR_SECRET},
            )
            res.raise_for_status()
            return {"available": True, "active": res.json()}
    except (httpx.HTTPError, ValueError):
        return {"available": False, "active": []}


@router.post("/faults", dependencies=[Depends(require_operator), Depends(require_csrf)])
async def command(body: FaultCommand, auth: Auth):
    settings = get_settings()
    if not settings.DEMO_CONTROLS_ENABLED or settings.ENV == "production":
        raise HTTPException(403, "Local demo controls are disabled")
    actor = f"user:{auth[0].username}"
    async with unit_of_work(AsyncSessionLocal, actor=actor) as repo:
        # Serialize intent registration across processes, not just this API instance.
        await repo.session.execute(sa.text("SELECT pg_advisory_xact_lock(714108)"))
        existing = await repo.session.scalar(
            sa.select(m.DemoCommand).where(m.DemoCommand.idempotency_key == body.idempotency_key)
        )
        if existing:
            if (existing.actor, existing.scenario_id, existing.action) != (
                actor,
                body.scenario_id,
                body.action,
            ):
                raise HTTPException(409, "Idempotency key belongs to a different command")
            return row(existing)
        pending = await repo.session.scalar(
            sa.select(m.DemoCommand).where(m.DemoCommand.status == "PENDING").limit(1)
        )
        if pending:
            age = (datetime.now(UTC) - pending.created_at).total_seconds()
            if body.action != "clear" or age < 30 or body.scenario_id != pending.scenario_id:
                raise HTTPException(
                    409,
                    "A fault command is pending; after 30 seconds clear the same scenario to reconcile it",
                )
            pending.status = "UNCERTAIN"
        if body.action == "inject":
            uncertain = await repo.session.scalar(
                sa.select(m.DemoCommand)
                .where(
                    m.DemoCommand.status == "UNCERTAIN",
                    m.DemoCommand.result["reconciled_by"].astext.is_(None),
                )
                .limit(1)
            )
            if uncertain:
                raise HTTPException(
                    409,
                    f"Clear {uncertain.scenario_id} to reconcile its uncertain command before injecting again",
                )
            status = await faults()
            active = status["active"]
            if not status["available"]:
                raise HTTPException(503, "Fault injector unavailable")
            if isinstance(active, dict):
                active = active.get("active_faults", active.get("faults", []))
            if active:
                raise HTTPException(
                    409, "Clear the existing fault before starting another scenario"
                )
        item = m.DemoCommand(
            id=uuid4(),
            idempotency_key=body.idempotency_key,
            scenario_id=body.scenario_id,
            action=body.action,
            actor=actor,
            status="PENDING",
            result={},
        )
        repo.session.add(item)
        await repo.session.flush()
        command_id = item.id
    # Intent is durable before issuing the external command. Never retry a timeout.
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            result = await client.post(
                f"{settings.FAULT_INJECTOR_URL}/faults/{body.scenario_id}/{body.action}",
                params={"ttl_seconds": 600},
                headers={"X-Fault-Token": settings.FAULT_INJECTOR_SECRET},
            )
            result.raise_for_status()
            payload, status = result.json(), "SUCCEEDED"
    except (httpx.HTTPError, ValueError):
        payload, status = (
            {
                "message": "Command outcome is uncertain. Inspect the fault state before issuing another command."
            },
            "UNCERTAIN",
        )
    async with unit_of_work(AsyncSessionLocal, actor=actor) as repo:
        item = await repo.session.get(m.DemoCommand, command_id)
        item.result, item.status = redact_sensitive_data(payload), status
        if body.action == "clear" and status == "SUCCEEDED":
            uncertain = (
                await repo.session.scalars(
                    sa.select(m.DemoCommand).where(
                        m.DemoCommand.scenario_id == body.scenario_id,
                        m.DemoCommand.status == "UNCERTAIN",
                    )
                )
            ).all()
            for prior in uncertain:
                prior.result = {**prior.result, "reconciled_by": str(command_id)}
        await repo.session.flush()
        return row(item)


@router.get("/commands")
async def commands():
    async with AsyncSessionLocal() as db:
        return {
            "items": await rows(
                db, sa.select(m.DemoCommand).order_by(m.DemoCommand.created_at.desc())
            )
        }


class DryRunRequest(BaseModel):
    expected_incident_version: int = Field(ge=1)
    plan_version: int = Field(ge=1)
    content_hash: str = Field(min_length=64, max_length=64)


@router.post(
    "/incidents/{incident_id}/dry-run",
    dependencies=[Depends(require_operator), Depends(require_csrf)],
)
async def dry_run(incident_id: UUID, body: DryRunRequest, auth: Auth):
    from actioncatalog.planner.dry_run import DryRunExecutor
    from pydantic import ValidationError
    from sharedmodels.plan import RemediationPlanSchema, RemediationStepSchema, TargetBinding

    actor = f"user:{auth[0].username}"
    async with unit_of_work(AsyncSessionLocal, actor=actor) as repo:
        incident = await repo.session.get(m.Incident, incident_id)
        if not incident:
            raise HTTPException(404, "Incident not found")
        plan = await repo.session.scalar(
            sa.select(m.RemediationPlan)
            .where(m.RemediationPlan.incident_id == incident_id)
            .order_by(m.RemediationPlan.version.desc())
            .limit(1)
        )
        if not plan or (incident.version, plan.version, plan.content_hash) != (
            body.expected_incident_version,
            body.plan_version,
            body.content_hash,
        ):
            raise HTTPException(
                409, "Incident or plan changed; refresh before running a simulation"
            )
        resource = await repo.session.get(m.Resource, incident.resource_id)
        steps = (
            await repo.session.scalars(
                sa.select(m.RemediationStep)
                .where(m.RemediationStep.plan_id == plan.id)
                .order_by(m.RemediationStep.position)
            )
        ).all()
        try:
            schema = RemediationPlanSchema(
                id=plan.id,
                incident_id=incident_id,
                diagnosis_id=plan.diagnosis_id,
                version=plan.version,
                risk=plan.risk,
                target_binding=TargetBinding(
                    resource_id=resource.id,
                    container_id=plan.container_id,
                    service_name=resource.labels.get("compose_service", resource.name),
                    binding_generation=plan.binding_generation,
                ),
                steps=[
                    RemediationStepSchema(
                        **{
                            k: getattr(s, k)
                            for k in (
                                "id",
                                "plan_id",
                                "resource_id",
                                "position",
                                "action",
                                "action_schema_version",
                                "parameters",
                                "verification",
                            )
                        }
                    )
                    for s in steps
                ],
                content_hash=plan.content_hash,
                verification_profile=plan.verification_profile,
                actor=actor,
            )
        except ValidationError:
            raise HTTPException(
                422, "Stored plan cannot be simulated: invalid target binding or plan schema"
            ) from None
        simulation = await DryRunExecutor().execute_dry_run(schema, actor=actor)
        # A simulated policy is never a current authorization decision.
        simulation.policy_evaluation = {
            "simulated": True,
            "authorization_granted": False,
            "note": "Structural simulation only. Execution re-evaluates live policy, approval, binding and health.",
        }
        item = m.DryRunRecord(**simulation.model_dump(exclude={"created_at"}))
        repo.session.add(item)
        await repo.session.flush()
        return row(item)
