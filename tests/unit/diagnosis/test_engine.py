import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from diagnosis.deterministic.engine import DeterministicDiagnosisEngine
from diagnosis.schemas import ObservationBundle
from sharedmodels.enums import RootCause

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "telemetry" / "m1"


def _load_fixture(filename: str) -> dict:
    with (FIXTURES_DIR / filename).open(encoding="utf-8") as f:
        return json.load(f)


def _fixture_to_obs(data: dict) -> ObservationBundle:
    container = data.get("container", {})
    metrics = data.get("metrics", {})
    completeness = data.get("completeness", {"prometheus": "COMPLETE", "loki": "COMPLETE"})

    return ObservationBundle(
        resource_id=UUID(data["resource_id"]),
        service_name=data["service_name"],
        container_id=data.get("container_id"),
        binding_generation=data.get("binding_generation"),
        container_status=container.get("state", "unknown"),
        exit_code=container.get("exit_code"),
        health_status=container.get("health_status"),
        up_metric=metrics.get("up"),
        normalized_cpu=metrics.get("normalized_cpu"),
        raw_cpu_seconds_rate=metrics.get("raw_cpu_seconds_rate"),
        readiness_success_ratio=metrics.get("probe_success"),
        latest_readiness_code=200 if metrics.get("probe_success") == 1.0 else 503,
        redis_connected=bool(metrics.get("redis_connected") >= 1.0)
        if metrics.get("redis_connected") is not None
        else None,
        db_connections_active=int(metrics.get("db_connections"))
        if metrics.get("db_connections") is not None
        else None,
        firing_alerts=data.get("alerts", []),
        completeness=completeness,
        freshness_seconds=metrics.get("freshness_seconds"),
        is_telemetry_unavailable=completeness.get("prometheus") == "UNAVAILABLE",
    )


@pytest.mark.asyncio
async def test_engine_scn001_container_crash():
    engine = DeterministicDiagnosisEngine()
    data = _load_fixture("scn001_container_crash.json")
    obs = _fixture_to_obs(data)
    incident_id = uuid4()

    diag = await engine.diagnose(incident_id, obs)
    assert diag.root_cause == RootCause.CONTAINER_STOPPED
    assert diag.confidence >= 0.85
    assert diag.is_actionable is True
    assert diag.rule_id == "RULE-001-CONTAINER-STOPPED"
    assert diag.rule_version == "1.0"


@pytest.mark.asyncio
async def test_engine_scn002_cpu_saturation():
    engine = DeterministicDiagnosisEngine()
    data = _load_fixture("scn002_cpu_saturation.json")
    obs = _fixture_to_obs(data)
    incident_id = uuid4()

    diag = await engine.diagnose(incident_id, obs)
    assert diag.root_cause == RootCause.CPU_SATURATION
    assert diag.confidence >= 0.85
    assert diag.is_actionable is True
    assert diag.rule_id == "RULE-002-CPU-SATURATION"


@pytest.mark.asyncio
async def test_engine_scn003_api_unresponsive():
    engine = DeterministicDiagnosisEngine()
    data = _load_fixture("scn003_api_unresponsive.json")
    obs = _fixture_to_obs(data)
    incident_id = uuid4()

    diag = await engine.diagnose(incident_id, obs)
    assert diag.root_cause == RootCause.API_UNRESPONSIVE
    assert diag.confidence >= 0.85
    assert diag.is_actionable is True
    assert diag.rule_id == "RULE-003-API-UNRESPONSIVE"


@pytest.mark.asyncio
async def test_engine_healthy_workload():
    engine = DeterministicDiagnosisEngine()
    data = _load_fixture("healthy_workload.json")
    obs = _fixture_to_obs(data)
    incident_id = uuid4()

    diag = await engine.diagnose(incident_id, obs)
    assert diag.root_cause == RootCause.NO_ACTIVE_FAULT
    assert diag.is_actionable is False
    assert diag.escalation_reason is not None


@pytest.mark.asyncio
async def test_engine_dependency_outage():
    engine = DeterministicDiagnosisEngine()
    data = _load_fixture("dependency_outage.json")
    obs = _fixture_to_obs(data)
    incident_id = uuid4()

    diag = await engine.diagnose(incident_id, obs)
    assert diag.root_cause == RootCause.DEPENDENCY_UNAVAILABLE
    assert diag.is_actionable is False
    assert "Redis" in diag.escalation_reason


@pytest.mark.asyncio
async def test_engine_contradictory_evidence():
    engine = DeterministicDiagnosisEngine()
    data = _load_fixture("contradictory_evidence.json")
    obs = _fixture_to_obs(data)
    incident_id = uuid4()

    diag = await engine.diagnose(incident_id, obs)
    assert diag.is_actionable is False
    assert len(diag.contradictory_findings) > 0


@pytest.mark.asyncio
async def test_engine_forged_scenario_label():
    """Forged SCN-001 label on healthy container MUST NOT diagnose crash."""
    engine = DeterministicDiagnosisEngine()
    data = _load_fixture("forged_scenario_label.json")
    obs = _fixture_to_obs(data)
    incident_id = uuid4()

    diag = await engine.diagnose(incident_id, obs)
    assert diag.root_cause != RootCause.CONTAINER_STOPPED
    assert diag.root_cause == RootCause.NO_ACTIVE_FAULT
    assert diag.is_actionable is False


@pytest.mark.asyncio
async def test_engine_unknown_resource():
    engine = DeterministicDiagnosisEngine()
    data = _load_fixture("unknown_resource.json")
    obs = _fixture_to_obs(data)
    incident_id = uuid4()

    diag = await engine.diagnose(incident_id, obs)
    assert diag.root_cause == RootCause.INSUFFICIENT_EVIDENCE
    assert diag.is_actionable is False
    assert "not found" in diag.escalation_reason


@pytest.mark.asyncio
async def test_engine_missing_telemetry():
    engine = DeterministicDiagnosisEngine()
    data = _load_fixture("missing_telemetry.json")
    obs = _fixture_to_obs(data)
    incident_id = uuid4()

    diag = await engine.diagnose(incident_id, obs)
    assert diag.root_cause == RootCause.INSUFFICIENT_EVIDENCE
    assert diag.is_actionable is False
    assert "unavailable" in diag.escalation_reason.lower()


@pytest.mark.asyncio
async def test_engine_retry_parent_linking():
    engine = DeterministicDiagnosisEngine()
    data = _load_fixture("scn001_container_crash.json")
    obs = _fixture_to_obs(data)
    incident_id = uuid4()
    parent_diag_id = uuid4()

    diag = await engine.diagnose(incident_id, obs, parent_diagnosis_id=parent_diag_id)
    assert diag.parent_diagnosis_id == parent_diag_id
    assert diag.root_cause == RootCause.CONTAINER_STOPPED


@pytest.mark.asyncio
async def test_engine_stale_data():
    engine = DeterministicDiagnosisEngine()
    data = _load_fixture("stale_data.json")
    obs = _fixture_to_obs(data)
    incident_id = uuid4()

    diag = await engine.diagnose(incident_id, obs)
    assert diag.root_cause == RootCause.INSUFFICIENT_EVIDENCE
    assert diag.is_actionable is False
    assert "stale" in diag.escalation_reason.lower()
