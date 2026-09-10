"""Run the existing M1 checks in a disposable database; never use application data."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "evaluation" / "results"
DB_NAME = "m1_review_" + uuid4().hex


async def main():
    connection = await asyncpg.connect(
        host="127.0.0.1", port=5432, user="postgres", password="postgres", database="postgres"
    )
    await connection.execute(f'CREATE DATABASE "{DB_NAME}"')
    env = dict(os.environ)
    env["DATABASE_URL"] = f"postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/{DB_NAME}"
    env["TEST_DATABASE_URL"] = env["DATABASE_URL"]
    env["OTEL_SDK_DISABLED"] = "true"
    env["REDIS_URL"] = "redis://127.0.0.1:6379/15"
    env["PYTHONPATH"] = os.pathsep.join(
        str(ROOT / part)
        for part in [
            "apps/control-api",
            "apps/agent-worker",
            "packages/shared-models",
            "packages/agent-core",
            "packages/diagnosis",
            "packages/telemetry-client",
            "packages/action-catalog",
            "packages/policy-engine",
            "packages/provider-adapters",
        ]
    )
    try:
        with (OUTPUT / "m1-review-migration.txt").open("w", encoding="utf-8") as log:
            migration = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=ROOT / "apps/control-api",
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        print(f"Fresh database migration exit code: {migration.returncode}", flush=True)
        if migration.returncode:
            return
        if "--database-probes" in sys.argv:
            result = subprocess.run(
                [sys.executable, str(OUTPUT / "m1_review_database_probes.py")],
                cwd=ROOT,
                env=env,
            )
            print(f"Database behavior probes exit code: {result.returncode}", flush=True)
            return
        targets = ["tests/integration/m1", "tests/integration/adapters/docker", "tests/security/m1"]
        with (OUTPUT / "m1-review-integration.txt").open("w", encoding="utf-8") as log:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    *targets,
                    "-q",
                    "--tb=short",
                    "--junitxml=evaluation/results/m1-review-integration.xml",
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        print(f"Existing M1 integration/security suite exit code: {result.returncode}", flush=True)
    finally:
        # DB_NAME is generated above with a fixed task prefix and UUID; it is never user input.
        await connection.execute(f'DROP DATABASE "{DB_NAME}" WITH (FORCE)')
        await connection.close()
        print("Disposable review database removed.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
