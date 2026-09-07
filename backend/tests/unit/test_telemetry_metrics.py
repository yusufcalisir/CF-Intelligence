"""Unit tests for telemetry, Prometheus metrics exporter, Grafana dashboards, and Alert rules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from app.infrastructure.telemetry import (
    TelemetryRegistry,
    telemetry,
    track_fl_round,
    track_grpc_latency,
)

DEPLOYMENTS_DIR = Path(__file__).parents[3] / "deployments"
GRAFANA_DIR = DEPLOYMENTS_DIR / "grafana" / "dashboards"
PROMETHEUS_DIR = DEPLOYMENTS_DIR / "prometheus"


# ---------------------------------------------------------------------------
# 1. Telemetry & Metric Recording Tests
# ---------------------------------------------------------------------------


class TestTelemetryMetrics:
    def setup_method(self):
        self.reg = TelemetryRegistry()

    def test_record_fl_round(self):
        self.reg.record_fl_round(duration_seconds=12.5, participant_count=4)
        text = self.reg.get_prometheus_metrics_text()
        assert "cfi_fl_round_duration_seconds" in text
        assert "cfi_fl_round_participants 4.000000" in text

    def test_record_dp_epsilon(self):
        self.reg.record_dp_epsilon(bank_id="bank-001", epsilon=0.5)
        self.reg.record_dp_epsilon(bank_id="bank-001", epsilon=0.3)
        text = self.reg.get_prometheus_metrics_text()
        assert 'cfi_dp_epsilon_consumed_total{bank_id="bank-001"} 0.800000' in text

    def test_record_spectral_anomaly(self):
        self.reg.record_spectral_anomaly(bank_id="bank-002", anomaly_type="backdoor")
        text = self.reg.get_prometheus_metrics_text()
        assert (
            'cfi_spectral_anomalies_detected_total{bank_id="bank-002",anomaly_type="backdoor"} 1.000000'
            in text
        )

    def test_record_grpc_latency(self):
        self.reg.record_grpc_latency(
            method="DownloadGlobalModel", duration_seconds=0.045, status="OK"
        )
        text = self.reg.get_prometheus_metrics_text()
        assert "cfi_grpc_request_duration_seconds" in text
        assert 'method="DownloadGlobalModel"' in text

    def test_record_hsm_signing(self):
        self.reg.record_hsm_signing(key_type="RSA-4096", duration_seconds=0.015)
        text = self.reg.get_prometheus_metrics_text()
        assert 'key_type="RSA-4096"' in text

    def test_record_node_heartbeat(self):
        ts = 1700000000.0
        self.reg.record_node_heartbeat(bank_id="bank-003", timestamp=ts)
        text = self.reg.get_prometheus_metrics_text()
        assert 'cfi_node_heartbeat_timestamp{bank_id="bank-003"} 1700000000.000000' in text

    def test_get_prometheus_metrics_bytes(self):
        self.reg.record_fl_round(duration_seconds=5.0, participant_count=2)
        data_bytes = self.reg.get_prometheus_metrics_bytes()
        assert isinstance(data_bytes, bytes)
        assert b"cfi_fl_round_participants 2.000000" in data_bytes


# ---------------------------------------------------------------------------
# 2. OpenTelemetry Tracer Tests
# ---------------------------------------------------------------------------


class TestOpenTelemetryTracer:
    def test_get_tracer_returns_span_context(self):
        reg = TelemetryRegistry()
        tracer = reg.get_tracer("test-service")
        assert tracer is not None
        with tracer.start_as_current_span("test_operation") as span:
            span.set_attribute("test_key", "test_val")


# ---------------------------------------------------------------------------
# 3. Decorator Tests
# ---------------------------------------------------------------------------


class TestDecorators:
    def test_track_grpc_latency_decorator(self):
        @track_grpc_latency("TestMethod")
        def dummy_handler():
            return "ok"

        res = dummy_handler()
        assert res == "ok"
        text = telemetry.get_prometheus_metrics_text()
        assert 'method="TestMethod"' in text

    def test_track_fl_round_decorator(self):
        @track_fl_round
        def dummy_fl_round():
            return {"participants": ["bank-1", "bank-2", "bank-3"]}

        res = dummy_fl_round()
        assert len(res["participants"]) == 3
        text = telemetry.get_prometheus_metrics_text()
        assert "cfi_fl_round_duration_seconds" in text


# ---------------------------------------------------------------------------
# 4. Grafana Dashboards Schema Validation
# ---------------------------------------------------------------------------

MONITORING_GRAFANA_DIR = Path(__file__).parents[3] / "monitoring" / "grafana" / "dashboards"

DASHBOARDS_TO_TEST = [
    (GRAFANA_DIR / "fl_consortium_overview.json"),
    (GRAFANA_DIR / "privacy_security_audit.json"),
    (MONITORING_GRAFANA_DIR / "cfi_operational_dashboard.json"),
    (MONITORING_GRAFANA_DIR / "cfi_privacy_budget_dashboard.json"),
]


@pytest.mark.parametrize("path", DASHBOARDS_TO_TEST)
def test_grafana_dashboard_json_validity(path: Path):
    assert path.exists(), f"Dashboard file not found: {path}"

    content = path.read_text(encoding="utf-8")
    data = json.loads(content)

    assert "title" in data, f"Missing 'title' in {path.name}"
    assert "panels" in data, f"Missing 'panels' in {path.name}"
    assert len(data["panels"]) >= 3, f"Expected >= 3 panels in {path.name}"
    assert "schemaVersion" in data, f"Missing 'schemaVersion' in {path.name}"


# ---------------------------------------------------------------------------
# 5. Prometheus Configuration & Alert Rules Validation
# ---------------------------------------------------------------------------


def test_prometheus_yml_validity():
    path = PROMETHEUS_DIR / "prometheus.yml"
    assert path.exists()
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert "scrape_configs" in data
    assert len(data["scrape_configs"]) >= 2
    assert "rule_files" in data


def test_prometheus_alert_rules_validity():
    path = PROMETHEUS_DIR / "alert_rules.yml"
    assert path.exists()
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert "groups" in data
    groups = data["groups"]
    assert len(groups) >= 1

    rules = groups[0].get("rules", [])
    assert len(rules) >= 4, f"Expected >= 4 alert rules, found {len(rules)}"

    for rule in rules:
        assert "alert" in rule, "Rule missing 'alert' field"
        assert "expr" in rule, "Rule missing 'expr' field"
        assert "labels" in rule, "Rule missing 'labels' field"
        assert "annotations" in rule, "Rule missing 'annotations' field"
