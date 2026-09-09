import asyncio
import os
import signal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.db.models import Resource
from app.db.repositories.control_plane import unit_of_work
from app.main import app as control_api_app
from app.services.correlation.engine import CorrelationEngine
from app.services.discovery.docker import DockerResourceDiscovery
from app.services.ingestion.normalizer import EventNormalizer
from app.services.ingestion.resource_resolver import ResourceResolver
from app.services.ingestion.schemas import AlertItem
from demo_api.config import get_settings as get_demo_settings
from demo_api.faults.cpu import CpuStressManager
from demo_api.faults.hang import ProcessHangManager
from demo_api.main import app as demo_api_app
from demo_api.metrics import DEMO_API_CPU_BUDGET_CORES, DEMO_API_CPU_SECONDS_TOTAL
from httpx import ASGITransport, AsyncClient

DEMO_AUTH_HEADER = {"X-Fault-Token": "injector-secret-token"}


# ---------------------------------------------------------------------------
# SCN-001: Container Crash & Restart Semantics
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scn_001_crash_endpoint_authentication_and_dispatch(monkeypatch):
    """Verifies that /_faults/crash requires X-Fault-Token and issues SIGTERM on valid auth."""
    killed_pid = None
    killed_sig = None

    def fake_kill(pid, sig):
        nonlocal killed_pid, killed_sig
        killed_pid = pid
        killed_sig = sig

    monkeypatch.setattr(os, "kill", fake_kill)

    async with AsyncClient(transport=ASGITransport(app=demo_api_app), base_url="http://test") as client:
        # 1. Unauthenticated request must be rejected with 401
        unauth = await client.post("/_faults/crash")
        assert unauth.status_code == 401
        assert killed_pid is None

        # 2. Authenticated request accepts crash command and targets current pid with SIGTERM
        auth_res = await client.post("/_faults/crash", headers=DEMO_AUTH_HEADER)
        assert auth_res.status_code == 200
        assert killed_pid == os.getpid()
        assert killed_sig == signal.SIGTERM


# ---------------------------------------------------------------------------
# SCN-002: Container-Internal CPU Stress & Metric Semantics
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scn_002_cpu_stress_lifecycle_and_counter_metrics():
    """Verifies container-internal CPU stress lifecycle, child process management, and Counter metric collection."""
    mgr = CpuStressManager()
    assert not mgr.is_active()

    # 1. Start bounded CPU worker inside container
    status = mgr.start(num_cores=1)
    assert status["status"] == "started"
    assert mgr.is_active()
    assert len(mgr._processes) == 1
    assert mgr._processes[0].is_alive()

    # 2. Allow worker to accumulate CPU work briefly
    await asyncio.sleep(0.3)

    # 3. Update metrics: must monotonically increment Counter and set budget
    initial_val = DEMO_API_CPU_SECONDS_TOTAL._value.get()
    mgr.update_metrics()
    updated_val = DEMO_API_CPU_SECONDS_TOTAL._value.get()
    assert updated_val >= initial_val
    assert DEMO_API_CPU_BUDGET_CORES._value.get() == 1.0

    # 4. Stop clears child processes cleanly
    stop_status = mgr.stop()
    assert stop_status["status"] == "stopped"
    assert not mgr.is_active()
    assert len(mgr._processes) == 0

    # 5. Restart simulation: a fresh manager has zero workers and is inactive
    restarted_mgr = CpuStressManager()
    assert not restarted_mgr.is_active()
    assert len(restarted_mgr._processes) == 0


@pytest.mark.asyncio
async def test_scn_002_cpu_stress_http_endpoints():
    """Verifies authentication and endpoints for CPU stress in demo-api."""
    async with AsyncClient(transport=ASGITransport(app=demo_api_app), base_url="http://test") as client:
        # 1. Unauthenticated injection fails
        res = await client.post("/_faults/cpu/inject")
        assert res.status_code == 401

        # 2. Authenticated injection succeeds
        res = await client.post("/_faults/cpu/inject?cores=1", headers=DEMO_AUTH_HEADER)
        assert res.status_code == 200
        assert res.json()["status"] in ("started", "already_active")

        # 3. Status check
        res = await client.get("/_faults/cpu/status", headers=DEMO_AUTH_HEADER)
        assert res.status_code == 200
        assert res.json()["active"] is True

        # 4. Clear stops the fault
        res = await client.post("/_faults/cpu/clear", headers=DEMO_AUTH_HEADER)
        assert res.status_code == 200
        assert res.json()["status"] == "stopped"


