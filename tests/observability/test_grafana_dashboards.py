import json
from pathlib import Path

import pytest

DASHBOARDS_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "infrastructure"
    / "monitoring"
    / "grafana"
    / "dashboards"
)


def test_dashboards_exist():
    expected = ["api-health.json", "resources.json", "incident-demo.json"]
    for filename in expected:
        path = DASHBOARDS_DIR / filename
        assert path.is_file(), f"Dashboard {filename} missing at {path}"


def test_dashboards_valid_json_and_schema():
    dashboards = list(DASHBOARDS_DIR.glob("*.json"))
    assert len(dashboards) >= 3

    uids = set()
    for dash_file in dashboards:
        with dash_file.open(encoding="utf-8") as f:
            data = json.load(f)

        assert "title" in data, f"{dash_file.name} missing 'title'"
        assert "uid" in data, f"{dash_file.name} missing 'uid'"
        assert data["uid"] not in uids, f"Duplicate UID '{data['uid']}' in {dash_file.name}"
        uids.add(data["uid"])

        assert "panels" in data, f"{dash_file.name} missing 'panels'"
        assert isinstance(data["panels"], list), f"{dash_file.name} 'panels' must be a list"
        assert len(data["panels"]) > 0, f"{dash_file.name} has no panels"

        for panel in data["panels"]:
            assert "type" in panel, f"Panel in {dash_file.name} missing 'type'"
            assert "title" in panel, f"Panel in {dash_file.name} missing 'title'"
            if panel["type"] != "row":
                assert "targets" in panel or "options" in panel, (
                    f"Panel {panel.get('title')} in {dash_file.name} missing targets"
                )
