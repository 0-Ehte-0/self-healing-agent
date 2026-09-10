import pytest
import sqlalchemy as sa
from app.db.models import Incident, Resource
from app.db.repositories.control_plane import unit_of_work
from sharedmodels.enums import IncidentState as S
from sharedmodels.enums import Severity


@pytest.mark.asyncio
async def test_safe_escalation_from_planned(session_factory):
    """Verifies ADR-0003: PLANNED -> ESCALATED succeeds regardless of approval_required."""
    async with unit_of_work(session_factory, actor="test:actor") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        assert res is not None

        # Case 1: approval_required = True, policy denies -> PLANNED -> ESCALATED
        inc1 = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-planned-esc-1-{res.id}",
            severity=Severity.HIGH,
            approval_required=True,
        )
        inc1 = await repo.transition(inc1.id, 1, S.TRIAGED)
        inc1 = await repo.transition(inc1.id, 2, S.DIAGNOSED)
        inc1 = await repo.transition(inc1.id, 3, S.PLANNED)
        assert inc1.state == S.PLANNED
        assert inc1.approval_required is True

        # Escalate directly from PLANNED without approval mismatch error
        inc1_esc = await repo.transition(inc1.id, 4, S.ESCALATED)
        assert inc1_esc.state == S.ESCALATED
        assert inc1_esc.version == 5

        # Case 2: approval_required = False, target ineligible -> PLANNED -> ESCALATED
        inc2 = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-planned-esc-2-{res.id}",
            severity=Severity.MEDIUM,
            approval_required=False,
        )
        inc2 = await repo.transition(inc2.id, 1, S.TRIAGED)
        inc2 = await repo.transition(inc2.id, 2, S.DIAGNOSED)
        inc2 = await repo.transition(inc2.id, 3, S.PLANNED)
        assert inc2.state == S.PLANNED
        assert inc2.approval_required is False

        inc2_esc = await repo.transition(inc2.id, 4, S.ESCALATED)
        assert inc2_esc.state == S.ESCALATED
        assert inc2_esc.version == 5


@pytest.mark.asyncio
async def test_safe_escalation_from_approved(session_factory):
    """Verifies ADR-0003: APPROVED -> ESCALATED succeeds (approval timeout, kill switch)."""
    async with unit_of_work(session_factory, actor="test:actor") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        assert res is not None

        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-approved-esc-{res.id}",
            severity=Severity.HIGH,
            approval_required=True,
        )
        inc = await repo.transition(inc.id, 1, S.TRIAGED)
        inc = await repo.transition(inc.id, 2, S.DIAGNOSED)
        inc = await repo.transition(inc.id, 3, S.PLANNED)
        inc = await repo.transition(inc.id, 4, S.PENDING_APPROVAL)
        inc = await repo.transition(inc.id, 5, S.APPROVED)
        assert inc.state == S.APPROVED

        # Now transition APPROVED -> ESCALATED
        inc_esc = await repo.transition(inc.id, 6, S.ESCALATED)
        assert inc_esc.state == S.ESCALATED
        assert inc_esc.version == 7


@pytest.mark.asyncio
async def test_illegal_transitions_rejected(session_factory):
    """Verifies illegal edges fail across repository and database guard layers."""
    async with unit_of_work(session_factory, actor="test:actor") as repo:
        res = await repo.session.scalar(sa.select(Resource).limit(1))
        assert res is not None

        inc = await repo.create_incident(
            resource_id=res.id,
            correlation_key=f"test-illegal-edges-{res.id}",
            severity=Severity.LOW,
        )

        # DETECTED -> PLANNED is illegal
        with pytest.raises(ValueError, match="Illegal incident transition"):
            await repo.transition(inc.id, 1, S.PLANNED)

        # DETECTED -> RESOLVED is illegal
        with pytest.raises(ValueError, match="Illegal incident transition"):
            await repo.transition(inc.id, 1, S.RESOLVED)

        # Legitimate progress to PLANNED
        inc = await repo.transition(inc.id, 1, S.TRIAGED)
        inc = await repo.transition(inc.id, 2, S.DIAGNOSED)
        inc = await repo.transition(inc.id, 3, S.PLANNED)

        # PLANNED -> RESOLVED is illegal
        with pytest.raises(ValueError, match="Illegal incident transition"):
            await repo.transition(inc.id, 4, S.RESOLVED)

        # Advance to ESCALATED
        inc = await repo.transition(inc.id, 4, S.ESCALATED)
        assert inc.state == S.ESCALATED

        # Terminal state cannot advance
        with pytest.raises(ValueError, match="Illegal incident transition"):
            await repo.transition(inc.id, 5, S.EXECUTING)
