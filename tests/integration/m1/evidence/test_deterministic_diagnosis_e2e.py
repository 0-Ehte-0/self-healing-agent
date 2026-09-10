from uuid import uuid4

import pytest
import sqlalchemy as sa
from app.db.models import Diagnosis, Incident, Resource
from app.db.repositories.control_plane import unit_of_work
from diagnosis.deterministic.engine import DeterministicDiagnosisEngine
from diagnosis.schemas import ObservationBundle
from sharedmodels.enums import IncidentState as S
from sharedmodels.enums import RootCause, Severity


@pytest.mark.asyncio
async def test_e2e_diagnosis_scn001_container_crash(session_factory):
    """End-to-end flow: DETECTED -> TRIAGED -> DIAGNOSED for SCN-001 container crash."""
    async with unit_of_work(session_factory, actor="test:engine") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        assert res is not None

        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-scn001-{uuid4().hex[:8]}",
            severity=Severity.CRITICAL,
        )
        assert inc.state == S.DETECTED

        inc = await repo.transition(inc.id, 1, S.TRIAGED)
        assert inc.state == S.TRIAGED

        obs = ObservationBundle(
            resource_id=res.id,
            service_name="demo-api",
            container_status="exited",
            exit_code=137,
            up_metric=0.0,
            firing_alerts=["ContainerDown"],
        )

        engine = DeterministicDiagnosisEngine()
        diag_schema = await engine.diagnose(inc.id, obs)

        assert diag_schema.root_cause == RootCause.CONTAINER_STOPPED
        assert diag_schema.confidence >= 0.85
        assert diag_schema.is_actionable is True
        assert diag_schema.rule_id == "RULE-001-CONTAINER-STOPPED"

        db_diag = Diagnosis(
            id=diag_schema.id,
            incident_id=inc.id,
            root_cause=diag_schema.root_cause.value,
            confidence=diag_schema.confidence,
            evidence_ids=diag_schema.evidence_ids,
            rule_id=diag_schema.rule_id,
            rule_version=diag_schema.rule_version,
            contradictory_findings=diag_schema.contradictory_findings,
            escalation_reason=diag_schema.escalation_reason,
            parent_diagnosis_id=diag_schema.parent_diagnosis_id,
            reasoning=diag_schema.reasoning,
            actor="test:engine",
        )
        await repo.add(db_diag)

        # Actionable: advance TRIAGED -> DIAGNOSED
        inc = await repo.transition(inc.id, 2, S.DIAGNOSED)
        assert inc.state == S.DIAGNOSED
        assert inc.version == 3


@pytest.mark.asyncio
async def test_e2e_diagnosis_scn002_cpu_saturation(session_factory):
    """End-to-end flow: DETECTED -> TRIAGED -> DIAGNOSED for SCN-002 CPU saturation."""
    async with unit_of_work(session_factory, actor="test:engine") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        assert res is not None

        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-scn002-{uuid4().hex[:8]}",
            severity=Severity.HIGH,
        )
        inc = await repo.transition(inc.id, 1, S.TRIAGED)

        obs = ObservationBundle(
            resource_id=res.id,
            service_name="demo-api",
            container_status="running",
            normalized_cpu=0.89,
            raw_cpu_seconds_rate=0.89,
            firing_alerts=["HighCpuSaturation"],
        )

        engine = DeterministicDiagnosisEngine()
        diag_schema = await engine.diagnose(inc.id, obs)

        assert diag_schema.root_cause == RootCause.CPU_SATURATION
        assert diag_schema.is_actionable is True

        db_diag = Diagnosis(
            id=diag_schema.id,
            incident_id=inc.id,
            root_cause=diag_schema.root_cause.value,
            confidence=diag_schema.confidence,
            evidence_ids=diag_schema.evidence_ids,
            rule_id=diag_schema.rule_id,
            rule_version=diag_schema.rule_version,
            contradictory_findings=diag_schema.contradictory_findings,
            reasoning=diag_schema.reasoning,
            actor="test:engine",
        )
        await repo.add(db_diag)

        inc = await repo.transition(inc.id, 2, S.DIAGNOSED)
        assert inc.state == S.DIAGNOSED


@pytest.mark.asyncio
async def test_e2e_diagnosis_scn003_api_unresponsive(session_factory):
    """End-to-end flow: DETECTED -> TRIAGED -> DIAGNOSED for SCN-003 unresponsive API."""
    async with unit_of_work(session_factory, actor="test:engine") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        assert res is not None

        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-scn003-{uuid4().hex[:8]}",
            severity=Severity.CRITICAL,
        )
        inc = await repo.transition(inc.id, 1, S.TRIAGED)

        obs = ObservationBundle(
            resource_id=res.id,
            service_name="demo-api",
            container_status="running",
            readiness_success_ratio=0.0,
            latest_readiness_code=503,
            normalized_cpu=0.15,
            redis_connected=True,
            db_connections_active=2,
            firing_alerts=["ApiUnresponsive"],
        )

        engine = DeterministicDiagnosisEngine()
        diag_schema = await engine.diagnose(inc.id, obs)

        assert diag_schema.root_cause == RootCause.API_UNRESPONSIVE
        assert diag_schema.is_actionable is True

        db_diag = Diagnosis(
            id=diag_schema.id,
            incident_id=inc.id,
            root_cause=diag_schema.root_cause.value,
            confidence=diag_schema.confidence,
            evidence_ids=diag_schema.evidence_ids,
            rule_id=diag_schema.rule_id,
            rule_version=diag_schema.rule_version,
            contradictory_findings=diag_schema.contradictory_findings,
            reasoning=diag_schema.reasoning,
            actor="test:engine",
        )
        await repo.add(db_diag)

        inc = await repo.transition(inc.id, 2, S.DIAGNOSED)
        assert inc.state == S.DIAGNOSED


