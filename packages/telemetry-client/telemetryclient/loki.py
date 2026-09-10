import logging
import re
from datetime import UTC, datetime, timezone
from typing import Any

import httpx

from telemetryclient.schemas import LogEntry, LogQueryResult

logger = logging.getLogger(__name__)

MAX_LOKI_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MB response cap
MAX_LOKI_LINES = 500  # 500 lines limit
MAX_LOKI_WINDOW_SECONDS = 300  # 5 minutes maximum window
DEFAULT_TIMEOUT_SECONDS = 5.0


def validate_resource_scoped_logql(query: str) -> None:
    """Ensures LogQL query contains resource-scoped stream selectors."""
    required_selectors = [r"job\s*=", r"container\s*=", r"service\s*=", r"app\s*="]
    if not any(re.search(pat, query) for pat in required_selectors):
        raise ValueError(
            f"LogQL query is not strictly resource-scoped: '{query}'. Must contain a stream selector such as job=, container=, service=, or app=."
        )


class LokiClient:
    """Typed, resource-scoped Loki log client with timeouts, byte limits, and window bounds."""

    def __init__(
        self,
        base_url: str = "http://localhost:3100",
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = MAX_LOKI_RESPONSE_BYTES,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_bytes = max_bytes
        self._external_client = client

    def _get_client(self) -> httpx.AsyncClient:
        if self._external_client is not None:
            return self._external_client
        return httpx.AsyncClient(timeout=self.timeout)

    async def query_range(
        self,
        query: str,
        start: datetime,
        end: datetime,
        limit: int = 100,
    ) -> LogQueryResult:
        """Queries /loki/api/v1/query_range with resource validation, line limits, and byte caps."""
        validate_resource_scoped_logql(query)

        # Enforce maximum window of 5 minutes
        duration = (end - start).total_seconds()
        if duration > MAX_LOKI_WINDOW_SECONDS:
            logger.warning(
                f"Requested log duration {duration}s exceeds {MAX_LOKI_WINDOW_SECONDS}s cap. Clamping start."
            )
            start = datetime.fromtimestamp(end.timestamp() - MAX_LOKI_WINDOW_SECONDS, tz=UTC)

        # Enforce max 500 lines
        clamped_limit = min(limit, MAX_LOKI_LINES)

        # Convert datetimes to nanoseconds for Loki API
        start_ns = int(start.timestamp() * 1_000_000_000)
        end_ns = int(end.timestamp() * 1_000_000_000)

        params: dict[str, Any] = {
            "query": query,
            "start": str(start_ns),
            "end": str(end_ns),
            "limit": clamped_limit,
            "direction": "BACKWARD",
        }

        url = f"{self.base_url}/loki/api/v1/query_range"
        client = self._get_client()
        should_close = self._external_client is None

        try:
            resp = await client.get(url, params=params)
            content_bytes = resp.content
            byte_size = len(content_bytes)

            is_truncated = False
            if byte_size > self.max_bytes:
                logger.warning(
                    f"Loki response {byte_size} bytes exceeded cap {self.max_bytes}. Truncating payload."
                )
                return LogQueryResult(
                    query=query,
                    status="error",
                    is_truncated=True,
                    byte_size=byte_size,
                    error_message=f"Response exceeded {self.max_bytes} byte limit",
                )

            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "success":
                return LogQueryResult(
                    query=query,
                    status="error",
                    byte_size=byte_size,
                    error_message=data.get("error", "Unknown Loki error"),
                )

            streams = data.get("data", {}).get("result", [])
            if not streams:
                return LogQueryResult(
                    query=query,
                    status="empty",
                    byte_size=byte_size,
                    entries=[],
                    total_lines=0,
                )

            entries: list[LogEntry] = []
            now = datetime.now(UTC)
            min_age_seconds: float | None = None

            for stream in streams:
                labels = stream.get("stream", {})
                values = stream.get("values", [])
                for val_pair in values:
                    ts_ns_str, line = val_pair
                    try:
                        ts_sec = float(ts_ns_str) / 1_000_000_000.0
                        dt = datetime.fromtimestamp(ts_sec, tz=UTC)
                    except Exception:
                        dt = now

                    age = (now - dt).total_seconds()
                    if min_age_seconds is None or age < min_age_seconds:
                        min_age_seconds = age

                    entries.append(LogEntry(timestamp=dt, line=line, labels=labels))

            total_lines = len(entries)
            if total_lines >= clamped_limit:
                is_truncated = True

            return LogQueryResult(
                query=query,
                status="success",
                entries=entries,
                total_lines=total_lines,
                is_truncated=is_truncated,
                byte_size=byte_size,
                freshness_seconds=min_age_seconds,
            )

        except httpx.TimeoutException as exc:
            logger.warning(f"Loki query timed out after {self.timeout}s: {exc}")
            return LogQueryResult(
                query=query,
                status="unavailable",
                error_message=f"Loki query timed out after {self.timeout}s",
            )
        except httpx.ConnectError as exc:
            logger.warning(f"Loki connection failed: {exc}")
            return LogQueryResult(
                query=query,
                status="unavailable",
                error_message=f"Loki service unavailable: {exc}",
            )
        except Exception as exc:
            logger.warning(f"Loki query error: {exc}")
            return LogQueryResult(
                query=query,
                status="error",
                error_message=str(exc),
            )
        finally:
            if should_close:
                await client.aclose()
