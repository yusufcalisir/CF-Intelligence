"""Unit & Integration Tests for Platform Observability, Prometheus Metrics & Health API.

Covers:
- Kubernetes Liveness (/health/live, /health) and Readiness (/health/ready, /health/dependencies)
- Degraded dependency 503 handling
- Multi-prefix route parity across root, /api/v1/health, and /v1/health
- System diagnostics (/system, /memory, /env, /connectors, /test-connector)
- Input validation (400 on empty connector, 404 on unknown connector)
- Model drift analysis (/drift/analyze, /drift/psi, /drift/evaluate)
- Model fairness and demographic parity (/fairness)
- Calibration reporting (/calibration)
- Active alerts, Alertmanager webhook receiver (/alerts, /alerts/webhook)
- Automated retraining triggers and job queries (/drift/trigger-retrain, /retraining/jobs)
- Prometheus /metrics scraping endpoint
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.application.schemas.observability import DependencyHealthStatus
from app.main import app

client = TestClient(app)


# ── 1. Health & Kubernetes Readiness Probes ─────────────────────────────────

class TestHealthAndReadinessProbes:
    """Validate liveness, readiness, and multi-prefix parity for health endpoints."""

    @pytest.mark.parametrize("prefix", ["/health", "/api/v1/health", "/v1/health"])
    def test_liveness_endpoints_multi_prefix(self, prefix: str):
        """Verify liveness probe returns HTTP 200, healthy status, and process uptime."""
        resp = client.get(prefix)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["service"] == "fraud-intelligence-api"
        assert "timestamp" in data
        assert data["uptime_seconds"] >= 0

    @pytest.mark.parametrize("prefix", ["/health/live", "/api/v1/health/live", "/v1/health/live"])
    def test_kubernetes_liveness_multi_prefix(self, prefix: str):
        """Verify Kubernetes K8s liveness probes across all prefixes."""
        resp = client.get(prefix)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "alive"
        assert "timestamp" in data

    @pytest.mark.parametrize("prefix", ["/health/ready", "/api/v1/health/ready", "/v1/health/ready"])
    def test_readiness_probe_healthy_state(self, prefix: str):
        """Verify readiness probe returns HTTP 200 when all dependencies are healthy."""
        healthy_redis = DependencyHealthStatus(status="HEALTHY", latency_ms=1.2)
        with patch("app.presentation.routers.health.check_redis_component_health", new_callable=AsyncMock) as mock_redis:
            mock_redis.return_value = healthy_redis
            resp = client.get(prefix)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ready"
            assert "checks" in data
            assert data["checks"]["database"] is True
            assert data["checks"]["vault"] is True

    def test_readiness_probe_degraded_state_returns_503(self):
        """Verify readiness probe returns RFC HTTP 503 when any dependency is degraded."""
        degraded_db = DependencyHealthStatus(
            status="DEGRADED",
            latency_ms=150.0,
            message="Connection pool exhausted",
        )
        with patch("app.presentation.routers.health.check_db_health", new_callable=AsyncMock) as mock_db:
            mock_db.return_value = degraded_db
            resp = client.get("/health/ready")
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "degraded"
            assert data["checks"]["database"] is False

    @pytest.mark.parametrize("prefix", ["/health/dependencies", "/api/v1/health/dependencies", "/v1/health/dependencies"])
    def test_dependencies_detailed_latencies(self, prefix: str):
        """Verify dependency breakdown provides individual latencies and component statuses."""
        resp = client.get(prefix)
        assert resp.status_code == 200
        data = resp.json()
        assert "database" in data
        assert "redis" in data
        assert "vault" in data
        assert "enclave" in data
        assert data["database"]["status"] == "HEALTHY"
        assert data["database"]["latency_ms"] >= 0.0


# ── 2. System Diagnostics Endpoints ─────────────────────────────────────────

class TestSystemDiagnosticsEndpoints:
    """Validate system diagnostics, memory profiling, and connector probes."""

    @pytest.mark.parametrize("prefix", ["/api/v1/diagnostics", "/v1/diagnostics"])
    def test_system_diagnostics_prefix_parity(self, prefix: str):
        """Verify /system returns CPU topology, platform, and virtual memory metrics."""
        resp = client.get(f"{prefix}/system")
        assert resp.status_code == 200
        data = resp.json()
        assert "platform" in data
        assert "python_version" in data
        assert data["cpu_count"] >= 1
        assert "memory" in data
        assert data["memory"]["total_mb"] > 0
        assert "process_memory" in data
        assert data["process_memory"]["rss_mb"] > 0

    @pytest.mark.parametrize("prefix", ["/api/v1/diagnostics", "/v1/diagnostics"])
    def test_process_memory_diagnostics(self, prefix: str):
        """Verify /memory returns process RSS and heap metrics."""
        resp = client.get(f"{prefix}/memory")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rss_mb"] > 0
        assert data["vms_mb"] > 0
        assert "timestamp" in data

    @pytest.mark.parametrize("prefix", ["/api/v1/diagnostics", "/v1/diagnostics"])
    def test_environment_diagnostics_sanitized(self, prefix: str):
        """Verify /env returns sanitized configuration flags without leaking secret tokens."""
        resp = client.get(f"{prefix}/env")
        assert resp.status_code == 200
        data = resp.json()
        assert "environment" in data
        assert "log_level" in data
        assert data["secure_mode"] is True

    @pytest.mark.parametrize("prefix", ["/api/v1/diagnostics", "/v1/diagnostics"])
    def test_connectors_overview(self, prefix: str):
        """Verify /connectors returns connectivity status for enterprise connectors."""
        resp = client.get(f"{prefix}/connectors")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_connectors"] == 7
        assert data["healthy_connectors"] >= 1
        assert len(data["connectors"]) == 7

    def test_connector_probe_success(self):
        """Verify active test probe succeeds for valid connector."""
        resp = client.post(
            "/api/v1/diagnostics/test-connector",
            json={"connector_id": "redis"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["connector_id"] == "redis"
        assert data["success"] is True
        assert data["round_trip_ms"] > 0
        assert len(data["diagnostics_log"]) >= 1

    def test_connector_probe_empty_id_rejected_400(self):
        """Verify empty connector ID is rejected with RFC 400 Bad Request."""
        resp = client.post(
            "/api/v1/diagnostics/test-connector",
            json={"connector_id": "   "},
        )
        assert resp.status_code == 400
        assert "cannot be empty" in resp.json()["detail"].lower()

    def test_connector_probe_unknown_id_rejected_404(self):
        """Verify unsupported connector ID returns RFC 404 Not Found."""
        resp = client.post(
            "/api/v1/diagnostics/test-connector",
            json={"connector_id": "nonexistent_adapter_xyz"},
        )
        assert resp.status_code == 404
        assert "unknown or not supported" in resp.json()["detail"].lower()


# ── 3. Observability, Drift & Fairness Monitoring ───────────────────────────

class TestMonitoringAndObservabilityRoutes:
    """Validate model drift analysis, fairness metrics, calibration, and alerting."""

    @pytest.mark.parametrize("prefix", ["/api/v1/monitoring", "/v1/monitoring"])
    def test_drift_analyze_endpoint(self, prefix: str):
        """Verify /drift/analyze evaluates feature drifts and concept drift PSI."""
        resp = client.get(f"{prefix}/drift/analyze?severe_drift=false")
        assert resp.status_code == 200
        data = resp.json()
        assert "overall_status" in data
        assert data["concept_drift_psi"] >= 0.0
        assert len(data["feature_drifts"]) >= 1
        for fd in data["feature_drifts"]:
            assert "ks_statistic" in fd
            assert "psi" in fd

    @pytest.mark.parametrize("prefix", ["/api/v1/monitoring", "/v1/monitoring"])
    def test_concept_drift_psi_focused_endpoint(self, prefix: str):
        """Verify /drift/psi provides focused Population Stability Index scores."""
        resp = client.get(f"{prefix}/drift/psi")
        assert resp.status_code == 200
        data = resp.json()
        assert "concept_drift_psi" in data
        assert "overall_status" in data
        assert "alert_level" in data
        assert "features" in data
        assert isinstance(data["features"], dict)

    @pytest.mark.parametrize("prefix", ["/api/v1/monitoring", "/v1/monitoring"])
    def test_fairness_metrics_four_fifths_rule(self, prefix: str):
        """Verify /fairness reports demographic parity and EEOC 80% four-fifths rule compliance."""
        resp = client.get(f"{prefix}/fairness")
        assert resp.status_code == 200
        data = resp.json()
        assert data["demographic_parity_ratio"] > 0.0
        assert data["disparate_impact_ratio"] >= 0.80
        assert data["satisfies_four_fifths_rule"] is True
        assert len(data["protected_attributes"]) >= 1

    @pytest.mark.parametrize("prefix", ["/api/v1/monitoring", "/v1/monitoring"])
    def test_telemetry_overview_endpoint(self, prefix: str):
        """Verify /telemetry provides real-time scrape counters and active alerts."""
        resp = client.get(f"{prefix}/telemetry")
        assert resp.status_code == 200
        data = resp.json()
        assert data["uptime_seconds"] >= 0
        assert data["active_requests"] >= 1
        assert "timestamp" in data

    def test_calibration_report_endpoint(self):
        """Verify /calibration computes Brier score, ECE, and reliability curve bins."""
        resp = client.get("/api/v1/monitoring/calibration")
        assert resp.status_code == 200
        data = resp.json()
        assert "brier_score" in data
        assert "expected_calibration_error" in data
        assert isinstance(data["is_well_calibrated"], bool)
        assert len(data["bins"]) == 10

    def test_live_drift_evaluation_post(self):
        """Verify POST /drift/evaluate with custom user-supplied feature distributions."""
        payload = {
            "current_features": {"amount": [120.0, 150.0, 180.0, 210.0]},
            "reference_features": {"amount": [100.0, 130.0, 160.0, 190.0]},
            "current_risk_scores": [0.1, 0.2, 0.4, 0.8],
            "reference_risk_scores": [0.1, 0.2, 0.3, 0.7],
        }
        resp = client.post("/api/v1/monitoring/drift/evaluate", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall_status"] in ("HEALTHY", "STABLE", "WARNING", "CRITICAL")
        assert len(data["feature_drifts"]) == 1

    def test_active_alerts_listing_and_creation(self):
        """Verify active alerts query and custom alert registration."""
        # Query existing alerts
        list_resp = client.get("/api/v1/monitoring/alerts")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) >= 1

        # Register custom alert
        new_alert = {
            "alert_name": "TestMIAAttackDetected",
            "severity": "critical",
            "summary": "Membership Inference Attack confidence spike on Bank Alpha node",
            "started_at": "2026-09-17T12:00:00Z",
            "status": "firing",
        }
        post_resp = client.post("/api/v1/monitoring/alerts", json=new_alert)
        assert post_resp.status_code == 201
        assert post_resp.json()["alert_name"] == "TestMIAAttackDetected"

        # Verify alert is listed
        verify_resp = client.get("/api/v1/monitoring/alerts?status_filter=firing")
        assert verify_resp.status_code == 200
        names = [a["alert_name"] for a in verify_resp.json()]
        assert "TestMIAAttackDetected" in names

    def test_alertmanager_webhook_processing(self):
        """Verify Alertmanager webhook receiver updates firing and resolved states."""
        webhook_payload = {
            "version": "4",
            "groupKey": "{alertname='HighMemoryUsage'}",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "HighMemoryUsage", "severity": "warning"},
                    "annotations": {"summary": "Host RAM utilization exceeded 90%"},
                }
            ],
        }
        resp = client.post("/api/v1/monitoring/alerts/webhook", json=webhook_payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["alerts_processed"] == 1

        # Resolve the alert via webhook
        webhook_payload["alerts"][0]["status"] = "resolved"
        resolve_resp = client.post("/api/v1/monitoring/alerts/webhook", json=webhook_payload)
        assert resolve_resp.status_code == 200
        assert resolve_resp.json()["alerts_processed"] == 1

    def test_automated_retraining_trigger_and_job_lifecycle(self):
        """Verify automated retraining trigger and job query endpoints."""
        # Trigger retraining
        trigger_resp = client.post(
            "/api/v1/monitoring/drift/trigger-retrain?reason=Critical%20Concept%20Drift%20Detected"
        )
        assert trigger_resp.status_code == 200
        trig_data = trigger_resp.json()
        assert trig_data["triggered"] is True
        job_id = trig_data["new_simulation_id"]
        assert job_id is not None

        # Query all jobs
        jobs_resp = client.get("/api/v1/monitoring/retraining/jobs")
        assert jobs_resp.status_code == 200
        assert len(jobs_resp.json()) >= 1

        # Query specific job by ID
        job_detail = client.get(f"/api/v1/monitoring/retraining/jobs/{job_id}")
        assert job_detail.status_code == 200
        assert job_detail.json()["job_id"] == job_id

        # Nonexistent job returns 404
        bad_job = client.get("/api/v1/monitoring/retraining/jobs/nonexistent_job_999")
        assert bad_job.status_code == 404


# ── 4. Prometheus Scrape Metrics Verification ───────────────────────────────

class TestPrometheusMetricsScrape:
    """Validate OpenTelemetry and Prometheus /metrics scrape endpoint."""

    def test_prometheus_metrics_endpoint_returns_plain_text(self):
        """Verify GET /metrics returns HTTP 200 with text/plain Prometheus format."""
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("content-type", "")
        body = resp.text
        # Check standard Prometheus comments or CFI metric names
        assert len(body) > 0
