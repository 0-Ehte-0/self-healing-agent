import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
import sqlalchemy as sa
from app.db.seed.__main__ import seed
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def workflow_db_url():
    raw = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/control_plane",
    )
    base = sa.make_url(raw)
    db_name = "m1b_test_" + uuid4().hex
    url = base.set(database=db_name)

    async def manage(create: bool):
        conn = await asyncpg.connect(
            base.set(drivername="postgresql").render_as_string(hide_password=False)
        )
        try:
            if create:
                await conn.execute(f'CREATE DATABASE "{db_name}"')
            else:
                await conn.execute(f'DROP DATABASE "{db_name}" WITH (FORCE)')
        finally:
            await conn.close()

    try:
        asyncio.run(manage(True))
    except Exception as exc:
        pytest.skip(f"PostgreSQL connection failed: {exc}")

    env = {**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False)}

    def migrate(revision: str):
        subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "upgrade" if revision == "head" else "downgrade",
                revision,
            ],
            cwd=ROOT / "apps/control-api",
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

    try:
        migrate("head")
        yield url.render_as_string(hide_password=False)
    finally:
        asyncio.run(manage(False))


@pytest_asyncio.fixture(scope="module")
async def session_factory(workflow_db_url):
    import app.api.system as system_api
    import app.db.session as db_session

    engine = create_async_engine(workflow_db_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await seed(factory)

    orig_db = db_session.AsyncSessionLocal
    orig_sys = getattr(system_api, "AsyncSessionLocal", None)

    db_session.AsyncSessionLocal = factory
    system_api.AsyncSessionLocal = factory

    try:
        yield factory
    finally:
        db_session.AsyncSessionLocal = orig_db
        if orig_sys is not None:
            system_api.AsyncSessionLocal = orig_sys
        await engine.dispose()
