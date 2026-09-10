import logging
import os
import re
from datetime import UTC, datetime, timezone
from typing import Any

import httpx

from telemetryclient.schemas import AlertState, MetricQueryResult, MetricSample, MetricValue

logger = logging.getLogger(__name__)

MAX_PROMETHEUS_RESPONSE_BYTES = 1024 * 1024  # 1 MB response cap
MAX_METRIC_WINDOW_SECONDS = 900  # 15 minutes maximum window
DEFAULT_TIMEOUT_SECONDS = 5.0

# Approved resource-scoped query templates
APPROVED_TEMPLATES = {
    "container_up": 'up{{job="{job}"}}',
    "demo_api_cpu": 'rate(demo_api_cpu_seconds_total{{job="{job}"}}[1m])',
    "process_cpu": 'rate(process_cpu_seconds_total{{job="{job}"}}[1m])',
    "http_requests_total": 'demo_api_http_requests_total{{job="{job}"}}',
    "http_errors": 'sum(rate(demo_api_http_requests_total{{job="{job}",status=~"5.."}}[1m]))',
    "probe_success": 'probe_success{{instance=~".*/health/ready",job=~".*{job}.*"}}',
    "redis_connected": 'demo_api_redis_connected{{job="{job}"}}',
    "db_connections": "fault_injector_db_connections_active",
}


def validate_resource_scoped_query(query: str) -> None:
    """Ensures query contains resource-scoped selectors to prevent cluster-wide leaks."""
    # Queries must scope by job, instance, service, container, or be an approved target template
    required_selectors = [r"job\s*=", r"instance\s*=", r"service\s*=", r"container\s*="]
    if "fault_injector_db_connections_active" in query:
        return
    if not any(re.search(pat, query) for pat in required_selectors):
        raise ValueError(
            f"Query is not strictly resource-scoped: '{query}'. Must contain a selector such as job=, instance=, service=, or container=."
        )


