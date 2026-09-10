from uuid import uuid4

import pytest
from diagnosis.deterministic.rules import (
    ApiUnresponsiveRule,
    ContainerStoppedRule,
    CpuSaturationRule,
    DependencyUnavailableRule,
    NoActiveFaultRule,
)
from diagnosis.schemas import ObservationBundle
from sharedmodels.enums import RootCause


def test_container_stopped_rule_matches():
    rule = ContainerStoppedRule()
    obs = ObservationBundle(
        resource_id=uuid4(),
        service_name="demo-api",
        container_status="exited",
        exit_code=137,
        up_metric=0.0,
        firing_alerts=["ContainerDown"],
    )

    res = rule.evaluate(obs)
    assert res.matched is True
    assert res.root_cause == RootCause.CONTAINER_STOPPED
    assert res.confidence >= 0.90
    assert res.is_actionable is True


def test_container_stopped_rule_contradiction():
    rule = ContainerStoppedRule()
    obs = ObservationBundle(
        resource_id=uuid4(),
        service_name="demo-api",
        container_status="running",
        up_metric=1.0,
        latest_readiness_code=200,
        firing_alerts=["ContainerDown"],  # Stale or misconfigured alert
    )

    res = rule.evaluate(obs)
    assert res.matched is False
    assert len(res.contradictory_findings) > 0
    assert "cannot be stopped" in res.contradictory_findings[0]


def test_dependency_unavailable_rule_redis():
    rule = DependencyUnavailableRule()
    obs = ObservationBundle(
        resource_id=uuid4(),
        service_name="demo-api",
        container_status="running",
        redis_connected=False,
        firing_alerts=["RedisUnreachable"],
    )

    res = rule.evaluate(obs)
    assert res.matched is True
    assert res.root_cause == RootCause.DEPENDENCY_UNAVAILABLE
    assert res.is_actionable is False
    assert res.escalation_reason is not None
    assert "Redis" in res.escalation_reason


def test_dependency_unavailable_rule_postgres():
    rule = DependencyUnavailableRule()
    obs = ObservationBundle(
        resource_id=uuid4(),
        service_name="demo-api",
        container_status="running",
        db_connections_active=8,
        firing_alerts=["PostgresPoolExhausted"],
    )

    res = rule.evaluate(obs)
    assert res.matched is True
    assert res.root_cause == RootCause.DEPENDENCY_UNAVAILABLE
    assert res.is_actionable is False
    assert "PostgreSQL" in res.escalation_reason


def test_cpu_saturation_rule_matches():
    rule = CpuSaturationRule()
    obs = ObservationBundle(
        resource_id=uuid4(),
        service_name="demo-api",
        container_status="running",
        normalized_cpu=0.88,
        raw_cpu_seconds_rate=0.88,
        firing_alerts=["HighCpuSaturation"],
    )

    res = rule.evaluate(obs)
    assert res.matched is True
    assert res.root_cause == RootCause.CPU_SATURATION
    assert res.confidence >= 0.85
    assert res.is_actionable is True


def test_cpu_saturation_rule_contradicted_by_host_spike():
    rule = CpuSaturationRule()
    # Alert is firing, but target container normalized CPU is low (< 0.50)
    obs = ObservationBundle(
        resource_id=uuid4(),
        service_name="demo-api",
        container_status="running",
        normalized_cpu=0.15,
        firing_alerts=["HighCpuSaturation"],
    )

    res = rule.evaluate(obs)
    assert res.matched is False
    assert len(res.contradictory_findings) > 0
    assert "external host or injector" in res.contradictory_findings[0]


def test_api_unresponsive_rule_matches():
    rule = ApiUnresponsiveRule()
    obs = ObservationBundle(
        resource_id=uuid4(),
        service_name="demo-api",
        container_status="running",
        readiness_success_ratio=0.0,
        latest_readiness_code=503,
        normalized_cpu=0.10,
        redis_connected=True,
        db_connections_active=2,
        firing_alerts=["ApiUnresponsive"],
    )

    res = rule.evaluate(obs)
    assert res.matched is True
    assert res.root_cause == RootCause.API_UNRESPONSIVE
    assert res.confidence >= 0.85
    assert res.is_actionable is True


def test_api_unresponsive_contradicted_by_healthy_readiness():
    rule = ApiUnresponsiveRule()
    obs = ObservationBundle(
        resource_id=uuid4(),
        service_name="demo-api",
        container_status="running",
        readiness_success_ratio=1.0,
        latest_readiness_code=200,
        firing_alerts=["ApiUnresponsive"],
    )

    res = rule.evaluate(obs)
    assert res.matched is False
    assert len(res.contradictory_findings) > 0
    assert "probe returns HTTP 200" in res.contradictory_findings[0]


def test_no_active_fault_rule_matches():
    rule = NoActiveFaultRule()
    obs = ObservationBundle(
        resource_id=uuid4(),
        service_name="demo-api",
        container_status="running",
        up_metric=1.0,
        readiness_success_ratio=1.0,
        latest_readiness_code=200,
        normalized_cpu=0.15,
        redis_connected=True,
        db_connections_active=2,
    )

    res = rule.evaluate(obs)
    assert res.matched is True
    assert res.root_cause == RootCause.NO_ACTIVE_FAULT
    assert res.confidence >= 0.90
    assert res.is_actionable is False
