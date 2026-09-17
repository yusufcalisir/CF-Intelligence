"""Unit tests for Design Partner Pilot and Sandbox Benchmark API routes across dual prefixes."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_validate_data_ingestion_clean_and_dirty() -> None:
    """Test POST /validate-ingest with clean and PII-tainted data across dual prefixes."""
    clean_payload = {
        "partner_name": "Nordic Commercial Bank",
        "schema_format": "ISO_20022",
        "sample_records": [
            {"tx_id": "tx_001", "amount": 1250.0, "account_hash": "a1b2c3d4e5f6"},
            {"tx_id": "tx_002", "amount": 4900.0, "account_hash": "b2c3d4e5f6a1"},
        ],
    }

    # Test /api/v1/design-partner/validate-ingest
    res_clean = client.post("/api/v1/design-partner/validate-ingest", json=clean_payload)
    assert res_clean.status_code == 200
    data_clean = res_clean.json()
    assert data_clean["is_clean_zero_pii"] is True
    assert data_clean["status"] == "READY_FOR_LOCAL_EDGE_TRAINING"
    assert len(data_clean["violations"]) == 0

    # Test dual prefix /v1/design-partner/validate-ingest
    res_v1 = client.post("/v1/design-partner/validate-ingest", json=clean_payload)
    assert res_v1.status_code == 200

    # Test PII tainted records
    dirty_payload = {
        "partner_name": "Insecure Fintech Corp",
        "schema_format": "CUSTOM_CSV",
        "sample_records": [
            {
                "tx_id": "tx_101",
                "customer_email": "leaked_user@bank.com",
                "credit_card": "4532-1234-5678-9012",
            }
        ],
    }
    res_dirty = client.post("/api/v1/design-partner/validate-ingest", json=dirty_payload)
    assert res_dirty.status_code == 200
    data_dirty = res_dirty.json()
    assert data_dirty["is_clean_zero_pii"] is False
    assert data_dirty["status"] == "REMEDIATION_REQUIRED"
    assert len(data_dirty["violations"]) >= 1


def test_validate_ingest_empty_records_rejected() -> None:
    """Test POST /validate-ingest rejects empty sample records with 422."""
    res = client.post(
        "/api/v1/design-partner/validate-ingest",
        json={"partner_name": "Test Bank", "sample_records": []},
    )
    assert res.status_code == 422


def test_evaluate_benchmark_endpoint() -> None:
    """Test GET /evaluate-benchmark computes calibrated comparison metrics."""
    res = client.get(
        "/api/v1/design-partner/evaluate-benchmark",
        params={"dataset": "paysim", "n_samples": 1500, "daily_volume": 50000},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["dataset_name"] == "paysim"
    assert data["total_samples"] == 1500
    assert "performance_comparison" in data
    assert "distribution_fidelity" in data


def test_distribution_fidelity_endpoint() -> None:
    """Test GET /distribution-fidelity returns statistical degradation metrics."""
    res = client.get(
        "/api/v1/design-partner/distribution-fidelity",
        params={"dataset": "paysim"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["dataset_name"] == "paysim"
    assert "degradation_metrics" in data


def test_readiness_checklist_endpoint() -> None:
    """Test GET /readiness-checklist generates institutional compliance report."""
    res = client.get(
        "/api/v1/design-partner/readiness-checklist",
        params={"partner_name": "Alpine Private Bank", "jurisdiction": "EU"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["partner_name"] == "Alpine Private Bank"
    assert data["overall_readiness_score"] >= 90.0
    assert data["status"] == "APPROVED_FOR_PILOT"
    assert len(data["compliance_items"]) >= 5
    assert "aggregation_security" in data["cryptographic_guarantees"]


def test_partner_leads_registration() -> None:
    """Test POST /leads registers an institution inquiry and assigns consortium tier."""
    lead_payload = {
        "partner_name": "Mediterranean Commercial Bank",
        "contact_email": "pilot@medbank.com",
        "jurisdiction": "TR",
        "institution_type": "COMMERCIAL_BANK",
        "daily_volume": 600000,
    }
    res = client.post("/api/v1/design-partner/leads", json=lead_payload)
    assert res.status_code == 201
    data = res.json()
    assert data["lead_id"].startswith("lead_")
    assert data["partner_name"] == "Mediterranean Commercial Bank"
    assert data["assigned_tier"] == "TIER_1_GLOBAL"
    assert "/onboarding?lead_id=" in data["onboarding_wizard_url"]

    # Dual prefix
    res_v1 = client.post(
        "/v1/design-partner/leads",
        json={**lead_payload, "partner_name": "Regional Credit Union", "daily_volume": 50000},
    )
    assert res_v1.status_code == 201
    assert res_v1.json()["assigned_tier"] == "TIER_2_REGIONAL"


def test_pilot_status_endpoint() -> None:
    """Test GET /pilot returns active sandbox capabilities."""
    res = client.get("/api/v1/design-partner/pilot", params={"partner_name": "Test Partner"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ACTIVE_SANDBOX"
    assert "ISO_20022" in data["supported_schemas"]
    assert "paysim" in data["benchmarks_available"]


def test_pilot_feedback_submission() -> None:
    """Test POST /feedback records qualitative institutional feedback."""
    feedback_payload = {
        "partner_name": "Global Horizon Bank",
        "contact_email": "ciso@globalhorizon.com",
        "satisfaction_rating": 5,
        "category": "ACCURACY_VS_PRIVACY",
        "comments": "The Zero-Raw-PII scanner and Rényi DP guarantees satisfied our internal data committee.",
    }
    res = client.post("/api/v1/design-partner/feedback", json=feedback_payload)
    assert res.status_code == 201
    data = res.json()
    assert data["feedback_id"].startswith("fb_")
    assert data["status"] == "ACKNOWLEDGED"
    assert data["partner_name"] == "Global Horizon Bank"