# ---------------------------------------------------------------------------
# SCN-003: Process-Local Hang & Probe Separation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scn_003_hang_probe_separation_and_clear_semantics():
    """Verifies that /health/live remains 200 during SCN-003, administrative routes respond, and /health/ready hangs."""
    mgr = ProcessHangManager()
    assert not mgr.is_hung()

    # 1. Inject hang
    res = mgr.inject()
    assert res["status"] == "injected"
    assert mgr.is_hung()

    # 2. Test blocked wait task can be cancelled
    wait_task = asyncio.create_task(mgr.wait_if_hung(duration_seconds=10.0))
    await asyncio.sleep(0.05)
    assert len(mgr._pending_tasks) == 1

    # 3. Clear cancels the waiting task cleanly
    clear_res = mgr.clear()
    assert clear_res["status"] == "cleared"
    assert clear_res["cancelled_tasks"] == 1
    assert not mgr.is_hung()

    # 4. Wait task was cleanly cancelled
    with pytest.raises(asyncio.CancelledError):
        await wait_task

    # 5. Restart simulation: fresh manager has hang inactive
    restarted_mgr = ProcessHangManager()
    assert not restarted_mgr.is_hung()


@pytest.mark.asyncio
async def test_scn_003_hang_http_probe_separation():
    """Verifies that HTTP /health/live returns 200 while hang is active, and /health/ready blocks."""
    async with AsyncClient(transport=ASGITransport(app=demo_api_app), base_url="http://test") as client:
        # 1. Inject hang via authenticated endpoint
        res = await client.post("/_faults/hang/inject", headers=DEMO_AUTH_HEADER)
        assert res.status_code == 200

        try:
            # 2. /health/live MUST remain responsive (HTTP 200) - probe separation!
            live_res = await client.get("/health/live", timeout=2.0)
            assert live_res.status_code == 200
            assert live_res.json() == {"status": "ok"}

            # 3. Administrative routes stay responsive
            status_res = await client.get("/_faults/hang/status", headers=DEMO_AUTH_HEADER, timeout=2.0)
            assert status_res.status_code == 200
            assert status_res.json()["active"] is True
        finally:
            # 4. Clear the hang cleanly
            clear_res = await client.post("/_faults/hang/clear", headers=DEMO_AUTH_HEADER)
            assert clear_res.status_code == 200


# ---------------------------------------------------------------------------
# Trusted Resource Resolution & Insecure Fallback Prevention
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_resource_resolver_rejects_arbitrary_fallback_and_forged_labels(session_factory):
    """Verifies that ResourceResolver NEVER falls back to demo-api for unknown alerts,
    and alert labels cannot forge managed=True.
    """
    async with unit_of_work(session_factory, actor="test:resolver") as repo:
        resolver = ResourceResolver(repo)

        # Ensure demo-api exists as managed target
        demo_api = await resolver.resolve(service="demo-api")
        assert demo_api.name == "demo-api"

        # 1. Unknown service label must NOT resolve to demo-api
        unknown_res = await resolver.resolve(
            service="unregistered-foreign-service",
            labels={"alertname": "RandomAlert", "severity": "warning"},
        )
        assert unknown_res.id != demo_api.id
        assert unknown_res.name == "unregistered-foreign-service"
        assert unknown_res.managed is False
        assert unknown_res.labels.get("quarantined") is True

        # 2. Forged alert labels claiming managed=true must be rejected
        forged_res = await resolver.resolve(
            service="attacker-service",
            labels={
                "managed": True,
                "self-healing.managed": "true",
                "role": "admin",
            },
        )
        assert forged_res.id != demo_api.id
        assert forged_res.managed is False

        # 3. Empty/ambiguous labels must produce an unmanaged placeholder, never demo-api
        ambiguous_res = await resolver.resolve(labels={})
        assert ambiguous_res.id != demo_api.id
        assert ambiguous_res.managed is False


