"""Local startup: migrate/seed as owner, then serve using the restricted runtime role."""

import asyncio
import os

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine


async def configure_role(url: str):
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            password = os.environ["CONTROL_APP_PASSWORD"]
            await conn.execute(
                sa.text("SELECT set_config('app.bootstrap_password', :value, true)"),
                {"value": password},
            )
            await conn.execute(
                sa.text("""DO $$ BEGIN EXECUTE format(
                'ALTER ROLE control_app LOGIN PASSWORD %L', current_setting('app.bootstrap_password'));
                END $$""")
            )
    finally:
        await engine.dispose()


def main():
    runtime = os.environ["DATABASE_URL"]
    owner = os.environ["MIGRATION_DATABASE_URL"]
    os.environ["DATABASE_URL"] = owner
    from app.core.config import get_settings

    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")
    from app.db.seed.__main__ import main as seed_main

    asyncio.run(seed_main())
    asyncio.run(configure_role(owner))
    os.environ["DATABASE_URL"] = runtime
    # uvicorn is a fresh process: the privileged connection configuration cannot leak into its settings cache.
    os.environ.pop("MIGRATION_DATABASE_URL", None)
    os.environ.pop("CONTROL_APP_PASSWORD", None)
    os.execvp("uvicorn", ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"])


if __name__ == "__main__":
    main()
