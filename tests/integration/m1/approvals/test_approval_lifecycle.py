from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
import sqlalchemy as sa
from app.db.models import (
    Approval,
    Diagnosis,
    EscalationRecord,
    Incident,
    PolicyDecision,
    RemediationPlan,
    Resource,
    User,
    UserSession,
)
from app.db.repositories.control_plane import unit_of_work
from app.db.session import AsyncSessionLocal
from app.main import app
from app.services.auth import get_current_session_and_user, require_csrf
from fastapi import status
from httpx import ASGITransport, AsyncClient
from sharedmodels.enums import IncidentState, RiskLevel, Severity, UserRole


@pytest.fixture
async def approver_user():
    user_id = uuid4()
    user = User(
        id=user_id,
        username=f"approver_{user_id.hex[:6]}",
        password_hash="mock",
        role=UserRole.APPROVER,
        enabled=True,
    )
    async with unit_of_work(AsyncSessionLocal, actor="system:test_setup") as repo:
        repo.session.add(user)
    return user


@pytest.fixture
async def pending_incident_and_plan():
    plan_id = uuid4()

    async with unit_of_work(AsyncSessionLocal, actor="system:test_setup") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        if res is None:
            res = Resource(
                provider="compose",
                external_id=f"self-healing:demo-api-{uuid4().hex[:6]}",
                name="demo-api",
                environment="local",
                labels={"compose_service": "demo-api"},
                managed=True,
            )
            repo.session.add(res)
            await repo.session.flush()

        # Follow strict legal transition guards
        incident = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"corr_{uuid4().hex}",
            severity=Severity.HIGH,
            approval_required=True,
        )
        incident_id = incident.id

        incident = await repo.transition(incident_id, 1, IncidentState.TRIAGED)
        incident = await repo.transition(incident_id, 2, IncidentState.DIAGNOSED)

        diag = Diagnosis(
            id=uuid4(),
            incident_id=incident_id,
            root_cause="CONTAINER_STOPPED",
            confidence=0.95,
            evidence_ids=["ev-1"],
            reasoning={"status": "stopped"},
            actor="system:test_setup",
        )
        repo.session.add(diag)

        incident = await repo.transition(incident_id, 3, IncidentState.PLANNED)

        plan = RemediationPlan(
            id=plan_id,
            incident_id=incident_id,
            diagnosis_id=diag.id,
            version=1,
            risk=RiskLevel.LOW,
            approved=False,
            actor="agent:planner",
            content_hash="content_hash_12345",
            container_id="demo-api-container",
            binding_generation=1,
        )
        repo.session.add(plan)
        await repo.session.flush()

        incident = await repo.transition(incident_id, 4, IncidentState.PENDING_APPROVAL)
        current_version = incident.version

    return incident_id, plan_id, current_version, 1, "content_hash_12345"


