"""Regenerate the version-controlled public JSON schemas."""

import json
from pathlib import Path

from sharedmodels.execution import ExecutionContract
from sharedmodels.incident import IncidentContract
from sharedmodels.policy import PolicyContract

root = Path(__file__).resolve().parents[1]
(root / "contracts").mkdir(exist_ok=True)
for model in (IncidentContract, ExecutionContract, PolicyContract):
    (root / "contracts" / f"{model.__name__}.json").write_text(
        json.dumps(model.model_json_schema(), indent=2) + "\n", encoding="utf-8"
    )