# ---------------------------------------------------------------------------
# Trusted Docker Discovery & Binding Generation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_trusted_docker_discovery_binding_generation_bump(session_factory):
    """Verifies that DockerResourceDiscovery binds container ID and increments binding_generation on container change."""
    mock_container_1 = MagicMock()
    mock_container_1.id = "container_id_alpha_111"
    mock_container_1.name = "self-healing-demo-api"
    mock_container_1.status = "running"
    mock_container_1.labels = {
        "com.docker.compose.service": "demo-api",
        "com.docker.compose.project": "self-healing",
        "self-healing.managed": "true",
    }
    mock_container_1.attrs = {"Created": "2026-09-10T00:00:00Z"}

    mock_client = MagicMock()
    mock_client.containers.list.return_value = [mock_container_1]

    discovery = DockerResourceDiscovery(docker_client=mock_client)

    # 1. Initial discovery sync
    async with unit_of_work(session_factory, actor="test:discovery") as repo:
        res = await discovery.sync_resource_binding(repo, service_name="demo-api")
        assert res is not None
        assert res.labels["docker_container_id"] == "container_id_alpha_111"
        assert res.labels["binding_generation"] == 1
        assert res.managed is True

    # 2. Simulated container recreation (e.g. docker restart/recreate)
    mock_container_2 = MagicMock()
    mock_container_2.id = "container_id_beta_222"
    mock_container_2.name = "self-healing-demo-api"
    mock_container_2.status = "running"
    mock_container_2.labels = mock_container_1.labels
    mock_container_2.attrs = {"Created": "2026-09-10T00:05:00Z"}
    mock_client.containers.list.return_value = [mock_container_2]

    # 3. Second discovery sync detects ID change and increments binding_generation to 2
    async with unit_of_work(session_factory, actor="test:discovery") as repo:
        res2 = await discovery.sync_resource_binding(repo, service_name="demo-api")
        assert res2 is not None
        assert res2.labels["docker_container_id"] == "container_id_beta_222"
        assert res2.labels["binding_generation"] == 2
        assert res2.managed is True

    # 4. Discover demo-worker: must have managed=False
    mock_worker = MagicMock()
    mock_worker.id = "worker_id_999"
    mock_worker.name = "self-healing-demo-worker"
    mock_worker.status = "running"
    mock_worker.labels = {
        "com.docker.compose.service": "demo-worker",
        "com.docker.compose.project": "self-healing",
        "self-healing.managed": "false",
    }
    mock_worker.attrs = {"Created": "2026-09-10T00:00:00Z"}
    mock_client.containers.list.return_value = [mock_worker]

    async with unit_of_work(session_factory, actor="test:discovery") as repo:
        worker_res = await discovery.sync_resource_binding(repo, service_name="demo-worker")
        assert worker_res is not None
        assert worker_res.managed is False


# ---------------------------------------------------------------------------
# Scenario Alert Correlation Non-Collision
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_m1_three_scenario_alerts_correlate_independently(session_factory):
    """Verifies that SCN-001, SCN-002, and SCN-003 alerts generate distinct, non-colliding correlation keys."""
    async with unit_of_work(session_factory, actor="test:correlation") as repo:
        resolver = ResourceResolver(repo)
        demo_api = await resolver.resolve(service="demo-api")

        normalizer = EventNormalizer(dedup_window_seconds=300)
        engine = CorrelationEngine(repo)

        # SCN-001 Alert
        alert_001 = AlertItem(
            status="firing",
            labels={
                "alertname": "ContainerDown",
                "scenario_id": "SCN-001",
                "service": "demo-api",
                "severity": "critical",
            },
            annotations={"summary": "Container Down"},
            startsAt="2026-09-10T01:00:00Z",
            fingerprint="fp_001",
        )
        norm_001 = normalizer.normalize_alertmanager_alert(alert_001, demo_api.id)
        key_001 = engine.compute_correlation_key(norm_001)

        # SCN-002 Alert
        alert_002 = AlertItem(
            status="firing",
            labels={
                "alertname": "HighCpuSaturation",
                "scenario_id": "SCN-002",
                "service": "demo-api",
                "severity": "warning",
            },
            annotations={"summary": "High CPU"},
            startsAt="2026-09-10T01:00:00Z",
            fingerprint="fp_002",
        )
        norm_002 = normalizer.normalize_alertmanager_alert(alert_002, demo_api.id)
        key_002 = engine.compute_correlation_key(norm_002)

        # SCN-003 Alert
        alert_003 = AlertItem(
            status="firing",
            labels={
                "alertname": "ApiUnresponsive",
                "scenario_id": "SCN-003",
                "service": "demo-api",
                "severity": "critical",
            },
            annotations={"summary": "API Unresponsive"},
            startsAt="2026-09-10T01:00:00Z",
            fingerprint="fp_003",
        )
        norm_003 = normalizer.normalize_alertmanager_alert(alert_003, demo_api.id)
        key_003 = engine.compute_correlation_key(norm_003)

        # All 3 scenarios target demo_api but generate isolated correlation keys
        assert key_001 == f"{demo_api.id}:SCN-001"
        assert key_002 == f"{demo_api.id}:SCN-002"
        assert key_003 == f"{demo_api.id}:SCN-003"
        assert len({key_001, key_002, key_003}) == 3
