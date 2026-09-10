from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from telemetryclient.loki import (
    MAX_LOKI_LINES,
    MAX_LOKI_RESPONSE_BYTES,
    MAX_LOKI_WINDOW_SECONDS,
    LokiClient,
    validate_resource_scoped_logql,
)
from telemetryclient.prometheus import (
    MAX_METRIC_WINDOW_SECONDS,
    MAX_PROMETHEUS_RESPONSE_BYTES,
    PrometheusClient,
    validate_resource_scoped_query,
)


def test_prometheus_resource_scoping_validation():
    # Valid scoped queries
    validate_resource_scoped_query('up{job="demo-api"}')
    validate_resource_scoped_query('rate(demo_api_cpu_seconds_total{container="demo"}[1m])')
    validate_resource_scoped_query('probe_success{instance="http://localhost:8000"}')
    validate_resource_scoped_query("fault_injector_db_connections_active")

    # Unscoped queries rejected
    with pytest.raises(ValueError, match="not strictly resource-scoped"):
        validate_resource_scoped_query("up")

    with pytest.raises(ValueError, match="not strictly resource-scoped"):
        validate_resource_scoped_query("rate(process_cpu_seconds_total[1m])")


def test_loki_resource_scoping_validation():
    # Valid LogQL
    validate_resource_scoped_logql('{job="demo-api"}')
    validate_resource_scoped_logql('{container="c123"}')
    validate_resource_scoped_logql('{service="demo-api"} |= "error"')

    # Unscoped LogQL rejected
    with pytest.raises(ValueError, match="not strictly resource-scoped"):
        validate_resource_scoped_logql("{}")

    with pytest.raises(ValueError, match="not strictly resource-scoped"):
        validate_resource_scoped_logql('{cluster="local"}')


@pytest.mark.asyncio
async def test_prometheus_query_range_window_clamping():
    client = PrometheusClient()
    now = datetime.now(UTC)
    start_old = now - timedelta(minutes=30)  # Exceeds 15m cap

    with patch.object(client, "_execute_query", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = AsyncMock()
        await client.query_range('up{job="demo-api"}', start=start_old, end=now)
        assert mock_exec.called
        # Verify start was clamped to at most 15 minutes before end
        call_params = mock_exec.call_args[0][1]
        clamped_start = datetime.fromisoformat(call_params["start"])
        duration = (now - clamped_start).total_seconds()
        assert duration <= MAX_METRIC_WINDOW_SECONDS + 1


@pytest.mark.asyncio
async def test_loki_query_range_window_and_line_clamping():
    client = LokiClient()
    now = datetime.now(UTC)
    start_old = now - timedelta(minutes=15)  # Exceeds 5m cap

    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        from unittest.mock import MagicMock

        mock_resp = MagicMock()
        mock_resp.content = b'{"status":"success","data":{"result":[]}}'
        mock_resp.json.return_value = {"status": "success", "data": {"result": []}}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        res = await client.query_range('{job="demo-api"}', start=start_old, end=now, limit=1000)
        assert res.status == "empty"

        call_params = mock_get.call_args[1]["params"]
        assert int(call_params["limit"]) == MAX_LOKI_LINES


@pytest.mark.asyncio
async def test_prometheus_byte_limit_enforcement():
    client = PrometheusClient(max_bytes=100)  # Small limit for test
    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        from unittest.mock import MagicMock

        mock_resp = MagicMock()
        mock_resp.content = b"x" * 200  # Exceeds 100 bytes
        mock_get.return_value = mock_resp

        res = await client.query_instant('up{job="demo-api"}')
        assert res.status == "error"
        assert res.is_truncated is True
        assert "byte limit" in res.error_message


@pytest.mark.asyncio
async def test_telemetry_client_timeout_handling():
    client = PrometheusClient(timeout=1.0)
    with patch.object(httpx.AsyncClient, "get", side_effect=httpx.TimeoutException("timed out")):
        res = await client.query_instant('up{job="demo-api"}')
        assert res.status == "unavailable"
        assert "timed out" in res.error_message.lower()

    loki = LokiClient(timeout=1.0)
    now = datetime.now(UTC)
    with patch.object(httpx.AsyncClient, "get", side_effect=httpx.ConnectError("refused")):
        res = await loki.query_range('{job="demo-api"}', start=now - timedelta(minutes=1), end=now)
        assert res.status == "unavailable"
        assert "unavailable" in res.error_message.lower()
