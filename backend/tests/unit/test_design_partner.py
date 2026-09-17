"""Unit tests for Design Partner Pilot and Real-World Benchmark API routes.

Validates zero raw PII ingestion scanning, reference benchmark evaluation,
distribution fidelity auditing, pilot compliance checklists, and commercial lead enrollment.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    """TestClient bound to main application."""
    return TestClient(app)


# ── Ingestion & Zero-PII Validation ──────────────────────────────────────────


@pytest.mark.parametrize("prefix", ["/api/v1/design-partner", "/v1/design-partner"])
def test_validate_data_ingestion_clean(client: TestClient, prefix: str) -> None:
    """Verify that clean hashed records pass Zero-PII edge scanning."""
    payload = {
        "partner_name": "Nordic Bank Pilot",
        "schema_format": "ISO_20022",
        "sample_records": [
            {
                "tx_id": "tx_001",
                "amount": 1500.0,
                "sender_hash": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
                "receiver_hash": "b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3",
                "channel": "SWIFT_GPI",
            },
            {
                "tx_id": "tx_002",
                "amount": 420.50,
                "sender_hash": "c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4",
                "receiver_hash": "d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5",
                "channel": "SEPA_INSTANT",
            },
        ],
    }
    response = client.post(f"{prefix}/validate-ingest", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["partner_name"] == "Nordic Bank Pilot"
    assert data["is_clean_zero_pii"] is True
    assert data["total_records_scanned"] == 2
    assert len(data["violations"]) == 0
    assert data["status"] == "READY_FOR_LOCAL_EDGE_TRAINING"


def test_validate_data_ingestion_with_pii_violations(client: TestClient) -> None:
    """Verify that raw credit cards, emails, and IBANs are caught and flagged."""
    payload = {
        "partner_name": "Unsanitized Bank",
        "schema_format": "CUSTOM_CSV",
        "sample_records": [
            {
                "customer_email": "john.doe@victim-bank.com",
                "card_number": "4532 1234 5678 9012",
                "phone": "+1 (555) 234-5678",
                "amount": 1000.0,
            }
        ],
    }
    response = client.post("/api/v1/design-partner/validate-ingest", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["is_clean_zero_pii"] is False
    assert data["status"] == "REMEDIATION_REQUIRED"
    assert len(data["violations"]) >= 1
    assert any("HMAC-SHA256" in v["remediation"] for v in data["violations"])


def test_validate_data_ingestion_empty_records_returns_422(client: TestClient) -> None:
    """Verify that empty sample_records returns 422 Unprocessable Entity due to min_length=1."""
    payload = {
        "partner_name": "Empty Bank",
        "schema_format": "ISO_20022",
        "sample_records": [],
    }
    response = client.post("/api/v1/design-partner/validate-ingest", json=payload)
    assert response.status_code == 422


def test_validate_data_ingestion_invalid_schema_format(client: TestClient) -> None:
    """Verify that unsupported schema format returns 422 Unprocessable Entity."""
    payload = {
        "partner_name": "Bad Schema Bank",
        "schema_format": "UNSUPPORTED_XML",
        "sample_records": [{"test": 1}],
    }
    response = client.post("/api/v1/design-partner/validate-ingest", json=payload)
    assert response.status_code == 422


# ── Benchmark Evaluation ─────────────────────────────────────────────────────


@pytest.mark.parametrize("prefix", ["/api/v1/design-partner", "/v1/design-partner"])
def test_evaluate_benchmark_paysim_success(client: TestClient, prefix: str) -> None:
    """Verify reference benchmark evaluation for PaySim dataset."""
    response = client.get(f"{prefix}/evaluate-benchmark?dataset=paysim&n_samples=2000&daily_volume=50000")
    assert response.status_code == 200
    data = response.json()
    assert data["dataset_name"] == "paysim"
    assert data["total_transactions_evaluated"] >= 2000

    perf = data["performance_comparison"]
    fl_perf = perf["federated_learning"]
    local_perf = perf["isolated_local_model"]
    adv = perf["federated_advantage"]

    assert 0.0 <= fl_perf["roc_auc"] <= 1.0
    assert 0.0 <= local_perf["roc_auc"] <= 1.0
    assert "net_daily_economic_benefit_dollars" in adv
    assert len(data["bank_partitions"]) == 3


def test_evaluate_benchmark_invalid_dataset_returns_422(client: TestClient) -> None:
    """Verify that non-whitelisted dataset parameter returns 422."""
    response = client.get("/api/v1/design-partner/evaluate-benchmark?dataset=nonexistent_data")
    assert response.status_code == 422


# ── Distribution Fidelity ───────────────────────────────────────────────────


def test_get_distribution_fidelity(client: TestClient) -> None:
    """Verify distribution fidelity audit metrics."""
    response = client.get("/api/v1/design-partner/distribution-fidelity?dataset=paysim")
    assert response.status_code == 200
    data = response.json()
    assert "wasserstein_distance" in data
    assert "ks_statistic" in data
    assert "fidelity_score" in data
    assert isinstance(data["drift_detected"], bool)


# ── Pilot Readiness Checklist ────────────────────────────────────────────────


def test_get_pilot_readiness_checklist(client: TestClient) -> None:
    """Verify institutional pilot readiness compliance checklist."""
    response = client.get(
        "/api/v1/design-partner/readiness-checklist?partner_name=Bank%20Zeta&jurisdiction=EU"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["partner_name"] == "Bank Zeta"
    assert data["jurisdiction"] == "EU"
    assert data["overall_readiness_score"] >= 90.0
    assert data["status"] == "APPROVED_FOR_PILOT"
    assert len(data["compliance_items"]) >= 4
    assert "ECDH Curve25519" in data["cryptographic_guarantees"]["aggregation_security"]


# ── Commercial Pilot Lead Enrollment ─────────────────────────────────────────


def test_pilot_lead_enrollment_and_listing(client: TestClient) -> None:
    """Verify enrolling a commercial pilot partner lead and listing registered leads."""
    lead_payload = {
        "institution_name": "Apex Federal Credit Union",
        "contact_name": "Sarah Connor",
        "contact_email": "sconnor@apexfcu.org",
        "tier": "TIER_2",
        "jurisdiction": "US",
        "monthly_tx_volume": 2500000,
        "notes": "Exploring cross-bank AML smurfing detection.",
    }
    enroll_res = client.post("/api/v1/design-partner/leads", json=lead_payload)
    assert enroll_res.status_code == 201
    enroll_data = enroll_res.json()
    assert enroll_data["institution_name"] == "Apex Federal Credit Union"
    assert enroll_data["assigned_tier"] == "TIER_2"
    assert enroll_data["sandbox_provisioned"] is True
    assert "lead_id" in enroll_data

    # Verify lead appears in listing
    list_res = client.get("/api/v1/design-partner/leads")
    assert list_res.status_code == 200
    leads = list_res.json()
    assert any(lead["institution_name"] == "Apex Federal Credit Union" for lead in leads)


def test_pilot_lead_validation_error(client: TestClient) -> None:
    """Verify invalid email format or tier triggers 422."""
    bad_lead = {
        "institution_name": "Bad Lead Bank",
        "contact_name": "Test User",
        "contact_email": "invalid-email-address",
        "tier": "UNKNOWN_TIER",
    }
    response = client.post("/api/v1/design-partner/leads", json=bad_lead)
    assert response.status_code == 422


def test_pilot_sandbox_overview(client: TestClient) -> None:
    """Verify GET /api/v1/design-partner/pilot returns sandbox status and supported formats."""
    response = client.get("/api/v1/design-partner/pilot")
    assert response.status_code == 200
    data = response.json()
    assert data["sandbox_status"] == "ACTIVE"
    assert data["active_sandboxes_provisioned"] >= 1
    assert "paysim" in data["supported_benchmarks"]
    assert "ISO_20022" in data["supported_schemas"]
    assert data["hardware_attestation_ready"] is True
