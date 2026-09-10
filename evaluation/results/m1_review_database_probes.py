"""Exercise adapter recovery only against the review DB and an in-memory Docker fake."""

import asyncio
import json
import os
import runpy
from pathlib import Path

from app.db.repositories.control_plane import unit_of_work
from provideradapters.docker.adapter import DockerExecutionAdapter
from sharedmodels.enums import ExecutionStatus
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[2]


async def main():
    url = os.environ["DATABASE_URL"]
    assert url.rsplit("/", 1)[1].startswith("m1_review_")
    engine = create_async_engine(url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    namespace = runpy.run_path(
        str(ROOT / "tests/integration/adapters/docker/test_docker_adapter.py")
    )
    setup = namespace["setup_incident_context"].__wrapped__
    context = await setup(factory)
    intent = context["intent"]
    container = namespace["FakeDockerContainer"](
        context["container_id"],
        "demo-api",
        {
            "self-healing.managed": "true",
            "com.docker.compose.service": "demo-api",
            "com.docker.compose.project": "self-healing-agent",
        },
    )
    client = namespace["FakeDockerClient"]({container.id: container})
    adapter = DockerExecutionAdapter(factory, client)
    async with unit_of_work(factory, actor="review:probe") as repo:
        decision = await repo.get_latest_policy_decision(intent.incident_id, intent.plan_id)
        decision.decision = "DEFER"
        decision.reason_codes = ["COOLDOWN_ACTIVE"]
    try:
        accepted = await adapter.precheck(intent)
        precheck_passed = accepted.get("precheck_passed", False)
    except Exception:
        precheck_passed = False
    results = {"deferred_policy_precheck_passed": precheck_passed}

    async with unit_of_work(factory, actor="review:probe") as repo:
        decision = await repo.get_latest_policy_decision(intent.incident_id, intent.plan_id)
        decision.decision = "ALLOW"
        record = await repo.record_execution(
            incident_id=intent.incident_id,
            plan_id=intent.plan_id,
            step_id=intent.step_id,
            resource_id=intent.resource_id,
            attempt_number=intent.attempt_number,
            container_id=intent.container_id,
            binding_generation=intent.binding_generation,
            idempotency_key=intent.idempotency_key,
            pre_state={"status": "exited"},
        )
        await repo.finish_execution(record.id, record.version, ExecutionStatus.RUNNING, result={})
    # Model the crash window: daemon restarted, outcome was not persisted, prior lease expired.
    container.restart()
    before = container.restart_call_count
    resume_error = None
    try:
        await adapter.execute(intent)
    except Exception as exc:
        resume_error = f"{type(exc).__name__}: {exc}"
    results["replay_of_running_execution"] = {
        "same_idempotency_key": intent.idempotency_key,
        "restart_calls_before_resume": before,
        "restart_calls_after_resume": container.restart_call_count,
        "resume_error": resume_error,
    }
    (ROOT / "evaluation/results/m1-review-database-probes.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    print(json.dumps(results, indent=2))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
