"""Read-only behavior probes for M1 integration gaps, with no real Docker mutations."""

# ruff: noqa: E402 -- Source-tree bootstrap for a standalone review script.

import asyncio
import importlib
import json
import sys
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
for directory in [
    "apps/control-api",
    "packages/agent-core",
    "packages/shared-models",
    "packages/diagnosis",
    "packages/telemetry-client",
    "packages/action-catalog",
    "packages/policy-engine",
    "packages/provider-adapters",
]:
    sys.path.insert(0, str(ROOT / directory))

importlib.import_module("agentcore.runtime.runner")  # Match production import order.

from agentcore.graph.builder import build_incident_workflow
from agentcore.nodes.diagnosis import diagnose_node
from agentcore.nodes.policy import evaluate_policy_node
from agentcore.verification.evaluator import TelemetryEvaluator
from agentcore.verification.profiles import load_verification_profile
from policyengine import PolicyRuleSet
from telemetryclient.schemas import MetricQueryResult, MetricSample


class PartialTelemetry:
    async def query_instant(self, query):
        if 'status=~"5.."' in query:
            return MetricQueryResult(query=query, status="unavailable")
        if "histogram_quantile" in query:
            return MetricQueryResult(query=query, status="unavailable")
        return MetricQueryResult(
            query=query,
            status="success",
            freshness_seconds=0,
            samples=[MetricSample(latest_value=10.0)],
        )


async def main():
    now = datetime.now(UTC)
    evaluator = TelemetryEvaluator(prometheus_client=PartialTelemetry())
    profile = load_verification_profile("SCN-003")
    results = {
        "no_live_container_observation": evaluator._evaluate_target_state(
            "a" * 64, 1, None, now
        ).model_dump(mode="json"),
        "unavailable_required_latency": (
            await evaluator._evaluate_latency(profile, True, "", now)
        ).model_dump(mode="json"),
        "unavailable_error_numerator": (
            await evaluator._evaluate_traffic_error_rate(profile, True, "", now)
        ).model_dump(mode="json"),
    }
    state = {
        "incident_id": str(uuid4()),
        "resource_id": str(uuid4()),
        "current_plan_id": str(uuid4()),
        "evidence_ids": [str(uuid4())],
    }
    results["diagnosis_with_actual_workflow_input"] = await diagnose_node(state)

    called = []

    async def evidence(s):
        return {"status": "EVIDENCE_COLLECTED"}

    async def diagnosis(s):
        return {"status": "DIAGNOSED", "is_actionable": True}

    async def plan(s):
        return {"current_plan_id": state["current_plan_id"], "status": "PLANNED"}

    async def execute(s):
        called.append({"node": "execute", "incoming_status": s.get("status")})
        return {"status": "EXECUTED", "execution_id": str(uuid4())}

    async def verify(s):
        called.append({"node": "verify", "execution_id": s.get("execution_id")})
        return {"verification_passed": False, "retry_eligible": False}

    async def escalate(s):
        return {"status": "ESCALATED"}

    workflow = build_incident_workflow(
        node_overrides={
            "collect_evidence": evidence,
            "diagnose": diagnosis,
            "plan": plan,
            "evaluate_policy": partial(
                evaluate_policy_node, rules=PolicyRuleSet(allowed_targets=[])
            ),
            "execute": execute,
            "verify": verify,
            "escalate": escalate,
        }
    )
    await workflow.ainvoke(state)
    results["denied_policy_graph_path"] = called
    results["root_cause_used_as_profile"] = {
        "input": "API_UNRESPONSIVE",
        "loaded": load_verification_profile("API_UNRESPONSIVE").profile_id,
        "expected": profile.profile_id,
    }
    path = ROOT / "evaluation/results/m1-review-probes.json"
    path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
