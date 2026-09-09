from pathlib import Path

import pytest
import yaml

ALERTS_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "infrastructure"
    / "monitoring"
    / "prometheus"
    / "alerts"
)


def test_alerts_directory_exists():
    assert ALERTS_DIR.is_dir(), f"Alerts directory does not exist at {ALERTS_DIR}"


def test_alert_files_valid_yaml():
    alert_files = list(ALERTS_DIR.glob("*.yml"))
    assert len(alert_files) >= 3, f"Expected at least 3 alert files, found {len(alert_files)}"

    for file_path in alert_files:
        with file_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
            assert isinstance(data, dict), f"{file_path.name} is not a valid YAML mapping"
            assert "groups" in data, f"{file_path.name} missing 'groups' key"
            assert isinstance(data["groups"], list), f"{file_path.name} 'groups' is not a list"


def test_alert_rule_structure():
    alert_files = list(ALERTS_DIR.glob("*.yml"))
    all_alerts = {}

    for file_path in alert_files:
        with file_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
            for group in data.get("groups", []):
                assert "name" in group, f"Group in {file_path.name} missing 'name'"
                assert "rules" in group, f"Group {group['name']} missing 'rules'"
                for rule in group["rules"]:
                    assert "alert" in rule, f"Rule in {group['name']} missing 'alert' name"
                    assert "expr" in rule, f"Alert {rule['alert']} missing 'expr'"
                    assert "for" in rule, (
                        f"Alert {rule['alert']} missing 'for' stabilization window"
                    )
                    assert "labels" in rule, f"Alert {rule['alert']} missing 'labels'"
                    assert "severity" in rule["labels"], (
                        f"Alert {rule['alert']} missing 'severity' label"
                    )
                    assert "annotations" in rule, f"Alert {rule['alert']} missing 'annotations'"
                    assert "summary" in rule["annotations"], (
                        f"Alert {rule['alert']} missing summary annotation"
                    )
                    assert "description" in rule["annotations"], (
                        f"Alert {rule['alert']} missing description annotation"
                    )

                    alert_name = rule["alert"]
                    all_alerts[alert_name] = rule
    assert len(all_alerts) > 0


def test_mvp_frozen_scenarios_covered():
    """Verify that all 3 frozen MVP scenarios are directly covered with explicit alert rules."""
    alert_files = list(ALERTS_DIR.glob("*.yml"))
    scenario_coverage = {}

    for file_path in alert_files:
        with file_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
            for group in data.get("groups", []):
                for rule in group["rules"]:
                    scenario_id = rule.get("labels", {}).get("scenario_id")
                    if scenario_id:
                        scenario_coverage.setdefault(scenario_id, []).append(rule["alert"])

    # MVP Must-Have Scenarios (SCN-001, SCN-002, SCN-003)
    assert "SCN-001" in scenario_coverage, "SCN-001 (Container Crash) missing alert coverage"
    assert "ContainerDown" in scenario_coverage["SCN-001"]

    assert "SCN-002" in scenario_coverage, "SCN-002 (CPU Saturation) missing alert coverage"
    assert "HighCpuSaturation" in scenario_coverage["SCN-002"]

    assert "SCN-003" in scenario_coverage, "SCN-003 (Unresponsive API) missing alert coverage"
    assert "ApiUnresponsive" in scenario_coverage["SCN-003"]

    # Other fault injector scenarios coverage check
    for scn_id in ["SCN-004", "SCN-005", "SCN-006", "SCN-007", "SCN-008", "SCN-009", "SCN-010"]:
        assert scn_id in scenario_coverage, f"{scn_id} missing alert coverage in Prometheus rules"
