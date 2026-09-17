"""Unit tests for Maintenance & Health CronJob API routes across dual prefixes."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

SECRET_HEADER = {"X-Cron-Secret": "cfi_cron_secret_secure_token_2026"}
BEARER_HEADER = {"Authorization": "Bearer cfi_cron_secret_secure_token_2026"}


def test_cron_unauthorized_access() -> None:
    """Verifies that all cron endpoints strictly reject unauthenticated calls with 401."""
    endpoints = [
        ("POST", "/v1/cron/cleanup-sessions"),
        ("POST", "/v1/cron/run"),
        ("GET", "/v1/cron/health-check"),
        ("GET", "/v1/cron/status"),
        ("POST", "/v1/cron/rotate-keys"),
        ("GET", "/v1/cron/schedule"),
        ("POST", "/api/v1/cron/cleanup-sessions"),
        ("GET", "/api/v1/cron/health-check"),
    ]
    for method, path in endpoints:
        if method == "POST":
            res = client.post(path)
        else:
            res = client.get(path)
        assert res.status_code == 401, f"Expected 401 for unauthenticated {method} {path}, got {res.status_code}"


def test_cron_cleanup_and_run_endpoints() -> None:
    """Verifies session cleanup and /run alias execution across dual prefixes."""
    # Test POST /v1/cron/cleanup-sessions with X-Cron-Secret
    res1 = client.post("/v1/cron/cleanup-sessions", headers=SECRET_HEADER)
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["status"] == "SUCCESS"
    assert "timestamp_iso" in data1
    assert data1["gdpr_ttl_erasure_records"] >= 0

    # Test dual prefix /api/v1/cron/cleanup-sessions with Bearer token
    res2 = client.post("/api/v1/cron/cleanup-sessions", headers=BEARER_HEADER)
    assert res2.status_code == 200

    # Test /run alias
    res_run = client.post("/v1/cron/run", headers=SECRET_HEADER)
    assert res_run.status_code == 200
    assert res_run.json()["status"] == "SUCCESS"


def test_cron_health_check_and_status_endpoints() -> None:
    """Verifies scheduled cluster health check and /status alias across dual prefixes."""
    res1 = client.get("/v1/cron/health-check", headers=BEARER_HEADER)
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["status"] == "HEALTHY"
    assert data1["database_pool_active"] is True
    assert data1["sla_compliance_pct"] == 99.95
    assert data1["disk_storage_available_mb"] > 0

    # Dual prefix /api/v1/cron/health-check
    res2 = client.get("/api/v1/cron/health-check", headers=SECRET_HEADER)
    assert res2.status_code == 200

    # Test /status alias
    res_status = client.get("/v1/cron/status", headers=SECRET_HEADER)
    assert res_status.status_code == 200
    assert res_status.json()["status"] == "HEALTHY"


def test_cron_rotate_keys_endpoint() -> None:
    """Verifies scheduled key rotation across tenant KMS vaults."""
    payload = {
        "tenant_ids": ["bank_a", "bank_b"],
        "auto_reencrypt": True,
        "revoke_retired": False,
    }
    res = client.post("/v1/cron/rotate-keys", json=payload, headers=SECRET_HEADER)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "SUCCESS"
    assert "bank_a" in data["tenants_rotated"]
    assert "bank_b" in data["tenants_rotated"]
    assert "timestamp_iso" in data


def test_cron_schedule_inspection() -> None:
    """Verifies retrieval of registered maintenance schedule definitions."""
    res = client.get("/v1/cron/schedule", headers=SECRET_HEADER)
    assert res.status_code == 200
    data = res.json()
    assert data["timezone"] == "UTC"
    assert len(data["tasks"]) >= 4

    job_names = [t["job_name"] for t in data["tasks"]]
    assert "cleanup-sessions" in job_names
    assert "health-check" in job_names
    assert "rotate-keys" in job_names
    assert "gdpr-ttl-purge" in job_names
