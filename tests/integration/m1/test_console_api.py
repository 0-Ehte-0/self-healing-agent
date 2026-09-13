"""Console contracts against a freshly migrated, isolated PostgreSQL database."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import sqlalchemy as sa
from app.api import console
from app.core.config import get_settings
from app.db import models as m
from app.db.repositories.control_plane import unit_of_work
from app.main import app
from app.services.auth import get_current_session_and_user
from httpx import ASGITransport, AsyncClient
from sharedmodels.enums import Severity, UserRole


@pytest.fixture
async def client(session_factory, monkeypatch):
    monkeypatch.setattr(console, "AsyncSessionLocal", session_factory)
    user = m.User(
        id=uuid4(),
        username="console_operator",
        password_hash="unused",
        role=UserRole.OPERATOR,
        enabled=True,
    )
    session = m.UserSession(
        id=uuid4(),
        user_id=user.id,
        session_token="test-session",
        csrf_token="test-csrf",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        is_revoked=False,
    )

    async def auth():
        return user, session

    app.dependency_overrides[get_current_session_and_user] = auth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, user
    app.dependency_overrides.clear()


@pytest.fixture
async def incident_id(session_factory):
    async with unit_of_work(session_factory, actor="test:console") as repo:
        resource = await repo.session.scalar(sa.select(m.Resource).limit(1))
        incident = await repo.create_incident(
            resource_id=resource.id, correlation_key=f"console-{uuid4()}", severity=Severity.HIGH
        )
        repo.session.add(
            m.EvidenceItem(
                incident_id=incident.id,
                kind="METRICS",
                source="test",
                observed_at=datetime.now(UTC),
                content={"password": "do-not-expose", "message": "Bearer abc-secret"},
                sha256="a" * 64,
                actor="test:console",
            )
        )
        return incident.id


async def test_snapshot_detail_pagination_and_redaction(client, incident_id):
    ac, _ = client
    response = await ac.get("/api/v1/console/snapshot")
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    assert response.json()["counts"]["DETECTED"] >= 1
    detail = await ac.get(f"/api/v1/console/incidents/{incident_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["evidence"][0]["content"]["password"] == "[REDACTED]"
    assert "abc-secret" not in detail.text
    assert (await ac.get("/api/v1/console/incidents?limit=101")).status_code == 422
    assert (await ac.get("/api/v1/console/incidents?state=INVALID")).status_code == 422
    assert (await ac.get(f"/api/v1/console/incidents/{uuid4()}")).status_code == 404
    assert (
        await ac.get(f"/api/v1/console/incidents/{incident_id}/records/user_sessions")
    ).status_code == 404
    timeline = (await ac.get(f"/api/v1/console/incidents/{incident_id}/timeline?limit=1")).json()
    assert len(timeline["items"]) == 1
    if timeline["next_cursor"]:
        older = (
            await ac.get(
                f"/api/v1/console/incidents/{incident_id}/timeline?before={timeline['next_cursor']}"
            )
        ).json()
        assert all(r["sequence"] < timeline["next_cursor"] for r in older["items"])


async def test_fault_role_csrf_and_allowlist(client, monkeypatch):
    ac, user = client
    body = {"scenario_id": "SCN-001", "action": "inject", "idempotency_key": str(uuid4())}
    monkeypatch.setattr(get_settings(), "DEMO_CONTROLS_ENABLED", True)
    assert (await ac.post("/api/v1/console/faults", json=body)).status_code == 403
    user.role = UserRole.VIEWER
    assert (
        await ac.post("/api/v1/console/faults", json=body, headers={"X-CSRF-Token": "test-csrf"})
    ).status_code == 403
    user.role = UserRole.OPERATOR
    body["scenario_id"] = "SCN-004"
    assert (
        await ac.post("/api/v1/console/faults", json=body, headers={"X-CSRF-Token": "test-csrf"})
    ).status_code == 422
    body["scenario_id"] = "SCN-001"
    monkeypatch.setattr(get_settings(), "ENV", "production")
    assert (
        await ac.post("/api/v1/console/faults", json=body, headers={"X-CSRF-Token": "test-csrf"})
    ).status_code == 403


async def test_fault_idempotency_intent_and_actor(client, session_factory, monkeypatch):
    ac, _ = client
    monkeypatch.setattr(get_settings(), "DEMO_CONTROLS_ENABLED", True)
    monkeypatch.setattr(
        console, "faults", AsyncMock(return_value={"available": True, "active": []})
    )

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "injected", "run_id": "test-run"}

    # Patch only the outgoing method, retaining the ASGI client's real POST method.
    original = console.httpx.AsyncClient.post
    calls = []

    async def post(instance, url, **kwargs):
        if str(url).startswith(get_settings().FAULT_INJECTOR_URL):
            async with session_factory() as db:
                intent = await db.scalar(
                    sa.select(m.DemoCommand).where(m.DemoCommand.idempotency_key == key)
                )
                assert intent and intent.status == "PENDING", (
                    "Intent must commit before external mutation"
                )
            calls.append(url)
            return Response()
        return await original(instance, url, **kwargs)

    monkeypatch.setattr(console.httpx.AsyncClient, "post", post)
    key = uuid4()
    body = {"scenario_id": "SCN-001", "action": "inject", "idempotency_key": str(key)}
    headers = {"X-CSRF-Token": "test-csrf", "X-Actor": "user:forged"}
    first = await ac.post("/api/v1/console/faults", json=body, headers=headers)
    assert first.status_code == 200, first.text
    second = await ac.post("/api/v1/console/faults", json=body, headers=headers)
    assert first.json()["id"] == second.json()["id"]
    assert len(calls) == 1
    assert first.json()["actor"] == "user:console_operator"
    body["action"] = "clear"
    assert (await ac.post("/api/v1/console/faults", json=body, headers=headers)).status_code == 409
    async with session_factory() as db:
        audit = await db.scalar(
            sa.select(m.AuditEntry).where(m.AuditEntry.entity_id == first.json()["id"])
        )
        assert audit.actor == "user:console_operator"


async def test_dry_run_requires_current_plan(client, incident_id):
    ac, _ = client
    response = await ac.post(
        f"/api/v1/console/incidents/{incident_id}/dry-run",
        json={"expected_incident_version": 1, "plan_version": 1, "content_hash": "a" * 64},
        headers={"X-CSRF-Token": "test-csrf"},
    )
    assert response.status_code == 409


async def test_console_anonymous_is_unauthorized():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for path in (
            "snapshot",
            "incidents",
            "resources",
            "policies",
            "system",
            "telemetry",
            "faults",
            "commands",
            "stream",
        ):
            assert (await ac.get(f"/api/v1/console/{path}")).status_code == 401


async def test_real_login_csrf_logout_and_revocation(session_factory, monkeypatch):
    from app.api import auth as auth_api
    from app.services.auth import dependencies

    monkeypatch.setattr(auth_api, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(dependencies, "AsyncSessionLocal", session_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        login = await ac.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "local-admin-change-me"}
        )
        assert login.status_code == 200, login.text
        assert "httponly" in login.headers["set-cookie"].lower()
        assert (await ac.get("/api/v1/auth/me")).status_code == 200
        assert (await ac.post("/api/v1/auth/logout")).status_code == 403
        token = login.json()["csrf_token"]
        old_cookie = ac.cookies.get("sh_session_id")
        assert (
            await ac.post("/api/v1/auth/logout", headers={"X-CSRF-Token": token})
        ).status_code == 200
        ac.cookies.set("sh_session_id", old_cookie)
        assert (await ac.get("/api/v1/auth/me")).status_code == 401


async def test_stream_rechecks_disabled_user(session_factory, monkeypatch):
    from types import SimpleNamespace

    from app.services.auth.session_service import SessionService

    monkeypatch.setattr(console, "AsyncSessionLocal", session_factory)
    async with unit_of_work(session_factory, actor="test:sse") as repo:
        user = m.User(
            id=uuid4(),
            username=f"sse-{uuid4()}",
            password_hash="unused",
            role=UserRole.VIEWER,
            enabled=True,
        )
        repo.session.add(user)
        await repo.session.flush()
        session, _, _ = await SessionService.create_user_session(repo, user.id)
    response = await console.stream(
        SimpleNamespace(is_disconnected=AsyncMock(return_value=False)), (user, session)
    )
    iterator = response.body_iterator
    assert "event: snapshot" in await anext(iterator)
    async with unit_of_work(session_factory, actor="test:sse-disable") as repo:
        record = await repo.session.get(m.User, user.id)
        record.enabled = False
    assert "event: expired" in await anext(iterator)
    await iterator.aclose()


async def test_dry_run_persists_without_healing_side_effects(client, session_factory, incident_id):
    from actioncatalog.planner.mapper import DeterministicPlanner
    from sharedmodels.enums import IncidentState

    ac, _ = client
    async with unit_of_work(session_factory, actor="test:dryrun") as repo:
        inc = await repo.session.get(m.Incident, incident_id)
        resource = await repo.session.get(m.Resource, inc.resource_id)
        await repo.transition(inc.id, 1, IncidentState.TRIAGED)
        await repo.transition(inc.id, 2, IncidentState.DIAGNOSED)
        diagnosis = m.Diagnosis(
            id=uuid4(),
            incident_id=inc.id,
            root_cause="CPU_SATURATION",
            confidence=0.97,
            evidence_ids=[],
            reasoning={},
            actor="test:dryrun",
        )
        repo.session.add(diagnosis)
        await repo.session.flush()
        plan = DeterministicPlanner().create_plan(
            incident_id=inc.id,
            diagnosis_id=diagnosis.id,
            root_cause="CPU_SATURATION",
            target_binding={
                "resource_id": resource.id,
                "container_id": "a" * 64,
                "binding_generation": 1,
                "service_name": resource.labels["compose_service"],
            },
        )
        await repo.create_remediation_plan_with_steps(
            incident_id=inc.id,
            diagnosis_id=diagnosis.id,
            content_hash=plan.content_hash,
            container_id=plan.target_binding.container_id,
            binding_generation=1,
            verification_profile=plan.verification_profile,
            steps_data=[s.model_dump() for s in plan.steps],
        )
        await repo.transition(inc.id, 3, IncidentState.PLANNED)
    response = await ac.post(
        f"/api/v1/console/incidents/{incident_id}/dry-run",
        json={"expected_incident_version": 4, "plan_version": 1, "content_hash": plan.content_hash},
        headers={"X-CSRF-Token": "test-csrf"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["validation_result"]["valid"] is True
    assert response.json()["policy_evaluation"]["authorization_granted"] is False
    async with session_factory() as db:
        inc = await db.get(m.Incident, incident_id)
        assert (inc.state, inc.version, inc.attempts, inc.resolved_at) == (
            IncidentState.PLANNED,
            4,
            0,
            None,
        )
        assert (
            await db.scalar(
                sa.select(sa.func.count())
                .select_from(m.Execution)
                .where(m.Execution.incident_id == incident_id)
            )
            == 0
        )
        assert (
            await db.scalar(
                sa.select(sa.func.count())
                .select_from(m.VerificationResult)
                .where(m.VerificationResult.incident_id == incident_id)
            )
            == 0
        )