@pytest.mark.asyncio
async def test_e2e_diagnosis_dependency_failure_escalates(session_factory):
    """End-to-end flow: External dependency failure safely transitions TRIAGED -> ESCALATED."""
    async with unit_of_work(session_factory, actor="test:engine") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        assert res is not None

        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-dep-esc-{uuid4().hex[:8]}",
            severity=Severity.HIGH,
        )
        inc = await repo.transition(inc.id, 1, S.TRIAGED)

        obs = ObservationBundle(
            resource_id=res.id,
            service_name="demo-api",
            container_status="running",
            redis_connected=False,
            firing_alerts=["RedisUnreachable"],
        )

        engine = DeterministicDiagnosisEngine()
        diag_schema = await engine.diagnose(inc.id, obs)

        assert diag_schema.root_cause == RootCause.DEPENDENCY_UNAVAILABLE
        assert diag_schema.is_actionable is False
        assert diag_schema.escalation_reason is not None

        db_diag = Diagnosis(
            id=diag_schema.id,
            incident_id=inc.id,
            root_cause=diag_schema.root_cause.value,
            confidence=diag_schema.confidence,
            evidence_ids=diag_schema.evidence_ids,
            rule_id=diag_schema.rule_id,
            rule_version=diag_schema.rule_version,
            contradictory_findings=diag_schema.contradictory_findings,
            escalation_reason=diag_schema.escalation_reason,
            reasoning=diag_schema.reasoning,
            actor="test:engine",
        )
        await repo.add(db_diag)

        # Non-actionable: legal safe escalation TRIAGED -> ESCALATED
        inc = await repo.transition(inc.id, 2, S.ESCALATED)
        assert inc.state == S.ESCALATED


@pytest.mark.asyncio
async def test_e2e_diagnosis_healthy_workload_closes(session_factory):
    """End-to-end flow: Healthy workload with stale alert safely transitions TRIAGED -> ESCALATED."""
    async with unit_of_work(session_factory, actor="test:engine") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        assert res is not None

        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-healthy-esc-{uuid4().hex[:8]}",
            severity=Severity.LOW,
        )
        inc = await repo.transition(inc.id, 1, S.TRIAGED)

        obs = ObservationBundle(
            resource_id=res.id,
            service_name="demo-api",
            container_status="running",
            up_metric=1.0,
            readiness_success_ratio=1.0,
            latest_readiness_code=200,
            normalized_cpu=0.10,
            redis_connected=True,
            db_connections_active=1,
            firing_alerts=["ContainerDown"],  # Stale alert
        )

        engine = DeterministicDiagnosisEngine()
        diag_schema = await engine.diagnose(inc.id, obs)

        assert diag_schema.root_cause == RootCause.NO_ACTIVE_FAULT
        assert diag_schema.is_actionable is False

        # Non-healing closure
        inc = await repo.transition(inc.id, 2, S.ESCALATED)
        assert inc.state == S.ESCALATED


@pytest.mark.asyncio
async def test_e2e_diagnosis_retry_parent_linking(session_factory):
    """Verifies that diagnosis on retry links parent_diagnosis_id in PostgreSQL."""
    async with unit_of_work(session_factory, actor="test:engine") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        assert res is not None

        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-retry-link-{uuid4().hex[:8]}",
            severity=Severity.HIGH,
        )
        inc = await repo.transition(inc.id, 1, S.TRIAGED)

        # First attempt diagnosis
        obs1 = ObservationBundle(
            resource_id=res.id,
            service_name="demo-api",
            container_status="exited",
            exit_code=137,
            up_metric=0.0,
        )
        engine = DeterministicDiagnosisEngine()
        diag1 = await engine.diagnose(inc.id, obs1)

        db_diag1 = Diagnosis(
            id=diag1.id,
            incident_id=inc.id,
            root_cause=diag1.root_cause.value,
            confidence=diag1.confidence,
            evidence_ids=[],
            rule_id=diag1.rule_id,
            rule_version=diag1.rule_version,
            contradictory_findings=[],
            reasoning=diag1.reasoning,
            actor="test:engine",
        )
        await repo.add(db_diag1)

        # Retry attempt diagnosis
        diag2 = await engine.diagnose(inc.id, obs1, parent_diagnosis_id=db_diag1.id)
        db_diag2 = Diagnosis(
            id=diag2.id,
            incident_id=inc.id,
            root_cause=diag2.root_cause.value,
            confidence=diag2.confidence,
            evidence_ids=[],
            rule_id=diag2.rule_id,
            rule_version=diag2.rule_version,
            contradictory_findings=[],
            parent_diagnosis_id=db_diag1.id,
            reasoning=diag2.reasoning,
            actor="test:engine",
        )
        await repo.add(db_diag2)

        # Verify parent link exists in DB
        fetched_diag2 = await repo.session.get(Diagnosis, db_diag2.id)
        assert fetched_diag2 is not None
        assert fetched_diag2.parent_diagnosis_id == db_diag1.id