@pytest.mark.asyncio
async def test_approve_plan_lifecycle(approver_user, pending_incident_and_plan):
    incident_id, plan_id, inc_ver, plan_ver, content_hash = pending_incident_and_plan
    session = UserSession(
        id=uuid4(),
        session_token="session-token",
        user_id=approver_user.id,
        csrf_token="csrf-token",
        is_revoked=False,
    )

    async def mock_auth():
        return approver_user, session

    app.dependency_overrides[get_current_session_and_user] = mock_auth
    app.dependency_overrides[require_csrf] = lambda: None

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post(
                f"/api/v1/incidents/{incident_id}/approval",
                json={
                    "decision": "APPROVE",
                    "expected_incident_version": inc_ver,
                    "plan_version": plan_ver,
                    "content_hash": content_hash,
                    "idempotency_key": f"key_{uuid4().hex[:8]}",
                },
            )
            assert res.status_code == status.HTTP_200_OK
            data = res.json()
            assert data["decision"] == "APPROVE"
            assert data["incident_state"] == "APPROVED"
            assert data["incident_version"] == inc_ver + 1
            assert data["approver"] == approver_user.username

            # Verify in DB
            async with unit_of_work(AsyncSessionLocal, actor="system:verify") as repo:
                inc = await repo.session.get(Incident, incident_id)
                assert inc.state == IncidentState.APPROVED
                assert inc.version == inc_ver + 1

                plan = await repo.session.get(RemediationPlan, plan_id)
                assert plan.approved is True

                approval = await repo.session.scalar(
                    sa.select(Approval).where(Approval.plan_id == plan_id)
                )
                assert approval is not None
                assert approval.decision == "APPROVE"
                assert approval.expires_at > datetime.now(UTC)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_reject_plan_lifecycle_with_escalation(approver_user, pending_incident_and_plan):
    incident_id, plan_id, inc_ver, plan_ver, content_hash = pending_incident_and_plan
    session = UserSession(
        id=uuid4(),
        session_token="session-token",
        user_id=approver_user.id,
        csrf_token="csrf-token",
        is_revoked=False,
    )

    async def mock_auth():
        return approver_user, session

    app.dependency_overrides[get_current_session_and_user] = mock_auth
    app.dependency_overrides[require_csrf] = lambda: None

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Rejection without reason fails with 422
            res = await ac.post(
                f"/api/v1/incidents/{incident_id}/approval",
                json={
                    "decision": "REJECT",
                    "expected_incident_version": inc_ver,
                    "plan_version": plan_ver,
                    "rejection_reason": "   ",
                    "idempotency_key": f"key_{uuid4().hex[:8]}",
                },
            )
            assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

            # Rejection with reason succeeds and transitions to ESCALATED per ADR-0003
            rejection_reason = "Manual maintenance in progress; automated restart is unsafe."
            res = await ac.post(
                f"/api/v1/incidents/{incident_id}/approval",
                json={
                    "decision": "REJECT",
                    "expected_incident_version": inc_ver,
                    "plan_version": plan_ver,
                    "rejection_reason": rejection_reason,
                    "idempotency_key": f"key_{uuid4().hex[:8]}",
                },
            )
            assert res.status_code == status.HTTP_200_OK
            data = res.json()
            assert data["decision"] == "REJECT"
            assert data["incident_state"] == "ESCALATED"
            assert data["rejection_reason"] == rejection_reason

            # Verify in DB: incident is ESCALATED and EscalationRecord exists
            async with unit_of_work(AsyncSessionLocal, actor="system:verify") as repo:
                inc = await repo.session.get(Incident, incident_id)
                assert inc.state == IncidentState.ESCALATED

                escalation = await repo.session.scalar(
                    sa.select(EscalationRecord).where(EscalationRecord.incident_id == incident_id)
                )
                assert escalation is not None
                assert rejection_reason in escalation.escalation_reason
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_version_and_state_conflict_guards(approver_user, pending_incident_and_plan):
    incident_id, plan_id, inc_ver, plan_ver, content_hash = pending_incident_and_plan
    session = UserSession(
        id=uuid4(),
        session_token="session-token",
        user_id=approver_user.id,
        csrf_token="csrf-token",
        is_revoked=False,
    )

    async def mock_auth():
        return approver_user, session

    app.dependency_overrides[get_current_session_and_user] = mock_auth
    app.dependency_overrides[require_csrf] = lambda: None

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Stale version returns 409
            res = await ac.post(
                f"/api/v1/incidents/{incident_id}/approval",
                json={
                    "decision": "APPROVE",
                    "expected_incident_version": inc_ver + 10,  # Wrong version
                    "plan_version": plan_ver,
                    "idempotency_key": f"key_{uuid4().hex[:8]}",
                },
            )
            assert res.status_code == status.HTTP_409_CONFLICT
            assert "version mismatch" in res.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_policy_decision_persistence():
    plan_id = uuid4()

    async with unit_of_work(AsyncSessionLocal, actor="system:test_policy") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        if res is None:
            res = Resource(
                provider="compose",
                external_id=f"self-healing:demo-api-{uuid4().hex[:6]}",
                name="demo-api",
                environment="local",
                labels={"compose_service": "demo-api"},
                managed=True,
            )
            repo.session.add(res)
            await repo.session.flush()

        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"corr_policy_{uuid4().hex}",
            severity=Severity.HIGH,
        )
        incident_id = inc.id
        inc = await repo.transition(incident_id, 1, IncidentState.TRIAGED)
        inc = await repo.transition(incident_id, 2, IncidentState.DIAGNOSED)

        diag = Diagnosis(
            id=uuid4(),
            incident_id=incident_id,
            root_cause="CONTAINER_STOPPED",
            confidence=0.90,
            evidence_ids=["ev-1"],
            reasoning={"status": "stopped"},
            actor="system:test_policy",
        )
        repo.session.add(diag)

        inc = await repo.transition(incident_id, 3, IncidentState.PLANNED)

        plan = RemediationPlan(
            id=plan_id,
            incident_id=incident_id,
            diagnosis_id=diag.id,
            version=1,
            risk=RiskLevel.LOW,
            content_hash="test_content_hash",
            container_id="demo-api-container",
            binding_generation=1,
            actor="system:test_policy",
        )
        repo.session.add(plan)
        await repo.session.flush()

        # Record policy decision
        decision_record = await repo.record_policy_decision(
            incident_id=incident_id,
            plan_id=plan_id,
            plan_version=1,
            policy_version=2,
            content_hash="test_content_hash",
            container_id="demo-api-container",
            binding_generation=1,
            decision="REQUIRE_APPROVAL",
            reason_codes=["APPROVAL_REQUIRED", "CONFIDENCE_ACCEPTABLE"],
            rule_results=[
                {
                    "rule_name": "kill_switch",
                    "passed": True,
                    "reason_code": "KILL_SWITCH_INACTIVE",
                    "message": "OK",
                }
            ],
            evaluated_facts={"action": "restart_container", "confidence": 0.88},
            evidence_freshness_seconds=42.5,
        )
        assert decision_record.id is not None
        assert decision_record.decision == "REQUIRE_APPROVAL"

        # Query latest policy decision
        latest = await repo.get_latest_policy_decision(incident_id, plan_id)
        assert latest is not None
        assert latest.decision == "REQUIRE_APPROVAL"
        assert latest.content_hash == "test_content_hash"
        assert latest.container_id == "demo-api-container"
        assert latest.binding_generation == 1
        assert latest.evidence_freshness_seconds == 42.5
        assert "APPROVAL_REQUIRED" in latest.reason_codes
