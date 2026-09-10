import asyncio
import hashlib
import os
import secrets

import sqlalchemy as sa
from app.db.models import Policy, Resource, User
from app.db.repositories.control_plane import unit_of_work
from app.db.session import AsyncSessionLocal, engine
from sharedmodels.enums import RiskLevel, UserRole


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"scrypt$16384$8$1${salt}${digest}"


async def seed(factory=AsyncSessionLocal):
    async with unit_of_work(factory, "system:seed") as repo:
        for name in ("demo-api", "demo-worker"):
            exists = await repo.session.scalar(
                sa.select(Resource).where(
                    Resource.provider == "compose", Resource.external_id == f"self-healing:{name}"
                )
            )
            if not exists:
                # Logical discovery entries, deliberately ineligible for execution until the Docker adapter resolves an immutable container ID.
                await repo.add(
                    Resource(
                        provider="compose",
                        external_id=f"self-healing:{name}",
                        name=name,
                        environment="local",
                        labels={"compose_service": name},
                        managed=False,
                    )
                )
        for username, role in [
            ("admin", UserRole.ADMIN),
            ("approver", UserRole.APPROVER),
            ("operator", UserRole.OPERATOR),
            ("viewer", UserRole.VIEWER),
        ]:
            if not await repo.session.scalar(sa.select(User).where(User.username == username)):
                password = os.getenv(
                    f"SEED_{username.upper()}_PASSWORD", f"local-{username}-change-me"
                )
                await repo.add(
                    User(
                        username=username,
                        role=role,
                        password_hash=hash_password(password),
                        enabled=True,
                    )
                )
        for action in (
            "restart_container",
            "inspect_container",
            "wait_for_stabilization",
            "notify_operator",
            "open_incident_ticket",
        ):
            name = f"local:{action}"
            if not await repo.session.scalar(
                sa.select(Policy).where(Policy.name == name, Policy.version == 1)
            ):
                await repo.add(
                    Policy(
                        name=name,
                        version=1,
                        environment="local",
                        action=action,
                        risk=RiskLevel.LOW,
                        rules={
                            "approval_required": False,
                            "retry_limit": 2,
                            "cooldown_seconds": 600,
                            "confidence_threshold": 0.8,
                            "allowed_environment": "local",
                            "managed_only": True,
                        },
                        enabled=True,
                    )
                )
            # M1-E Documented Policy Profile: Version 2
            if not await repo.session.scalar(
                sa.select(Policy).where(Policy.name == name, Policy.version == 2)
            ):
                await repo.add(
                    Policy(
                        name=name,
                        version=2,
                        environment="local",
                        action=action,
                        risk=RiskLevel.LOW,
                        rules={
                            "approval_required": True,
                            "auto_approval_confidence_threshold": 0.85,
                            "min_approval_confidence_threshold": 0.60,
                            "retry_limit": 2,
                            "cooldown_seconds": 600,
                            "allowed_environments": ["local"],
                            "allowed_targets": ["demo-api"],
                            "require_low_risk_for_approval": True,
                            "evidence_freshness_limit_seconds": 300,
                        },
                        enabled=True,
                    )
                )

        # M1-E Initial Automation Control
        await repo.get_automation_controls()


async def main():
    await seed()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
