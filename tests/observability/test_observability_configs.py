from pathlib import Path

import pytest
import yaml

MONITORING_DIR = Path(__file__).resolve().parent.parent.parent / "infrastructure" / "monitoring"


def test_prometheus_config():
    prom_file = MONITORING_DIR / "prometheus" / "prometheus.yml"
    assert prom_file.is_file(), f"prometheus.yml not found at {prom_file}"

    with prom_file.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)

    assert config.get("global", {}).get("scrape_interval") == "15s"
    assert config.get("global", {}).get("evaluation_interval") == "15s"

    job_names = [job["job_name"] for job in config.get("scrape_configs", [])]
    expected_jobs = [
        "prometheus",
        "demo-api",
        "demo-worker",
        "control-plane",
        "node-exporter",
        "cAdvisor",
    ]
    for expected in expected_jobs:
        assert expected in job_names, (
            f"Expected scrape job '{expected}' not found in prometheus.yml"
        )

    # Alertmanager targets verification
    am_configs = config.get("alerting", {}).get("alertmanagers", [])
    assert len(am_configs) > 0, "No alertmanager configurations found in prometheus.yml"
    targets = []
    for am in am_configs:
        for sc in am.get("static_configs", []):
            targets.extend(sc.get("targets", []))
    assert any("alertmanager:9093" in t for t in targets), "alertmanager:9093 target not found"


def test_alertmanager_config():
    am_file = MONITORING_DIR / "alertmanager" / "alertmanager.yml"
    assert am_file.is_file(), f"alertmanager.yml not found at {am_file}"

    with am_file.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)

    receivers = config.get("receivers", [])
    assert len(receivers) > 0, "No receivers configured in alertmanager.yml"

    webhook_urls = []
    for r in receivers:
        for wh in r.get("webhook_configs", []):
            webhook_urls.append(wh.get("url"))

    assert any("control-plane" in url for url in webhook_urls if url), (
        "Webhook URL pointing to control-plane not found"
    )


def test_loki_config():
    loki_file = MONITORING_DIR / "loki" / "config.yaml"
    assert loki_file.is_file(), f"Loki config not found at {loki_file}"

    with loki_file.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)

    assert config.get("server", {}).get("http_listen_port") == 3100
    assert config.get("auth_enabled") is False
    assert "schema_config" in config


def test_fluentbit_config():
    fb_conf = MONITORING_DIR / "fluentbit" / "fluent-bit.conf"
    fb_parsers = MONITORING_DIR / "fluentbit" / "parsers.conf"

    assert fb_conf.is_file(), f"fluent-bit.conf not found at {fb_conf}"
    assert fb_parsers.is_file(), f"parsers.conf not found at {fb_parsers}"

    conf_text = fb_conf.read_text(encoding="utf-8")
    assert "[SERVICE]" in conf_text
    assert "[INPUT]" in conf_text
    assert "[OUTPUT]" in conf_text
    assert "loki" in conf_text

    parsers_text = fb_parsers.read_text(encoding="utf-8")
    assert "[PARSER]" in parsers_text
    assert "Name        json" in parsers_text


def test_otel_collector_config():
    otel_file = MONITORING_DIR / "otel-collector" / "config.yaml"
    assert otel_file.is_file(), f"OTel Collector config not found at {otel_file}"

    with otel_file.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)

    receivers = config.get("receivers", {})
    assert "otlp" in receivers
    assert receivers["otlp"]["protocols"]["grpc"]["endpoint"] == "0.0.0.0:4317"

    exporters = config.get("exporters", {})
    assert any("jaeger" in k or "otlp" in k for k in exporters)

    pipelines = config.get("service", {}).get("pipelines", {})
    assert "traces" in pipelines


def test_grafana_provisioning():
    ds_file = MONITORING_DIR / "grafana" / "provisioning" / "datasources" / "datasources.yaml"
    dash_prov_file = MONITORING_DIR / "grafana" / "provisioning" / "dashboards" / "dashboards.yaml"

    assert ds_file.is_file()
    assert dash_prov_file.is_file()

    with ds_file.open(encoding="utf-8") as f:
        ds_config = yaml.safe_load(f)

    ds_names = [ds["name"] for ds in ds_config.get("datasources", [])]
    assert "Prometheus" in ds_names
    assert "Loki" in ds_names
    assert "Jaeger" in ds_names