class PrometheusClient:
    """Typed, resource-scoped Prometheus telemetry client with timeouts, byte limits, and window bounds."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = MAX_PROMETHEUS_RESPONSE_BYTES,
        client: httpx.AsyncClient | None = None,
    ):
        raw_url = base_url or os.getenv("PROMETHEUS_URL", "http://localhost:9090")
        self.base_url = raw_url.rstrip("/")
        self.timeout = timeout

        self.max_bytes = max_bytes
        self._external_client = client

    def _get_client(self) -> httpx.AsyncClient:
        if self._external_client is not None:
            return self._external_client
        return httpx.AsyncClient(timeout=self.timeout)

    async def query_instant(self, query: str, time: datetime | None = None) -> MetricQueryResult:
        """Executes an instant query against /api/v1/query with resource validation and byte caps."""
        validate_resource_scoped_query(query)
        params: dict[str, Any] = {"query": query}
        if time is not None:
            params["time"] = time.isoformat()

        url = f"{self.base_url}/api/v1/query"
        return await self._execute_query(url, params, query)

    async def query_range(
        self,
        query: str,
        start: datetime,
        end: datetime,
        step: str = "15s",
    ) -> MetricQueryResult:
        """Executes a range query against /api/v1/query_range bounded to at most 15 minutes."""
        validate_resource_scoped_query(query)

        # Enforce maximum window of 15 minutes
        duration = (end - start).total_seconds()
        if duration > MAX_METRIC_WINDOW_SECONDS:
            logger.warning(
                f"Requested range duration {duration}s exceeds {MAX_METRIC_WINDOW_SECONDS}s cap. Clamping start."
            )
            start = datetime.fromtimestamp(end.timestamp() - MAX_METRIC_WINDOW_SECONDS, tz=UTC)

        params: dict[str, Any] = {
            "query": query,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "step": step,
        }
        url = f"{self.base_url}/api/v1/query_range"
        return await self._execute_query(url, params, query)

    async def get_active_alerts(self) -> list[AlertState]:
        """Queries /api/v1/alerts to retrieve current active Prometheus alerts."""
        url = f"{self.base_url}/api/v1/alerts"
        client = self._get_client()
        should_close = self._external_client is None

        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            alerts_data = data.get("data", {}).get("alerts", [])
            results: list[AlertState] = []
            for item in alerts_data:
                active_at_str = item.get("activeAt")
                active_at = None
                if active_at_str:
                    try:
                        active_at = datetime.fromisoformat(active_at_str.replace("Z", "+00:00"))
                    except Exception:
                        pass
                results.append(
                    AlertState(
                        name=item.get("labels", {}).get("alertname", "UnknownAlert"),
                        state=item.get("state", "inactive"),
                        labels=item.get("labels", {}),
                        annotations=item.get("annotations", {}),
                        active_at=active_at,
                        value=item.get("value"),
                    )
                )
            return results
        except Exception as exc:
            logger.warning(f"Failed to query Prometheus active alerts: {exc}")
            return []
        finally:
            if should_close:
                await client.aclose()

    async def _execute_query(
        self, url: str, params: dict[str, Any], query: str
    ) -> MetricQueryResult:
        client = self._get_client()
        should_close = self._external_client is None

        try:
            resp = await client.get(url, params=params)
            content_bytes = resp.content
            byte_size = len(content_bytes)

            is_truncated = False
            if byte_size > self.max_bytes:
                logger.warning(
                    f"Prometheus response {byte_size} bytes exceeded cap {self.max_bytes}. Truncating payload."
                )
                is_truncated = True
                content_bytes = content_bytes[: self.max_bytes]
                # Return empty/truncated result to avoid OOM
                return MetricQueryResult(
                    query=query,
                    status="error",
                    is_truncated=True,
                    byte_size=byte_size,
                    error_message=f"Response exceeded {self.max_bytes} byte limit",
                )

            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "success":
                return MetricQueryResult(
                    query=query,
                    status="error",
                    byte_size=byte_size,
                    error_message=data.get("error", "Unknown Prometheus error"),
                )

            result_data = data.get("data", {}).get("result", [])
            if not result_data:
                return MetricQueryResult(
                    query=query,
                    status="empty",
                    byte_size=byte_size,
                    samples=[],
                )

            samples: list[MetricSample] = []
            now = datetime.now(UTC)
            min_age_seconds: float | None = None

            for series in result_data:
                metric_labels = series.get("metric", {})
                values_list: list[MetricValue] = []

                if "values" in series:  # Matrix (range query)
                    for ts_val in series["values"]:
                        ts, val_str = ts_val
                        dt = datetime.fromtimestamp(ts, tz=UTC)
                        try:
                            val = float(val_str)
                            values_list.append(MetricValue(timestamp=dt, value=val))
                        except (ValueError, TypeError):
                            pass
                elif "value" in series:  # Vector (instant query)
                    ts, val_str = series["value"]
                    dt = datetime.fromtimestamp(ts, tz=UTC)
                    try:
                        val = float(val_str)
                        values_list.append(MetricValue(timestamp=dt, value=val))
                    except (ValueError, TypeError):
                        pass

                latest_val = values_list[-1].value if values_list else None
                latest_ts = values_list[-1].timestamp if values_list else None

                if latest_ts:
                    age = (now - latest_ts).total_seconds()
                    if min_age_seconds is None or age < min_age_seconds:
                        min_age_seconds = age

                samples.append(
                    MetricSample(
                        metric=metric_labels,
                        values=values_list,
                        latest_value=latest_val,
                        latest_timestamp=latest_ts,
                    )
                )

            return MetricQueryResult(
                query=query,
                status="success",
                samples=samples,
                freshness_seconds=min_age_seconds,
                is_truncated=is_truncated,
                byte_size=byte_size,
            )

        except httpx.TimeoutException as exc:
            logger.warning(f"Prometheus query timed out after {self.timeout}s: {exc}")
            return MetricQueryResult(
                query=query,
                status="unavailable",
                error_message=f"Query timed out after {self.timeout}s",
            )
        except httpx.ConnectError as exc:
            logger.warning(f"Prometheus connection failed: {exc}")
            return MetricQueryResult(
                query=query,
                status="unavailable",
                error_message=f"Prometheus service unavailable: {exc}",
            )
        except Exception as exc:
            logger.warning(f"Prometheus query error: {exc}")
            return MetricQueryResult(
                query=query,
                status="error",
                error_message=str(exc),
            )
        finally:
            if should_close:
                await client.aclose()
