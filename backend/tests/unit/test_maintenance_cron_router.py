"""Unit tests for Maintenance & Health CronJob API router."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_cron_cleanup_sessions_unauthorized_without_secret() -> None:
    """Verifies that cleanup endpoint rejects requests without secret header."""
    response = client.post("/v1/cron/cleanup-sessions")
    assert response.status_code == 401
    assert "Invalid or missing Cron authorization secret" in response.json()["detail"]


def test_cron_cleanup_sessions_success() -> None:
    """Verifies successful session & artifact cleanup when authorized."""
    headers = {"X-Cron-Secret": "cfi_cron_secret_secure_token_2026"}
    response = client.post("/v1/cron/cleanup-sessions", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCESS"
    assert data["expired_sessions_purged"] >= 0
    assert "timestamp_iso" in data


def test_cron_health_check_unauthorized_without_secret() -> None:
    """Verifies that health check endpoint rejects unauthorized requests."""
    response = client.get("/v1/cron/health-check")
    assert response.status_code == 401


def test_cron_health_check_success() -> None:
    """Verifies successful system health check response when authorized."""
    headers = {"Authorization": "Bearer cfi_cron_secret_secure_token_2026"}
    response = client.get("/v1/cron/health-check", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "HEALTHY"
    assert data["database_pool_active"] is True
    assert data["sla_compliance_pct"] == 99.95
