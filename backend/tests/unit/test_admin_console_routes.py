"""Unit tests for Admin Web Console API routes across all multi-prefix mounts."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_dashboard_summary_multi_prefix() -> None:
    """Test GET summary metrics across /admin/dashboard and /admin endpoints."""
    prefixes = [
        "/v1/admin/dashboard/summary",
        "/api/v1/admin/dashboard/summary",
        "/api/v1/admin/summary",
        "/v1/admin/summary",
    ]
    for path in prefixes:
        res = client.get(path)
        assert res.status_code == 200, f"Failed at path: {path}"
        data = res.json()
        assert data["active_bank_nodes_count"] >= 3
        assert data["federated_rounds_completed"] >= 20
        assert data["global_model_auc"] > 0.80
        assert data["sla_compliance_pct"] >= 99.0


def test_role_configuration_multi_role() -> None:
    """Test role configuration filtering for all enterprise personas."""
    roles = ["EXECUTIVE", "COMPLIANCE_OFFICER", "ML_ENGINEER", "FRAUD_INVESTIGATOR"]
    for role in roles:
        res = client.get("/api/v1/admin/role-config", params={"role": role})
        assert res.status_code == 200
        data = res.json()
        assert data["role"] == role
        assert len(data["visible_widgets"]) >= 1
        assert len(data["permissions"]) >= 1

    # Check dashboard path parity
    res_dash = client.get("/v1/admin/dashboard/role-config", params={"role": "COMPLIANCE_OFFICER"})
    assert res_dash.status_code == 200
    assert "sar_filings" in res_dash.json()["visible_widgets"]


def test_platform_config_endpoint() -> None:
    """Test GET /api/v1/admin/config returns platform parameters and security posture."""
    res = client.get("/api/v1/admin/config")
    assert res.status_code == 200
    data = res.json()
    assert "Collaborative Fraud Intelligence" in data["platform_title"]
    assert data["default_dp_epsilon"] == 1.0
    assert data["default_dp_delta"] == 1e-5
    assert "Curve25519" in data["secagg_protocol"]
    assert data["max_active_tenants"] == 16


def test_tenant_partitions_endpoint() -> None:
    """Test GET /api/v1/admin/tenants returns multi-tenant bank partition metadata."""
    res = client.get("/api/v1/admin/tenants")
    assert res.status_code == 200
    data = res.json()
    assert data["total_tenants"] >= 3
    assert data["active_tenants"] >= 3

    tenant_ids = [p["tenant_id"] for p in data["partitions"]]
    assert "bank_a" in tenant_ids
    assert "bank_b" in tenant_ids
    assert "bank_c" in tenant_ids

    # Verify schema isolation naming
    for partition in data["partitions"]:
        assert partition["schema_name"].startswith("tenant_")
        assert partition["kms_vault_path"].startswith("transit/keys/")


def test_admin_maintenance_endpoint() -> None:
    """Test GET /api/v1/admin/maintenance returns subsystem health and storage metrics."""
    res = client.get("/api/v1/admin/maintenance")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "OPERATIONAL"
    assert data["database_pool_status"] == "HEALTHY"
    assert data["redis_cache_status"] == "CONNECTED"
    assert data["vault_transit_status"] == "SEALED_OK"
    assert data["disk_free_mb"] > 0.0
    assert data["background_worker_count"] >= 1
