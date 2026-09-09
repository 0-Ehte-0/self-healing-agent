import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "capture_baseline", Path(__file__).resolve().parents[2] / "data/baseline/capture_baseline.py"
)
# pyrefly: ignore [bad-argument-type]
capture = importlib.util.module_from_spec(spec)
# pyrefly: ignore [missing-attribute]
spec.loader.exec_module(capture)


def healthy_sample():
    def row(value):
        return {"metric": {}, "value": [0, str(value)]}

    return {
        "up": [{"metric": {"job": job}, "value": [0, "1"]} for job in capture.REQUIRED_JOBS],
        "availability": [row(1), row(1)],
        "error_rate": [row(0)],
        "p95": [row(0.04)],
        "queue_depth": [row(0)],
        "alerts": [],
    }


def test_capture_accepts_measured_healthy_sample():
    capture.validate_sample(healthy_sample())


@pytest.mark.parametrize(
    "key,value",
    [
        ("p95", "NaN"),
        ("p95", "2"),
        ("error_rate", "0.1"),
        ("queue_depth", "50"),
        ("availability", "0"),
    ],
)
def test_capture_rejects_unhealthy_or_nonfinite_values(key, value):
    sample = healthy_sample()
    # pyrefly: ignore [unsupported-operation]
    sample[key][0]["value"][1] = value
    with pytest.raises(ValueError):
        capture.validate_sample(sample)


def test_capture_rejects_missing_targets_and_alerts():
    sample = healthy_sample()
    sample["up"].pop()
    with pytest.raises(ValueError):
        capture.validate_sample(sample)


def test_saved_baseline_is_measured_and_checksums_match():
    base = Path(__file__).resolve().parents[2] / "data/baseline"
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"] == "live_capture"
    assert manifest["duration_seconds"] >= 60
    for info in manifest["files"].values():
        content = (base / info["path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == info["sha256"]
        assert len(content) == info["size_bytes"]
    data = json.loads((base / manifest["files"]["metrics"]["path"]).read_text(encoding="utf-8"))
    assert len(data["samples"]) >= 5
    for sample in data["samples"]:
        capture.validate_sample(sample)
    sample = healthy_sample()
    sample["alerts"] = [{"metric": {"alertname": "ContainerDown"}}]
    with pytest.raises(ValueError):
        capture.validate_sample(sample)
