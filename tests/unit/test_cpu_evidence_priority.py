from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from diagnosis.evidence.collector import EvidenceCollector
from sharedmodels.enums import EvidenceKind


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("application_cpu,expected", [(0.98, 0.98), (0.0, 0.0), (None, 0.04)])
def test_container_cpu_has_priority_over_parent_process(reverse, application_cpu, expected):
    items = [
        SimpleNamespace(
            id=uuid4(),
            kind=EvidenceKind.METRICS,
            source="prometheus:cpu_rate",
            content={"latest_value": application_cpu},
        ),
        SimpleNamespace(
            id=uuid4(),
            kind=EvidenceKind.METRICS,
            source="prometheus:process_cpu",
            content={"latest_value": 0.04},
        ),
    ]
    if reverse:
        items.reverse()
    bundle = SimpleNamespace(
        resource_id=uuid4(),
        completeness={},
        freshness_seconds=0,
        collected_at=datetime.now(UTC),
        items=items,
    )
    # Conversion itself requires no network clients.
    observation = EvidenceCollector.to_observation_bundle(None, bundle)
    assert observation.normalized_cpu == expected
