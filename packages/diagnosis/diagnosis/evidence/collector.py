import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sharedmodels.enums import EvidenceKind
from sharedmodels.evidence import EvidenceBundle, EvidenceItemSchema
from telemetryclient.loki import LokiClient
from telemetryclient.prometheus import PrometheusClient
from telemetryclient.schemas import ContainerInspectionResult, LogQueryResult, MetricQueryResult

from diagnosis.evidence.container_reader import ContainerReaderProtocol, DockerContainerReader
from diagnosis.evidence.redactor import redact_payload
from diagnosis.schemas import ObservationBundle

logger = logging.getLogger(__name__)

MAX_METRIC_WINDOW_SECONDS = 900  # 15 minutes
MAX_LOG_WINDOW_SECONDS = 300  # 5 minutes
MAX_DEPLOYMENT_WINDOW_SECONDS = 3600  # 60 minutes
MAX_COLLECTION_RETRIES = 2
RETRY_BACKOFF_SECONDS = [0.5, 1.0]


class EvidenceCollector:
    """Bounded, redacted telemetry evidence collector with bounded retries and PostgreSQL persistence."""

    def __init__(
        self,
        prometheus_client: PrometheusClient | None = None,
        loki_client: LokiClient | None = None,
        container_reader: ContainerReaderProtocol | None = None,
        max_retries: int = MAX_COLLECTION_RETRIES,
    ):
        self.prom = prometheus_client or PrometheusClient()
        self.loki = loki_client or LokiClient()
        self.reader = container_reader or DockerContainerReader()
        self.max_retries = max_retries

    async def collect(
        self,
        incident_id: UUID,
        resource_id: UUID,
        target_service: str = "demo-api",
        target_container_id: str | None = None,
        binding_generation: int | None = None,
        session: Any = None,
        actor: str = "agent:evidence_collector",
    ) -> EvidenceBundle:
        """Collects bounded, redacted evidence from Prometheus, Loki, container inspection, and PostgreSQL."""
        now = datetime.now(UTC)
        items: list[EvidenceItemSchema] = []
        completeness: dict[str, str] = {}
        is_truncated = False
        min_freshness: float | None = None

        # 1. Container Status Inspection
        container_res = await self._collect_container_status(
            target_container_id, binding_generation
        )
        c_item = self._create_evidence_item(
            incident_id=incident_id,
            kind=EvidenceKind.CONTAINER_STATE,
            source=f"docker:inspect:{target_container_id or 'unknown'}",
            observed_at=container_res.observed_at,
            content={
                "state": container_res.state,
                "name": container_res.name,
                "exit_code": container_res.exit_code,
                "health_status": container_res.health_status,
                "binding_generation": container_res.binding_generation,
                "status": container_res.status,
                "error": container_res.error_message,
            },
            unit=None,
            binding_generation=binding_generation,
            actor=actor,
        )
        items.append(c_item)
        completeness["docker"] = "COMPLETE" if container_res.status == "success" else "ERROR"

        # 2. Prometheus Metrics Collection with Bounded Retries (Step 10)
        prom_data, prom_comp, prom_freshness, prom_trunc = await self._collect_metrics_with_retry(
            target_service, now
        )
        completeness["prometheus"] = prom_comp
        if prom_trunc:
            is_truncated = True
        if prom_freshness is not None:
            min_freshness = (
                prom_freshness if min_freshness is None else min(min_freshness, prom_freshness)
            )

        for p_key, p_val in prom_data.items():
            item = self._create_evidence_item(
                incident_id=incident_id,
                kind=EvidenceKind.METRICS,
                source=f"prometheus:{p_key}",
                observed_at=now,
                content=p_val["content"],
                unit=p_val.get("unit"),
                binding_generation=binding_generation,
                actor=actor,
            )
            items.append(item)

        # 3. Loki Logs Collection with Bounded Retries (Step 10)
        loki_res, loki_comp, loki_trunc = await self._collect_logs_with_retry(target_service, now)
        completeness["loki"] = loki_comp
        if loki_trunc:
            is_truncated = True
        if loki_res.freshness_seconds is not None:
            min_freshness = (
                loki_res.freshness_seconds
                if min_freshness is None
                else min(min_freshness, loki_res.freshness_seconds)
            )

        log_lines = [e.line for e in loki_res.entries]
        log_item = self._create_evidence_item(
            incident_id=incident_id,
            kind=EvidenceKind.LOGS,
            source=f"loki:query:{target_service}",
            observed_at=now,
            content={
                "total_lines": loki_res.total_lines,
                "is_truncated": loki_res.is_truncated,
                "lines": log_lines,
                "status": loki_res.status,
                "error": loki_res.error_message,
            },
            unit="lines",
            binding_generation=binding_generation,
            actor=actor,
        )
        items.append(log_item)

        # 4. Resource / Deployment History Window (60 minutes) & Similar Incidents (Step 3)
        deploy_status, deploy_items = await self._collect_deployment_and_history(
            resource_id=resource_id,
            incident_id=incident_id,
            now=now,
            session=session,
            actor=actor,
            binding_generation=binding_generation,
        )
        items.extend(deploy_items)
        completeness["deployment_history"] = deploy_status

        bundle = EvidenceBundle(
            incident_id=incident_id,
            resource_id=resource_id,
            collected_at=now,
            collection_window_seconds=MAX_METRIC_WINDOW_SECONDS,
            items=items,
            freshness_seconds=min_freshness,
            completeness=completeness,
            deployment_history_status=deploy_status,
            is_truncated=is_truncated,
            redaction_applied=True,
        )
        return bundle

    async def _collect_container_status(
        self, container_id: str | None, binding_generation: int | None
    ) -> ContainerInspectionResult:
        if not container_id:
            return ContainerInspectionResult(
                container_id="none",
                state="not_found",
                binding_generation=binding_generation,
                status="not_found",
                error_message="No target container ID provided",
            )
        return await self.reader.inspect(container_id, binding_generation)

    async def _collect_metrics_with_retry(
        self, target_service: str, now: datetime
    ) -> tuple[dict[str, Any], str, float | None, bool]:
        """Collects Prometheus metrics with up to MAX_COLLECTION_RETRIES for transient errors."""
        start_15m = datetime.fromtimestamp(now.timestamp() - MAX_METRIC_WINDOW_SECONDS, tz=UTC)

        queries = {
            "up": (f'up{{job="{target_service}"}}', "ratio"),
            "cpu_rate": (
                f'rate(demo_api_cpu_seconds_total{{job="{target_service}"}}[1m])',
                "cores",
            ),
            "process_cpu": (
                f'rate(process_cpu_seconds_total{{job="{target_service}"}}[1m])',
                "cores",
            ),
            "probe_success": (
                f'probe_success{{instance=~".*/health/ready",job=~".*{target_service}.*"}}',
                "ratio",
            ),
            "http_requests_total": (
                f'demo_api_http_requests_total{{job="{target_service}"}}',
                "count",
            ),
            "http_errors": (
                f'sum(rate(demo_api_http_requests_total{{job="{target_service}",status=~"5.."}}[1m]))',
                "count/sec",
            ),
            "redis_connected": (f'demo_api_redis_connected{{job="{target_service}"}}', "ratio"),
            "db_connections": ("fault_injector_db_connections_active", "count"),
        }

        data: dict[str, Any] = {}
        completeness = "COMPLETE"
        min_freshness: float | None = None
        is_truncated = False

        # Query Prometheus alerts
        active_alerts = await self.prom.get_active_alerts()
        alert_names = [a.name for a in active_alerts if a.state == "firing"]
        data["active_alerts"] = {
            "content": {"firing_alerts": alert_names, "count": len(alert_names)},
            "unit": "count",
        }

        # Query metrics with retry
        for name, (query_str, unit) in queries.items():
            result: MetricQueryResult | None = None
            for attempt in range(self.max_retries + 1):
                try:
                    result = await self.prom.query_instant(query_str, time=now)
                    if result.status == "success" or result.status == "empty":
                        break
                    if attempt < self.max_retries:
                        await asyncio.sleep(RETRY_BACKOFF_SECONDS[attempt])
                except Exception:
                    if attempt < self.max_retries:
                        await asyncio.sleep(RETRY_BACKOFF_SECONDS[attempt])

            if result is None or result.status == "unavailable":
                completeness = "UNAVAILABLE"
                data[name] = {
                    "content": {"query": query_str, "status": "unavailable", "samples": []},
                    "unit": unit,
                }
            elif result.status == "error":
                completeness = "ERROR" if completeness != "UNAVAILABLE" else "UNAVAILABLE"
                data[name] = {
                    "content": {
                        "query": query_str,
                        "status": "error",
                        "error": result.error_message,
                    },
                    "unit": unit,
                }
            else:
                if result.is_truncated:
                    is_truncated = True
                if result.freshness_seconds is not None:
                    min_freshness = (
                        result.freshness_seconds
                        if min_freshness is None
                        else min(min_freshness, result.freshness_seconds)
                    )

                latest_val = result.samples[0].latest_value if result.samples else None
                data[name] = {
                    "content": {
                        "query": query_str,
                        "status": result.status,
                        "latest_value": latest_val,
                        "samples": [s.model_dump() for s in result.samples],
                    },
                    "unit": unit,
                }

        return data, completeness, min_freshness, is_truncated

    async def _collect_logs_with_retry(
        self, target_service: str, now: datetime
    ) -> tuple[LogQueryResult, str, bool]:
        """Collects Loki logs with bounded retry."""
        start_5m = datetime.fromtimestamp(now.timestamp() - MAX_LOG_WINDOW_SECONDS, tz=UTC)
        logql = f'{{job="{target_service}"}}'

        res: LogQueryResult | None = None
        for attempt in range(self.max_retries + 1):
            try:
                res = await self.loki.query_range(logql, start=start_5m, end=now, limit=100)
                if res.status in ("success", "empty"):
                    break
                if attempt < self.max_retries:
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS[attempt])
            except Exception:
                if attempt < self.max_retries:
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS[attempt])

        if res is None or res.status == "unavailable":
            return (
                LogQueryResult(
                    query=logql,
                    status="unavailable",
                    error_message="Loki unavailable after retries",
                ),
                "UNAVAILABLE",
                False,
            )
        if res.status == "empty":
            return res, "EMPTY", False
        if res.status == "error":
            return res, "ERROR", res.is_truncated

        return res, "COMPLETE", res.is_truncated

    async def _collect_deployment_and_history(
        self,
        resource_id: UUID,
        incident_id: UUID,
        now: datetime,
        session: Any,
        actor: str,
        binding_generation: int | None,
    ) -> tuple[str, list[EvidenceItemSchema]]:
        """Queries up to 60m of deployment/resource changes and past 5 similar incidents."""
        items: list[EvidenceItemSchema] = []
        if session is None:
            # Unavailable when running without DB session
            unavailable_item = self._create_evidence_item(
                incident_id=incident_id,
                kind=EvidenceKind.RESOURCE_CHANGES,
                source="postgres:deployment_history",
                observed_at=now,
                content={
                    "status": "UNAVAILABLE",
                    "reason": "No database session provided for deployment history retrieval",
                    "window_seconds": MAX_DEPLOYMENT_WINDOW_SECONDS,
                },
                unit=None,
                binding_generation=binding_generation,
                actor=actor,
            )
            return "UNAVAILABLE", [unavailable_item]

        try:
            from app.db.models import AuditEntry, Incident  # type: ignore[import-not-found]

            start_60m = datetime.fromtimestamp(
                now.timestamp() - MAX_DEPLOYMENT_WINDOW_SECONDS, tz=UTC
            )

            # 1. Audit entries of resource mutations in last 60m
            audit_records = list(
                (
                    await session.scalars(
                        sa.select(AuditEntry)
                        .where(
                            AuditEntry.entity_id == resource_id,
                            AuditEntry.created_at >= start_60m,
                        )
                        .order_by(AuditEntry.created_at.desc())
                        .limit(20)
                    )
                ).all()
            )

            # 2. Similar recent incidents for the same resource (capped at 5)
            past_incidents = list(
                (
                    await session.scalars(
                        sa.select(Incident)
                        .where(
                            Incident.resource_id == resource_id,
                            Incident.id != incident_id,
                            Incident.created_at >= start_60m,
                        )
                        .order_by(Incident.created_at.desc())
                        .limit(5)
                    )
                ).all()
            )

            status = "COMPLETE" if (audit_records or past_incidents) else "EMPTY"
            item = self._create_evidence_item(
                incident_id=incident_id,
                kind=EvidenceKind.RESOURCE_CHANGES,
                source="postgres:audit_and_incidents",
                observed_at=now,
                content={
                    "status": status,
                    "window_seconds": MAX_DEPLOYMENT_WINDOW_SECONDS,
                    "audit_count": len(audit_records),
                    "audit_changes": [
                        {
                            "sequence": a.sequence,
                            "operation": a.operation,
                            "actor": a.actor,
                            "created_at": a.created_at.isoformat(),
                        }
                        for a in audit_records
                    ],
                    "similar_incidents": [
                        {
                            "id": str(inc.id),
                            "state": inc.state.value
                            if hasattr(inc.state, "value")
                            else str(inc.state),
                            "severity": inc.severity.value
                            if hasattr(inc.severity, "value")
                            else str(inc.severity),
                            "correlation_key": inc.correlation_key,
                            "created_at": inc.created_at.isoformat(),
                        }
                        for inc in past_incidents
                    ],
                },
                unit="records",
                binding_generation=binding_generation,
                actor=actor,
            )
            return status, [item]

        except Exception as exc:
            logger.warning(f"Failed to query deployment history: {exc}")
            item = self._create_evidence_item(
                incident_id=incident_id,
                kind=EvidenceKind.RESOURCE_CHANGES,
                source="postgres:deployment_history",
                observed_at=now,
                content={
                    "status": "UNAVAILABLE",
                    "reason": f"Deployment history query failed: {exc}",
                    "window_seconds": MAX_DEPLOYMENT_WINDOW_SECONDS,
                },
                unit=None,
                binding_generation=binding_generation,
                actor=actor,
            )
            return "UNAVAILABLE", [item]

    def _create_evidence_item(
        self,
        incident_id: UUID,
        kind: EvidenceKind | str,
        source: str,
        observed_at: datetime,
        content: dict[str, Any],
        unit: str | None,
        binding_generation: int | None,
        actor: str,
    ) -> EvidenceItemSchema:
        """Sanitizes content, computes SHA-256 digest, and creates typed EvidenceItemSchema."""
        redacted = redact_payload(content)
        content_json = json.dumps(redacted, sort_keys=True, default=str)
        sha256 = hashlib.sha256(content_json.encode("utf-8")).hexdigest()
        clean_content = json.loads(content_json)

        return EvidenceItemSchema(
            id=uuid4(),
            incident_id=incident_id,
            kind=kind,
            source=source,
            observed_at=observed_at,
            unit=unit,
            binding_generation=binding_generation,
            content=clean_content,
            sha256=sha256,
            actor=actor,
        )

    def to_observation_bundle(
        self,
        bundle: EvidenceBundle,
        service_name: str = "demo-api",
        container_id: str | None = None,
        binding_generation: int | None = None,
    ) -> ObservationBundle:
        """Constructs an ObservationBundle from an EvidenceBundle for rule evaluation."""
        obs = ObservationBundle(
            resource_id=bundle.resource_id,
            service_name=service_name,
            container_id=container_id,
            binding_generation=binding_generation,
            completeness=bundle.completeness,
            freshness_seconds=bundle.freshness_seconds,
            observed_at=bundle.collected_at,
        )

        for item in bundle.items:
            obs.evidence_id_map[item.source] = str(item.id)

            if item.kind == EvidenceKind.CONTAINER_STATE:
                obs.container_status = item.content.get("state", "unknown")
                obs.exit_code = item.content.get("exit_code")
                obs.health_status = item.content.get("health_status")

            elif item.kind == EvidenceKind.METRICS:
                if "prometheus:up" in item.source:
                    obs.up_metric = item.content.get("latest_value")
                elif (
                    "prometheus:cpu_rate" in item.source or "prometheus:process_cpu" in item.source
                ):
                    val = item.content.get("latest_value")
                    if val is not None:
                        obs.raw_cpu_seconds_rate = val
                        # Normalized CPU = rate / cpu_budget (1.0 core)
                        obs.normalized_cpu = val / obs.cpu_budget_cores
                elif "prometheus:probe_success" in item.source:
                    obs.readiness_success_ratio = item.content.get("latest_value")
                    if obs.readiness_success_ratio is not None:
                        obs.latest_readiness_code = (
                            200 if obs.readiness_success_ratio >= 1.0 else 503
                        )
                elif "prometheus:redis_connected" in item.source:
                    val = item.content.get("latest_value")
                    if val is not None:
                        obs.redis_connected = bool(val >= 1.0)
                elif "prometheus:db_connections" in item.source:
                    val = item.content.get("latest_value")
                    if val is not None:
                        obs.db_connections_active = int(val)
                elif "prometheus:active_alerts" in item.source:
                    obs.firing_alerts = item.content.get("firing_alerts", [])

            elif item.kind == EvidenceKind.LOGS:
                lines = item.content.get("lines", [])
                err_lines = [
                    line for line in lines if "error" in line.lower() or "exception" in line.lower()
                ]
                obs.recent_log_errors = err_lines

        if bundle.completeness.get("prometheus") == "UNAVAILABLE":
            obs.is_telemetry_unavailable = True

        return obs
